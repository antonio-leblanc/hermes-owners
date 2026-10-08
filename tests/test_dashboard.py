"""Read-only dashboard safety contracts; all data is fictional."""
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


@pytest.fixture
def home(tmp_path, monkeypatch):
    # Default charter lookup deliberately ignores HERMES_HOME. Isolate both the
    # real user home and plugin fallback, not just profile storage.
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    plugin = tmp_path / "plugin" / "dashboard"
    plugin.mkdir(parents=True)
    monkeypatch.setattr(api, "__file__", str(plugin / "plugin_api.py"))
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(home)
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


def test_absent_or_invalid_charter_never_uses_example_or_last_good(home):
    assert api.build_snapshot()["company"] == "Acme Solar"
    (home / "fleet.yaml").write_text("departments: [broken]")
    assert api.build_snapshot()["areas"] == []
    (home / "fleet.yaml").unlink()
    result = api.build_snapshot()
    assert result["areas"] == []
    assert result["charter"]["availability"] == "missing"


def test_explicit_missing_setting_does_not_fall_back(home):
    (home / "config.yaml").write_text(
        "plugins:\n  entries:\n    owners:\n      settings:\n        fleet_path: absent.yaml\n"
    )
    assert api.build_snapshot()["charter"]["availability"] == "missing"
    (home / "config.yaml").write_text("plugins: [invalid]")
    assert api.build_snapshot()["charter"]["availability"] == "malformed"


def test_default_charter_matches_plugin_global_then_local_precedence(home, monkeypatch):
    (home / "config.yaml").unlink()
    named = home / "profiles" / "support-agent"
    named.mkdir(parents=True)
    (named / "fleet.yaml").write_text("company: Acme Solar Profile\ndepartments: {}")
    global_fleet = Path.home() / ".hermes" / "fleet.yaml"
    global_fleet.parent.mkdir()
    global_fleet.write_text("company: Acme Solar Global\ndepartments: {}")
    assert api.__file__ is not None
    local_fleet = Path(api.__file__).parent.parent / "fleet.yaml"
    local_fleet.write_text("company: Acme Solar Plugin\ndepartments: {}")
    monkeypatch.setenv("HERMES_HOME", str(named))
    assert api.build_snapshot()["company"] == "Acme Solar Global"
    assert api.build_snapshot("default")["company"] == "Acme Solar Global"
    global_fleet.unlink()
    assert api.build_snapshot()["company"] == "Acme Solar Plugin"
    local_fleet.unlink()
    assert api.build_snapshot()["charter"]["availability"] == "missing"


@pytest.mark.parametrize("subtree", ["settings", "config"])
def test_relative_setting_resolves_against_runtime_cwd(home, monkeypatch, subtree):
    cwd = home / "runtime"
    cwd.mkdir()
    (cwd / "fleet.yaml").write_text("company: Acme Solar Runtime\ndepartments: {}")
    (home / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{{subtree}: {{fleet_path: fleet.yaml}}}}}}}}"
    )
    monkeypatch.chdir(cwd)
    assert api.build_snapshot()["company"] == "Acme Solar Runtime"
    (cwd / "fleet.yaml").unlink()
    assert api.build_snapshot()["charter"]["availability"] == "missing"


@pytest.mark.parametrize("reference", ["${OWNERS_TEST_FLEET}", "${env:OWNERS_TEST_FLEET}",
                                        "${ env: OWNERS_TEST_FLEET }"])
@pytest.mark.parametrize("subtree", ["settings", "config"])
def test_settings_expand_environment_like_native_config(home, monkeypatch, reference, subtree):
    fleet = home / "expanded.yaml"
    fleet.write_text("company: Acme Solar Expanded\ndepartments: {}")
    monkeypatch.setenv("OWNERS_TEST_FLEET", str(fleet))
    (home / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{{subtree}: {{fleet_path: '{reference}'}}}}}}}}"
    )
    assert api.build_snapshot()["company"] == "Acme Solar Expanded"
    monkeypatch.delenv("OWNERS_TEST_FLEET")
    assert api.build_snapshot()["charter"]["availability"] == "missing"


@pytest.mark.parametrize("value", ["null", "''", "'${OWNERS_TEST_EMPTY}'"])
def test_explicit_empty_settings_override_legacy_and_use_plugin_default(home, monkeypatch, value):
    global_fleet = Path.home() / ".hermes" / "fleet.yaml"
    global_fleet.parent.mkdir()
    global_fleet.write_text("company: Acme Solar Global\ndepartments: {}")
    monkeypatch.setenv("OWNERS_TEST_EMPTY", "")
    (home / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{settings: {{fleet_path: {value}}}, "
        f"config: {{fleet_path: '{home / 'fleet.yaml'}'}}}}}}}}"
    )
    assert api.build_snapshot()["company"] == "Acme Solar Global"


@pytest.mark.parametrize("reference", ["$OWNERS_TEST_FLEET", "${vault:OWNERS_TEST_FLEET}"])
def test_non_native_environment_references_stay_literal(home, monkeypatch, reference):
    monkeypatch.setenv("OWNERS_TEST_FLEET", str(home / "fleet.yaml"))
    monkeypatch.setenv("vault:OWNERS_TEST_FLEET", str(home / "fleet.yaml"))
    (home / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{settings: {{fleet_path: '{reference}'}}}}}}}}"
    )
    assert api.build_snapshot()["charter"]["availability"] == "missing"


def test_profile_selection_and_symlinks_cannot_escape_root(home, tmp_path):
    with pytest.raises(api.HTTPException) as exc:
        api.build_snapshot("../outside")
    assert exc.value.status_code == 400
    with pytest.raises(api.HTTPException) as exc:
        api.build_snapshot("unknown-agent")
    assert exc.value.status_code == 404
    outside = home.parent / (home.name + "-outside")
    outside.mkdir()
    (home / "profiles").mkdir()
    (home / "profiles" / "escape-agent").symlink_to(outside, target_is_directory=True)
    with pytest.raises(api.HTTPException):
        api.build_snapshot("escape-agent")
    (home / "fleet.yaml").write_text("departments: {support: {profile: '../escape'}}")
    assert api.build_snapshot()["charter"]["availability"] == "malformed"


def test_named_home_uses_shared_board_and_selected_profile_settings(home, monkeypatch):
    named = home / "profiles" / "support-agent"
    named.mkdir(parents=True)
    (named / "fleet.yaml").write_text("company: Acme Solar Local\ndepartments: {}")
    (named / "config.yaml").write_text(
        f"plugins: {{entries: {{owners: {{settings: {{fleet_path: '{named / 'fleet.yaml'}'}}}}}}}}"
    )
    monkeypatch.setenv("HERMES_HOME", str(named))
    assert api.build_snapshot()["company"] == "Acme Solar Local"
    assert api.build_snapshot("default")["company"] == "Acme Solar"
    assert not (named / "kanban.db").exists()


@pytest.mark.parametrize("record,availability,status", [
    (None, "missing", "unknown"),
    ("bad-json", "malformed", "unknown"),
    ({"gateway_state": "running", "updated_at": "2000-01-01T00:00:00Z"}, "stale", "unknown"),
    ({"gateway_state": "running", "updated_at": "invalid"}, "malformed", "unknown"),
    ({"gateway_state": "running", "updated_at": "2099-01-01T00:00:00Z"}, "malformed", "unknown"),
    ({"gateway_state": "running", "updated_at": "2026-01-01T00:00:00Z", "active_agents": 2}, "fresh", "busy"),
])
def test_gateway_missing_stale_malformed_are_not_offline(home, record, availability, status):
    profile = home / "profiles" / "tech-agent"
    profile.mkdir(parents=True)
    if record is not None:
        (profile / "gateway_state.json").write_text(
            json.dumps(record) if isinstance(record, dict) else record
        )
    result = api.build_snapshot(now=1767225600)
    bot = result["areas"][1]["profiles"][0]
    assert bot["availability"] == availability
    assert bot["status"] == status


def test_only_native_historical_transfer_proves_return(home):
    conn = board(home)
    task(conn, "done-card", "done")
    task(conn, "blocked-card", "blocked")
    task(conn, "review-card", "review", "support-agent")
    task(conn, "assigned-card", "ready", "support-agent")
    task(conn, "no-proof-card", "review", "support-agent")
    conn.execute("INSERT INTO task_events VALUES (1, 'review-card', 'review_requested', ?, 1700000100)",
                 (json.dumps({"implementer": "tech-agent", "reviewer": "support-agent", "summary": "PRIVATE"}),))
    conn.execute("INSERT INTO task_events VALUES (2, 'assigned-card', 'assigned', ?, 1700000200)",
                 (json.dumps({"from": "tech-agent", "assignee": "support-agent"}),))
    conn.commit()
    conn.close()
    before = (home / "kanban.db").read_bytes()
    result = api.build_snapshot()
    items = {t["id"]: t for t in result["tasks"]}
    assert items["done-card"]["handback"] == "not_observed"
    assert items["blocked-card"]["status"] == "blocked"
    assert "stuck" not in json.dumps(result)
    assert items["review-card"]["handback"] == "observed"
    assert items["assigned-card"]["handback"] == "observed"
    assert items["no-proof-card"]["handback"] == "not_observed"
    assert result["observed_handoffs"][0]["returned"] == 2
    assert result["configured_routes"] == [{"from": "support", "to": "tech"}]
    assert "PRIVATE" not in json.dumps(result)
    assert (home / "kanban.db").read_bytes() == before


def test_missing_database_distinct_from_zero_and_no_creation(home):
    result = api.build_snapshot()
    assert result["kanban"]["availability"] == "missing"
    assert result["kanban"]["task_count"] is None
    assert not (home / "kanban.db").exists()
    conn = board(home)
    conn.close()
    assert api.build_snapshot()["kanban"]["task_count"] == 0


def test_malformed_events_leave_handback_unknown_not_false(home):
    conn = board(home)
    task(conn, "card", "done")
    conn.execute("INSERT INTO task_events VALUES (1, 'card', 'assigned', 'bad-json', 1700000100)")
    conn.commit()
    conn.close()
    assert api.build_snapshot()["tasks"][0]["handback"] == "unknown"


def test_router_read_only_and_profile_scope(home):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/owners")
    client = TestClient(app)
    assert client.get("/api/plugins/owners/snapshot").json()["company"] == "Acme Solar"
    assert client.get("/api/plugins/owners/snapshot?profile=../escape").status_code == 400
    assert client.post("/api/plugins/owners/snapshot").status_code == 405
    assert client.get("/api/plugins/owners/snapshot").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("charter", ["departments: [", "departments: {tech: {profile: Tech}}",
    "departments: {tech: {profile: tech-agent, owns: code}}",
    "departments: {tech: {profile: tech-agent, escalates_to: absent}}"])
def test_bad_yaml_and_invalid_ownership_fail_closed(home, charter):
    (home / "fleet.yaml").write_text(charter)
    result = api.build_snapshot()
    assert result["charter"]["availability"] == "malformed"
    assert result["areas"] == []


def test_missing_event_table_and_corrupt_database_remain_unknown(home):
    conn = board(home)
    task(conn, "card", "done")
    conn.execute("DROP TABLE task_events")
    conn.commit()
    conn.close()
    assert api.build_snapshot()["tasks"][0]["handback"] == "unknown"
    (home / "kanban.db").write_bytes(b"not sqlite")
    assert api.build_snapshot()["kanban"]["availability"] == "malformed"
    assert api.build_snapshot()["kanban"]["task_count"] is None


def test_scan_and_details_bounds_are_explicit(home):
    conn = board(home)
    for i in range(1001):
        task(conn, f"card-{i:04}", "ready")
    conn.commit()
    conn.close()
    result = api.build_snapshot()
    assert result["kanban"]["task_count"] == 1001
    assert result["kanban"]["scanned_count"] == 1000
    assert result["kanban"]["truncated"] is True
    assert result["kanban"]["details_truncated"] is True
    assert len(result["tasks"]) == 50
    assert result["observed_handoffs"][0]["total"] == 1000


def test_readonly_connection_closes_and_rejects_writes(home, monkeypatch):
    conn = board(home)
    conn.close()
    real_connect = sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        assert args[0].endswith("?mode=ro") and kwargs["uri"] is True
        conn = real_connect(*args, **kwargs)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden (id TEXT)")
        connections.append(conn)
        return conn
    monkeypatch.setattr(api.sqlite3, "connect", connect)
    api.build_snapshot()
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_legacy_setting_and_symlinked_state(home):
    original_config = (home / "config.yaml").read_text()
    (home / "alternate.yaml").write_text("company: Acme Solar Alternate\ndepartments: {}")
    (home / "config.yaml").write_text("plugins: {entries: {owners: {config: {fleet_path: alternate.yaml}}}}")
    assert api.build_snapshot()["company"] == "Acme Solar Alternate"
    (home / "config.yaml").write_text(original_config)
    outside = home.parent / (home.name + "-state.json")
    outside.write_text('{}')
    profile = home / "profiles" / "tech-agent"
    profile.mkdir(parents=True)
    (profile / "gateway_state.json").symlink_to(outside)
    assert api.build_snapshot()["areas"][1]["profiles"][0]["availability"] == "malformed"
