"""Read-only dashboard contracts; all data is fictional."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SPEC = importlib.util.spec_from_file_location(
    "owners_dashboard", Path(__file__).parents[1] / "dashboard" / "plugin_api.py"
)
assert SPEC is not None and SPEC.loader is not None
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)

NOW = 1767225600  # 2026-01-01T00:00:00Z


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    (home / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{settings: {{fleet_path: '{home / 'fleet.yaml'}'}}}}}}}}\n"
    )
    (home / "fleet.yaml").write_text(
        "company: Acme Solar\ndepartments:\n"
        "  support:\n    profile: support-agent\n    owns: [Customer inquiries]\n"
        "    does_not_own: [Code fixes]\n    escalates_to: tech\n"
        "  tech:\n    profile: tech-agent\n    owns: [Code fixes]\n"
    )
    return home


def board(home):
    conn = sqlite3.connect(home / "kanban.db")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, body TEXT, assignee TEXT,
                            status TEXT, created_at INTEGER, completed_at INTEGER);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT,
                                  kind TEXT, payload TEXT, created_at INTEGER);
    """)
    return conn


def task(conn, ident, status, assignee="tech-agent"):
    conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)", (
        ident, ("**From Department:** support (profile: `support-agent`)\n"
        "**To Department:** tech (profile: `tech-agent`)\n"
        "**Ticket Ref:** `PRIVATE-REFERENCE`\nPrivate customer context"),
        assignee, status, 1700000000, None,
    ))


def state(path, **record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"gateway_state": "running", "updated_at": "2026-01-01T00:00:00Z", **record}))


def test_missing_or_invalid_charter_never_uses_example(home):
    (home / "fleet.yaml").write_text("departments: [broken]")
    assert api.build_snapshot()["charter"]["availability"] == "malformed"
    (home / "fleet.yaml").unlink()
    result = api.build_snapshot()
    assert result["areas"] == []
    assert result["charter"]["availability"] == "missing"


def test_profile_selection_cannot_escape_root(home):
    with pytest.raises(api.HTTPException) as exc:
        api.build_snapshot("../outside")
    assert exc.value.status_code == 400
    (home / "fleet.yaml").write_text("departments: {support: {profile: '../escape'}}")
    assert api.build_snapshot()["charter"]["availability"] == "malformed"


def test_multiplexed_gateway_reports_each_served_profile(home):
    # Per-profile files are left stale once one gateway serves every profile.
    state(home / "profiles" / "support-agent" / "gateway_state.json",
          gateway_state="stopped", updated_at="2025-12-01T00:00:00Z")
    state(home / "gateway_state.json", served_profiles=["default", "support-agent"], platforms={
        "telegram": {"state": "connected"},
        "support-agent:telegram": {"state": "retrying"},
    })
    support, tech = (a["profiles"][0] for a in api.build_snapshot(now=NOW)["areas"])
    assert (support["availability"], support["status"]) == ("fresh", "degraded")
    assert (tech["availability"], tech["status"]) == ("missing", "unknown")


def test_stale_state_is_unknown_not_offline(home):
    state(home / "profiles" / "tech-agent" / "gateway_state.json",
          gateway_state="stopped", updated_at="2025-12-01T00:00:00Z")
    tech = api.build_snapshot(now=NOW)["areas"][1]["profiles"][0]
    assert (tech["availability"], tech["status"]) == ("stale", "unknown")


def test_only_a_transfer_back_to_origin_counts_as_handback(home):
    conn = board(home)
    task(conn, "done-card", "done")
    task(conn, "review-card", "review", "support-agent")
    task(conn, "assigned-card", "ready", "support-agent")
    conn.execute("INSERT INTO task_events VALUES (1, 'review-card', 'review_requested', ?, 1700000100)",
                 (json.dumps({"implementer": "tech-agent", "reviewer": "support-agent", "summary": "PRIVATE"}),))
    conn.execute("INSERT INTO task_events VALUES (2, 'assigned-card', 'assigned', ?, 1700000200)",
                 (json.dumps({"from": "tech-agent", "assignee": "support-agent"}),))
    conn.commit()
    conn.close()
    before = (home / "kanban.db").read_bytes()
    result = api.build_snapshot()
    items = {t["id"]: t["handback"] for t in result["tasks"]}
    assert items == {"done-card": "not_observed", "review-card": "observed", "assigned-card": "observed"}
    assert result["observed_handoffs"][0]["returned"] == 2
    assert "PRIVATE" not in json.dumps(result)
    assert (home / "kanban.db").read_bytes() == before


def test_missing_database_is_not_zero_and_is_not_created(home):
    result = api.build_snapshot()
    assert result["kanban"]["availability"] == "missing"
    assert result["kanban"]["task_count"] is None
    assert not (home / "kanban.db").exists()


def test_router_is_read_only(home):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/owners")
    client = TestClient(app)
    assert client.get("/api/plugins/owners/snapshot").json()["company"] == "Acme Solar"
    assert client.get("/api/plugins/owners/snapshot?profile=../escape").status_code == 400
    assert client.post("/api/plugins/owners/snapshot").status_code == 405
