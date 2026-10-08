"""Owners snapshot API: read-only view of the charter, bot status and handoffs on the native kanban.

Storage contracts checked against hermes-agent ee8dd6c886. The host provides auth;
no request may supply a filesystem path.
"""
from __future__ import annotations

import importlib.util
import json
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

# Charter lookup is the plugin's own, so the dashboard never reads a different fleet.yaml than the bots.
_spec = importlib.util.spec_from_file_location("_owners_plugin", Path(__file__).parent.parent / "__init__.py")
_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plugin)


def _read(path: Path, *, yaml=False):
    text = path.read_text(encoding="utf-8")
    value = YAML(typ="safe").load(text) if yaml else json.loads(text)
    if not isinstance(value, dict):
        raise TypeError("expected mapping")
    return value


def _root_home():
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
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
            return dt.timestamp() if dt.tzinfo else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    except (ValueError, OverflowError):
        pass
    return None


def _iso(value):
    stamp = _timestamp(value)
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def _strings(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("expected text list")
    return value


def _charter(home, root):
    config = home / "config.yaml"
    cfg = _read(config, yaml=True) if config.exists() else {}
    entry = ((cfg.get("plugins") or {}).get("entries") or {}).get("owners") or {}
    path = _plugin.get_fleet_path((entry.get("settings") or {}).get("fleet_path"))
    if path is None:
        raise FileNotFoundError("no fleet charter")
    fleet = _read(path, yaml=True)
    departments = fleet.get("departments") or {}
    if not isinstance(departments, dict):
        raise TypeError("invalid departments")
    areas, routes, profiles = [], [], set()
    for name, dept in departments.items():
        if not isinstance(dept, dict):
            raise TypeError("invalid department")
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
        for target in _strings([targets] if isinstance(targets, str) else targets):
            if target not in departments or target == name:
                raise ValueError("invalid route target")
            if {"from": name, "to": target} not in routes:
                routes.append({"from": name, "to": target})
    return fleet.get("company"), areas, routes, _iso(path.stat().st_mtime)


def _fresh_state(path, now):
    """A gateway_state.json updated within STATE_TTL, else None. Stale is unknown, not offline."""
    try:
        record = _read(path)
    except (OSError, ValueError, TypeError):
        return None
    stamp = _timestamp(record.get("updated_at"))
    return record if stamp is not None and 0 <= now - stamp <= STATE_TTL else None


def _bot(root, profile, now, shared):
    # A multiplexed gateway in the root home serves several profiles and keys their
    # platforms as "<profile>:<platform>" (unprefixed for default). Per-profile state
    # files stop being written once it takes over, so they are only read as a fallback.
    if shared and profile in (shared.get("served_profiles") or []):
        prefix = "" if profile == "default" else profile + ":"
        platforms = [v for k, v in (shared.get("platforms") or {}).items()
                     if isinstance(v, dict) and (k.startswith(prefix) if prefix else ":" not in k)]
        status = shared.get("gateway_state") or "unknown"
        if status == "running" and any(p.get("state") != "connected" for p in platforms):
            status = "degraded"
        return {"name": profile, "status": status, "availability": "fresh",
                "updated_at": _iso(shared.get("updated_at"))}
    bot = {"name": profile, "status": "unknown", "availability": "missing", "updated_at": None}
    path = _profile_home(root, profile) / "gateway_state.json"
    if path.exists():
        record = _fresh_state(path, now)
        bot["availability"] = "fresh" if record else "stale"
        if record:
            bot["status"] = record.get("gateway_state") or "unknown"
            bot["updated_at"] = _iso(record.get("updated_at"))
    return bot


def _pair(body, area_ids):
    # Read only the Owners provenance lines, never return the customer body or title.
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


def _handback(conn, task_id, pair, profile_area):
    """Observed when a native event moved the card from the receiving area back to the origin."""
    events = conn.execute("SELECT kind, payload, created_at FROM task_events "
                          "WHERE task_id=? AND kind IN ('assigned', 'review_requested') ORDER BY id DESC",
                          (task_id,)).fetchall()
    for event in events:
        try:
            payload = json.loads(event["payload"] or "{}")
        except ValueError:
            return "unknown", None
        if not isinstance(payload, dict):
            return "unknown", None
        if event["kind"] == "assigned":
            previous, target = payload.get("from"), payload.get("assignee")
        else:
            previous, target = payload.get("implementer"), payload.get("reviewer")
        if profile_area.get(previous) == pair[1] and profile_area.get(target) == pair[0]:
            return "observed", _iso(event["created_at"])
    return "not_observed", None


def _kanban(root, areas):
    source = {"availability": "missing", "task_count": None, "scanned_count": None,
              "truncated": False, "updated_at": None, "history_available": False}
    path = Path(os.environ.get("HERMES_KANBAN_DB") or
                Path(os.environ.get("HERMES_KANBAN_HOME") or root) / "kanban.db").expanduser()
    if not path.exists():
        return source, [], []
    tasks, aggregates = [], {}
    try:
        # mode=ro cannot create or write the database.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            source["task_count"] = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
            rows = conn.execute("SELECT id, body, assignee, status, created_at, completed_at "
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
                handback, returned_at = (_handback(conn, row["id"], pair, profile_area)
                                         if source["history_available"] else ("unknown", None))
                tasks.append({"id": row["id"], "from": pair[0], "to": pair[1],
                              "current_area": profile_area.get(row["assignee"]),
                              "status": row["status"], "created_at": _iso(row["created_at"]),
                              "completed_at": _iso(row["completed_at"]),
                              "handback": handback, "returned_at": returned_at})
                agg = aggregates.setdefault(pair, {"from": pair[0], "to": pair[1], "total": 0,
                                                   "returned": 0, "handback_unknown": 0, "statuses": Counter()})
                agg["total"] += 1
                agg["returned"] += handback == "observed"
                agg["handback_unknown"] += handback == "unknown"
                agg["statuses"][row["status"]] += 1
        source.update(availability="available", updated_at=_iso(path.stat().st_mtime),
                      handoff_count=len(tasks), details_truncated=len(tasks) > DETAIL_LIMIT)
        return source, tasks[:DETAIL_LIMIT], list(aggregates.values())
    except (OSError, sqlite3.Error):
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
    shared = _fresh_state(root / "gateway_state.json", now)
    for area in areas:
        area["profiles"] = [_bot(root, p, now, shared) for p in area["profiles"]]
        if not area["profiles"] or any(b["availability"] != "fresh" for b in area["profiles"]):
            result["warnings"].append(f"{area['id']}: bot state is unknown or not fresh; no offline claim is made.")
    result["areas"] = areas
    return result


@router.get("/snapshot")
def snapshot(response: Response, profile: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    return build_snapshot(profile)
