import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Allow importing plugin root __init__.py
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import importlib
wf = importlib.import_module("__init__")


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
    monkeypatch.setattr(wf, "load_fleet", lambda: fleet)

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


def test_completed_handoff_notifies_origin_department(monkeypatch):
    fleet = {
        "departments": {
            "support": {"profile": "support-test", "notification_webhook": "https://support.example.com/webhook"},
            "tech": {"profile": "dev"},
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda: fleet)
    sent = []
    monkeypatch.setattr(wf, "_send_notification", lambda url, payload: sent.append((url, payload)))

    body = (
        "**From Department:** support (profile: `support-test`)\n"
        "**To Department:** tech (profile: `dev`)\n"
        "**Ticket Ref:** `#8819`\n\n"
        "### Context & Details\n500 error on /login"
    )
    ctx = MagicMock()
    ctx.dispatch_tool.return_value = json.dumps(
        {"task": {"id": "task-100", "title": "Fix login crash", "body": body, "assignee": "dev"}}
    )
    wf.make_task_completed_handler(ctx)(task_id="task-100", summary="Fixed session cookie parser")

    assert sent == [("https://support.example.com/webhook", {
        "event": "handoff_completed",
        "task_id": "task-100",
        "title": "Fix login crash",
        "ticket_id": "#8819",
        "from_department": "support",
        "to_department": "tech",
        "assignee": "dev",
        "summary": "Fixed session cookie parser",
    })]

    # A task that is not a handoff notifies nobody.
    sent.clear()
    ctx.dispatch_tool.return_value = json.dumps({"task": {"id": "task-101", "body": "Regular task"}})
    wf.make_task_completed_handler(ctx)(task_id="task-101")
    assert sent == []
