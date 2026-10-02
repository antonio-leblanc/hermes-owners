import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow importing plugin root __init__.py
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import importlib
wf = importlib.import_module("__init__")


def test_parse_handoff_body():
    body = (
        "**From Department:** support (profile: `support-test`)\n"
        "**To Department:** tech (profile: `dev`)\n"
        "**Ticket Ref:** `#4412`\n\n"
        "### Context & Details\nCustomer cannot login."
    )
    meta = wf.parse_handoff_body(body)
    assert meta["from_department"] == "support"
    assert meta["to_department"] == "tech"
    assert meta["ticket_id"] == "#4412"


def test_parse_handoff_body_empty():
    assert wf.parse_handoff_body(None) == {}
    assert wf.parse_handoff_body("Regular kanban task body") == {}


def test_build_charter():
    fleet = {
        "company": "Acme Solar",
        "departments": {
            "support": {
                "profile": "support-test",
                "owns": ["Customer questions", "Ticket status"],
                "does_not_own": ["Bugs"],
                "escalates_to": "tech",
            },
            "tech": {
                "profile": "dev",
                "owns": ["Bugs", "Infrastructure"],
            },
        },
    }
    charter = wf.build_charter(fleet, "support-test")
    assert charter is not None
    assert "You are the support department of Acme Solar." in charter
    assert "You own: Customer questions; Ticket status." in charter
    assert "You do not own: Bugs." in charter
    assert "hand it off to tech using the `handoff_task` tool" in charter
    assert "- tech: Bugs; Infrastructure" in charter

    assert wf.build_charter(fleet, "unknown-profile") is None


def test_load_fleet_dynamic_mtime(tmp_path, monkeypatch):
    import os
    fleet_file = tmp_path / "fleet.yaml"
    fleet_file.write_text("company: Acme V1\n", encoding="utf-8")

    monkeypatch.setattr(wf, "get_fleet_path", lambda: fleet_file)

    fleet1 = wf.load_fleet(force_reload=True)
    assert fleet1["company"] == "Acme V1"

    # Update file content and modify mtime
    fleet_file.write_text("company: Acme V2\n", encoding="utf-8")
    new_mtime = fleet_file.stat().st_mtime + 5.0
    os.utime(fleet_file, (new_mtime, new_mtime))

    # Dynamic reload should pick up changes without force_reload=True
    fleet2 = wf.load_fleet()
    assert fleet2["company"] == "Acme V2"


def test_handoff_task_policy_enforcement(monkeypatch):
    fleet = {
        "company": "Acme Solar",
        "departments": {
            "support": {
                "profile": "support-test",
                "owns": ["Customer questions"],
                "escalates_to": "tech",
            },
            "tech": {
                "profile": "dev",
                "owns": ["Bugs"],
            },
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda **kw: fleet)

    ctx = MagicMock()
    ctx.profile_name = "support-test"
    handler = wf.make_handoff_handler(ctx)

    # Disallowed destination
    res_raw = handler({"to_department": "finance", "title": "Pay invoice", "context": "urgent"})
    res = json.loads(res_raw)
    assert res["ok"] is False
    assert "Policy violation" in res["error"]

    # Allowed destination dispatches to kanban_create
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-100", "status": "blocked"})
    res_raw2 = handler({
        "to_department": "tech",
        "title": "Fix login crash",
        "context": "500 error on /login",
        "ticket_id": "#8819",
    })
    res2 = json.loads(res_raw2)
    assert res2["ok"] is True
    assert res2["task_id"] == "task-100"
    assert res2["status"] == "blocked"

    # Check dispatch args passed to ctx.dispatch_tool
    ctx.dispatch_tool.assert_called_once()
    args_called = ctx.dispatch_tool.call_args[0][1]
    assert args_called["title"] == "Fix login crash"
    assert args_called["assignee"] == "dev"
    assert args_called["initial_status"] == "blocked"
    assert args_called["idempotency_key"] == "workforce:support:#8819"


def test_task_completed_closed_loop(tmp_path, monkeypatch):
    # Setup temporary kanban db
    db_file = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT, result TEXT)"
    )
    body = (
        "**From Department:** support (profile: `support-test`)\n"
        "**To Department:** tech (profile: `dev`)\n"
        "**Ticket Ref:** `#8819`\n\n"
        "### Context & Details\n500 error on /login"
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        ("task-100", "Fix login crash", body, "dev", "done", "Fixed session cookie parser"),
    )
    conn.commit()
    conn.close()

    fleet = {
        "company": "Acme Solar",
        "departments": {
            "support": {
                "profile": "support-test",
                "notification_webhook": "https://support.example.com/webhook",
            },
            "tech": {
                "profile": "dev",
            },
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda **kw: fleet)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Monkeypatch db_path resolution inside on_kanban_task_completed
    recorded_notifs = []
    monkeypatch.setattr(wf, "_send_notification", lambda url, payload: recorded_notifs.append((url, payload)))

    # Ensure tmp_path / .hermes / kanban.db exists
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    db_dest = hermes_dir / "kanban.db"
    db_dest.write_bytes(db_file.read_bytes())

    ctx = MagicMock()
    handler = wf.make_task_completed_handler(ctx)

    handler(task_id="task-100", summary="Fixed session cookie parser", assignee="dev")

    # Verify notification sent to support webhook
    assert len(recorded_notifs) == 1
    target_url, payload = recorded_notifs[0]
    assert target_url == "https://support.example.com/webhook"
    assert payload["event"] == "handoff_completed"
    assert payload["task_id"] == "task-100"
    assert payload["ticket_id"] == "#8819"
    assert payload["from_department"] == "support"
    assert payload["summary"] == "Fixed session cookie parser"

    # Verify resolution log created
    resolutions_file = hermes_dir / "workforce" / "resolutions.jsonl"
    assert resolutions_file.exists()
    lines = resolutions_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["task_id"] == "task-100"
    assert saved["ticket_id"] == "#8819"
    assert saved["summary"] == "Fixed session cookie parser"
