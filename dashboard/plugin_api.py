"""Owners snapshot API. Native storage adapter, no workflow writes or core imports.

Storage contracts inspected at hermes-agent ee8dd6c886. The host provides auth;
explicit profile selection is resolved here because the public SDK does not expose
Python's context-local home resolver. No request may supply a filesystem path.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

router = APIRouter()
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
SCAN_LIMIT = 1000
DETAIL_LIMIT = 50
STATE_TTL = 120
MAX_FILE_BYTES = 1024 * 1024


def _read(path: Path, *, yaml=False):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("source too large")
    text = path.read_text(encoding="utf-8")
    value = YAML(typ="safe").load(text) if yaml else json.loads(text)
    if not isinstance(value, dict):
        raise TypeError("expected mapping")
    return value


def _root_home():
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
    # A custom home remains isolated even when stored beneath ~/.hermes (e.g.
    # local fixtures). Only the direct profiles/<id> layout shares its parent.
    root = home.parent.parent if home.parent.name == "profiles" else home
    return root, home


def _profile_home(root, name):
    if not isinstance(name, str) or not PROFILE_ID.fullmatch(name):
        raise HTTPException(400, "Invalid profile identifier")
    path = root if name == "default" else root / "profiles" / name
    if not path.resolve().is_relative_to(root):
        raise HTTPException(400, "Profile path escapes Hermes root")
    return path


def _timestamp(value):
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
            number = dt.timestamp()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
        else:
            return None
        return number if math.isfinite(number) else None
    except (ValueError, OverflowError):
        return None


def _iso(value):
    stamp = _timestamp(value)
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _strings(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("expected text list")
    return [v[:500] for v in value[:50]]


def _expand_setting(value: str) -> str:
    """Match native ${VAR}/${env:VAR} expansion; preserve unresolved refs.

    Only the configured setting is expanded, never charter content. No private
    Hermes config/secret resolver is imported by this public dashboard adapter.
    """
    def replace(match: re.Match[str]) -> str:
        ref = match[1].strip()
        if ref.startswith("env:"):
            name = ref[4:].strip()
        elif re.match(r"^[a-z][a-z0-9_-]*:", ref):
            return match[0]
        else:
            name = ref
        return os.environ.get(name, match[0]) if name else match[0]

    return re.sub(r"\${([^}]+)}", replace, value)


def _charter(home, root):
    """Match Owners get_fleet_path at ee8dd6c886's PluginContext settings API.

    Profile home selects settings, not default charter candidates. Relative
    configured paths use this process's CWD, just as the native plugin does.
    """
    configured = None
    config = home / "config.yaml"
    if config.exists():
        cfg = _read(config, yaml=True)
        plugins = cfg.get("plugins", {})
        entries = plugins.get("entries", {})
        owner = entries.get("owners", {})
        settings = owner.get("settings", {})
        legacy = owner.get("config", {})
        configured = (settings["fleet_path"] if isinstance(settings, dict) and "fleet_path" in settings
                      else legacy.get("fleet_path") if isinstance(legacy, dict) else None)
        if configured is not None and not isinstance(configured, str):
            raise ValueError("invalid fleet_path setting")
        if isinstance(configured, str):
            configured = _expand_setting(configured)
    if configured:
        path = Path(configured).expanduser()
    else:
        candidates = [Path.home() / ".hermes" / "fleet.yaml", Path(__file__).parent.parent / "fleet.yaml"]
        path = next((p for p in candidates if p.exists()), candidates[0])
    fleet = _read(path, yaml=True)
    company = fleet.get("company")
    if company is not None and not isinstance(company, str):
        raise ValueError("invalid company")
    departments = fleet.get("departments", {})
    if not isinstance(departments, dict) or len(departments) > 100:
        raise ValueError("invalid departments")
    areas, routes, profiles = [], [], set()
    for name, dept in departments.items():
        if not isinstance(name, str) or not name or len(name) > 100 or not isinstance(dept, dict):
            raise ValueError("invalid department")
        profile = dept.get("profile")
        if profile is not None:
            _profile_home(root, profile)
            if profile in profiles:
                raise ValueError("ambiguous profile ownership")
            profiles.add(profile)
        areas.append({"id": name, "owns": _strings(dept.get("owns")),
                      "does_not_own": _strings(dept.get("does_not_own")),
                      "profiles": [profile] if profile else []})
        targets = dept.get("escalates_to", [])
        targets = [targets] if isinstance(targets, str) else targets
        for target in _strings(targets):
            if target not in departments or target == name:
                raise ValueError("invalid route target")
            route = {"from": name, "to": target}
            if route not in routes:
                routes.append(route)
    return company, areas, routes, _iso(path.stat().st_mtime)


def _bot(root, profile, now):
    bot = {"name": profile, "status": "unknown", "availability": "missing", "updated_at": None}
    try:
        path = _profile_home(root, profile) / "gateway_state.json"
        if not path.resolve().is_relative_to(root):
            raise ValueError("unsafe state path")
        record = _read(path)
        stamp = _timestamp(record.get("updated_at"))
        bot["updated_at"] = _iso(stamp)
        if stamp is None or stamp > now + 30:
            raise ValueError("invalid state timestamp")
        if now - stamp > STATE_TTL:
            bot["availability"] = "stale"
            return bot
        state = record.get("gateway_state")
        if state not in {"running", "starting", "stopping", "stopped", "failed", "startup_failed", "degraded", "draining"}:
            raise ValueError("unknown gateway state")
        active = record.get("active_agents")
        bot["availability"] = "fresh"
        bot["status"] = "busy" if state == "running" and isinstance(active, int) and active > 0 else state
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, HTTPException):
        bot["availability"] = "malformed"
    return bot


def _pair(body, area_ids):
    # Read only Owners provenance lines, never return the customer body or title.
    found = []
    for label in ("From", "To"):
        match = re.search(r"^\*\*" + label + r" Department:\*\* ([^\n]+)", body or "", re.MULTILINE)
        if not match:
            return None
        name = match[1].split(" (profile:", 1)[0].strip()
        if name not in area_ids:
            return None
        found.append(name)
    return tuple(found) if found[0] != found[1] else None


def _kanban(root, areas):
    source = {"availability": "missing", "task_count": None, "scanned_count": None,
              "truncated": False, "updated_at": None, "history_available": False}
    path = Path(os.environ.get("HERMES_KANBAN_DB") or
                Path(os.environ.get("HERMES_KANBAN_HOME") or root) / "kanban.db").expanduser()
    tasks, aggregates = [], {}
    if not path.exists():
        return source, tasks, []
    if path.is_symlink():
        source["availability"] = "malformed"
        return source, tasks, []
    try:
        # mode=ro cannot create a DB; query_only also denies accidental future writes.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            source["task_count"] = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
            rows = conn.execute("SELECT id, substr(body, 1, 8192) AS body, assignee, status, created_at, completed_at "
                                "FROM tasks ORDER BY created_at DESC, id LIMIT ?", (SCAN_LIMIT,)).fetchall()
            source["scanned_count"] = len(rows)
            source["truncated"] = source["task_count"] > len(rows)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            source["history_available"] = "task_events" in tables
            area_ids = {a["id"] for a in areas}
            profile_area = {p: a["id"] for a in areas for p in a["profiles"]}
            for row in rows:
                pair = _pair(row["body"], area_ids)
                if not pair:
                    continue
                handback = "not_observed" if source["history_available"] else "unknown"
                returned_at = None
                if source["history_available"]:
                    events = conn.execute("SELECT kind, payload, created_at FROM task_events "
                                          "WHERE task_id=? AND kind IN ('assigned', 'review_requested') "
                                          "ORDER BY id DESC LIMIT 101", (row["id"],)).fetchall()
                    if len(events) > 100:
                        handback = "unknown"
                    for event in events[:100]:
                        try:
                            payload = json.loads(event["payload"] or "{}")
                            if not isinstance(payload, dict):
                                raise TypeError("invalid event")
                            previous = payload.get("from") if event["kind"] == "assigned" else payload.get("implementer")
                            target = payload.get("assignee") if event["kind"] == "assigned" else payload.get("reviewer")
                            if profile_area.get(previous) == pair[1] and profile_area.get(target) == pair[0]:
                                handback, returned_at = "observed", _iso(event["created_at"])
                                break
                        except (ValueError, TypeError):
                            handback = "unknown"
                item = {"id": row["id"], "from": pair[0], "to": pair[1],
                        "current_area": profile_area.get(row["assignee"]),
                        "status": row["status"], "created_at": _iso(row["created_at"]),
                        "completed_at": _iso(row["completed_at"]),
                        "handback": handback, "returned_at": returned_at}
                tasks.append(item)
                agg = aggregates.setdefault(pair, {"from": pair[0], "to": pair[1], "total": 0,
                                                   "returned": 0, "handback_unknown": 0, "statuses": Counter()})
                agg["total"] += 1
                agg["returned"] += handback == "observed"
                agg["handback_unknown"] += handback == "unknown"
                agg["statuses"][row["status"]] += 1
        source.update(availability="available", updated_at=_iso(path.stat().st_mtime),
                      handoff_count=len(tasks), details_truncated=len(tasks) > DETAIL_LIMIT)
        return source, tasks[:DETAIL_LIMIT], list(aggregates.values())
    except (OSError, sqlite3.Error, ValueError, TypeError):
        source.update(availability="malformed", task_count=None, scanned_count=None)
        return source, [], []


def build_snapshot(profile: str | None = None, *, now: float | None = None):
    now = time.time() if now is None else now
    root, home = _root_home()
    if profile:
        home = _profile_home(root, profile)
        if not home.is_dir():
            raise HTTPException(404, "Profile not found")
    result = {"company": None, "profile": profile or (home.name if home.parent.name == "profiles" else "default"),
              "generated_at": _iso(now), "areas": [], "configured_routes": [], "observed_handoffs": [],
              "tasks": [], "warnings": [], "charter": {"availability": "missing", "updated_at": None}}
    try:
        company, areas, routes, updated = _charter(home, root)
        result.update(company=company, configured_routes=routes)
        result["charter"] = {"availability": "available", "updated_at": updated}
    except FileNotFoundError:
        result["warnings"].append("No fleet charter found. No example data is substituted.")
        areas = []
    except (OSError, ValueError, TypeError, AttributeError, HTTPException, YAMLError):
        result["charter"]["availability"] = "malformed"
        result["warnings"].append("Fleet charter or plugin settings are invalid; ownership is unknown.")
        areas = []
    source, tasks, pairs = _kanban(root, areas)
    result.update(kanban=source, tasks=tasks, observed_handoffs=pairs)
    if source["availability"] != "available":
        result["warnings"].append("Kanban unavailable; missing observations do not mean zero handoffs.")
    if source["truncated"]:
        result["warnings"].append("Observations cover the latest 1,000 native cards, not the entire board.")
    if source.get("details_truncated"):
        result["warnings"].append("Task details are limited to the latest 50 observed handoff cards.")
    if not source["history_available"] and source["availability"] == "available":
        result["warnings"].append("Native event history unavailable; handback evidence is unknown.")
    for area in areas:
        area["profiles"] = [_bot(root, p, now) for p in area["profiles"]]
        if any(b["availability"] != "fresh" for b in area["profiles"]) or not area["profiles"]:
            result["warnings"].append(f"{area['id']}: bot state is unknown or not fresh; no offline claim is made.")
    result["areas"] = areas
    return result


@router.get("/snapshot")
def snapshot(response: Response, profile: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    return build_snapshot(profile)
