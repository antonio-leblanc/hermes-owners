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
            "sales": {
                "profile": "sales-agent",
                "owns": ["Pricing and quotes"],
            },
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda *_: fleet)

    ctx = MagicMock()
    ctx.profile_name = "support-test"
    handler = wf.make_handoff_handler(ctx)

    # Unknown destination rejected
    res_raw = handler({"to_department": "finance", "title": "Pay invoice", "context": "urgent"})
    res = json.loads(res_raw)
    assert res["ok"] is False
    assert "Unknown department 'finance'" in res["error"]

    # Handoff to self rejected
    res_self_raw = handler({"to_department": "support", "title": "Self task", "context": "loop"})
    res_self = json.loads(res_self_raw)
    assert res_self["ok"] is False
    assert "cannot hand off a task to itself" in res_self["error"]

    # Hand off to default escalates_to ("tech") dispatches to kanban_create
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-100", "status": "blocked", "subscribed": True})
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
    assert res2["subscribed"] is True
    assert "Successfully created" not in res2["message"]
    assert "awaiting human review" in res2["message"]

    # Check dispatch args passed to ctx.dispatch_tool
    ctx.dispatch_tool.assert_called_once()
    args_called = ctx.dispatch_tool.call_args[0][1]
    assert args_called["title"] == "Fix login crash"
    assert args_called["assignee"] == "dev"
    assert args_called["initial_status"] == "blocked"
    assert args_called["idempotency_key"] == "owners:support:tech:#8819"

    # Hand off to any other department in charter ("sales") is also allowed
    ctx.dispatch_tool.reset_mock()
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-101", "status": "blocked", "subscribed": False})
    res_sales_raw = handler({
        "to_department": "sales",
        "title": "Enterprise inquiry",
        "context": "Customer wants 500 panels",
        "ticket_id": "#8820",
    })
    res_sales = json.loads(res_sales_raw)
    assert res_sales["ok"] is True
    assert res_sales["task_id"] == "task-101"
    assert res_sales["subscribed"] is False
    args_sales = ctx.dispatch_tool.call_args[0][1]
    assert args_sales["assignee"] == "sales-agent"
    assert args_sales["idempotency_key"] == "owners:support:sales:#8820"


def test_completed_handoff_notifies_origin_department(monkeypatch):
    fleet = {
        "departments": {
            "support": {"profile": "support-test", "notification_webhook": "https://support.example.com/webhook"},
            "tech": {"profile": "dev"},
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda *_: fleet)
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


def test_handoff_task_appends_target_intake(monkeypatch):
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
                "intake": "Investigate root cause and estimate complexity before modifying code.",
            },
            "sales": {
                "profile": "sales-agent",
                "owns": ["Sales"],
                "intake": [
                    "Qualify lead revenue",
                    "Do not offer custom pricing without finance",
                ],
            },
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda *_: fleet)

    ctx = MagicMock()
    ctx.profile_name = "support-test"
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-200", "status": "blocked", "subscribed": True})
    handler = wf.make_handoff_handler(ctx)

    # String intake
    res_raw = handler({
        "to_department": "tech",
        "title": "Investigate 500 error",
        "context": "Crash on /login",
        "ticket_id": "#9942",
    })
    res = json.loads(res_raw)
    assert res["ok"] is True
    body = ctx.dispatch_tool.call_args[0][1]["body"]
    assert "### Department Intake (tech)" in body
    assert "Investigate root cause and estimate complexity before modifying code." in body

    # List intake
    ctx.dispatch_tool.reset_mock()
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-201", "status": "blocked", "subscribed": False})
    res_sales_raw = handler({
        "to_department": "sales",
        "title": "New enterprise lead",
        "context": "Needs 5000 panels",
    })
    res_sales = json.loads(res_sales_raw)
    assert res_sales["ok"] is True
    body_sales = ctx.dispatch_tool.call_args[0][1]["body"]
    assert "### Department Intake (sales)" in body_sales
    assert "- Qualify lead revenue" in body_sales
    assert "- Do not offer custom pricing without finance" in body_sales


def test_handoff_task_initial_status_configurable(monkeypatch):
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
                "initial_status": "ready",
            },
            "ops": {
                "profile": "ops-agent",
                "owns": ["Operations"],
                "initial_status": "blocked",
            },
            "triage_dept": {
                "profile": "triage-agent",
                "owns": ["Misc"],
                "initial_status": "triage",
            },
        },
    }
    monkeypatch.setattr(wf, "load_fleet", lambda *_: fleet)

    ctx = MagicMock()
    ctx.profile_name = "support-test"
    handler = wf.make_handoff_handler(ctx)

    # 1. Destination with initial_status="ready" -> dispatches "running" (creating "ready" task)
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-ready-1", "status": "ready", "subscribed": True})
    res_raw = handler({
        "to_department": "tech",
        "title": "Investigate bug",
        "context": "Bug details",
        "ticket_id": "#1001",
    })
    res = json.loads(res_raw)
    assert res["ok"] is True
    assert res["status"] == "ready"
    assert "ready for execution" in res["message"]
    args_tech = ctx.dispatch_tool.call_args[0][1]
    assert args_tech["initial_status"] == "running"

    # 2. Destination with explicit initial_status="blocked" -> dispatches "blocked"
    ctx.dispatch_tool.reset_mock()
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-blocked-1", "status": "blocked", "subscribed": True})
    res_ops_raw = handler({
        "to_department": "ops",
        "title": "Ops review",
        "context": "Contract details",
        "ticket_id": "#1002",
    })
    res_ops = json.loads(res_ops_raw)
    assert res_ops["ok"] is True
    assert res_ops["status"] == "blocked"
    assert "awaiting human review" in res_ops["message"]
    args_ops = ctx.dispatch_tool.call_args[0][1]
    assert args_ops["initial_status"] == "blocked"

    # 3. Destination attempting "triage" -> never uses triage, falls back to "blocked"
    ctx.dispatch_tool.reset_mock()
    ctx.dispatch_tool.return_value = json.dumps({"task_id": "task-triage-1", "status": "blocked", "subscribed": True})
    res_triage_raw = handler({
        "to_department": "triage_dept",
        "title": "Triage attempt",
        "context": "Needs review",
        "ticket_id": "#1003",
    })
    res_triage = json.loads(res_triage_raw)
    assert res_triage["ok"] is True
    assert res_triage["status"] == "blocked"
    args_triage = ctx.dispatch_tool.call_args[0][1]
    assert args_triage["initial_status"] == "blocked"


def test_configured_fleet_path_never_falls_back(tmp_path, monkeypatch):
    # A charter at the default location must not be picked up when the setting points elsewhere.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "fleet.yaml").write_text("company: Wrong Co\n", encoding="utf-8")

    assert wf.get_fleet_path(str(tmp_path / "missing.yaml")) is None
    assert wf.get_fleet_path() == tmp_path / ".hermes" / "fleet.yaml"
