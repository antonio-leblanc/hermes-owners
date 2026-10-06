"""hermes-owners: injects company charter, provides departmental handoff, and closes the resolution loop."""

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# Cache for dynamic reloading of fleet.yaml
_FLEET_CACHE: dict = {}
_LAST_MTIME: float = 0.0
_LAST_PATH: Path | None = None


def get_fleet_path(configured: str | None = None) -> Path | None:
    """Resolve fleet.yaml: the `fleet_path` setting if set, else ~/.hermes/fleet.yaml, then plugin directory.

    A configured path that does not exist resolves to None: it never falls back to another charter.
    """
    if configured:
        path = Path(configured).expanduser()
        return path if path.exists() else None
    home_fleet = Path.home() / ".hermes" / "fleet.yaml"
    if home_fleet.exists():
        return home_fleet
    local_fleet = Path(__file__).parent / "fleet.yaml"
    if local_fleet.exists():
        return local_fleet
    return None


def load_fleet(configured: str | None = None) -> dict:
    """Read fleet.yaml with mtime-based dynamic reload. Never falls back to example charter."""
    global _FLEET_CACHE, _LAST_MTIME, _LAST_PATH
    fleet_path = get_fleet_path(configured)
    if not fleet_path or not fleet_path.exists():
        _FLEET_CACHE = {}
        _LAST_MTIME = 0.0
        _LAST_PATH = None
        return {}

    try:
        mtime = fleet_path.stat().st_mtime
        if mtime != _LAST_MTIME or fleet_path != _LAST_PATH:
            _FLEET_CACHE = YAML(typ="safe").load(fleet_path.read_text(encoding="utf-8")) or {}
            _LAST_MTIME = mtime
            _LAST_PATH = fleet_path
    except Exception as e:
        logger.warning("owners: failed to load fleet at %s: %s", fleet_path, e)
    return _FLEET_CACHE


def build_charter(fleet: dict, profile: str) -> str | None:
    """Charter text for the department served by `profile`, or None if it serves none."""
    departments = fleet.get("departments") or {}
    mine = next((k for k, d in departments.items() if d.get("profile") == profile), None)
    if mine is None:
        return None
    dept = departments[mine]

    lines = [f"You are the {mine} department of {fleet.get('company', 'the company')}."]
    if dept.get("owns"):
        lines.append("You own: " + "; ".join(dept["owns"]) + ". Handle these as usual, with your tools.")
    if dept.get("does_not_own"):
        lines.append("You do not own: " + "; ".join(dept["does_not_own"]) + ".")
    escalates = dept.get("escalates_to")
    default_hint = f" (default: {', '.join(escalates) if isinstance(escalates, list) else escalates})" if escalates else ""
    lines.append(
        f"When a request is not yours, do not try to solve it: hand it off to the owner department{default_hint} using the `handoff_task` tool. "
        "Only say you escalated or registered something if a tool call actually did it."
    )

    others = [f"- {k}: " + "; ".join(d.get("owns") or []) for k, d in departments.items() if k != mine]
    if others:
        lines.append("Other departments:\n" + "\n".join(others))
    return "\n".join(lines)


def _send_notification(webhook_url: str, payload: dict) -> bool:
    """Send an outbound JSON notification to a department webhook without blocking or raising."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "hermes-owners/0.1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        logger.warning("owners: notification webhook failed for %s: %s", webhook_url, e)
        return False


def parse_handoff_body(body: str | None) -> dict:
    """Extract structured handoff metadata from kanban task body."""
    meta: dict[str, str] = {}
    if not body:
        return meta
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("**From Department:**"):
            parts = line.replace("**From Department:**", "").strip()
            dept = parts.split("(")[0].strip()
            meta["from_department"] = dept
        elif line.startswith("**To Department:**"):
            parts = line.replace("**To Department:**", "").strip()
            dept = parts.split("(")[0].strip()
            meta["to_department"] = dept
        elif line.startswith("**Ticket Ref:**"):
            ref = line.replace("**Ticket Ref:**", "").strip().strip("`")
            meta["ticket_id"] = ref
    return meta


HANDOFF_TASK_SCHEMA = {
    "name": "handoff_task",
    "description": (
        "Hand off a request or ticket that belongs to another department according to the company charter. "
        "Opens a task on the Hermes Kanban for the destination department's profile "
        "(in ready or blocked status as configured in the charter) carrying ticket and request details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_department": {
                "type": "string",
                "description": "Destination department name as defined in the company charter (e.g. 'tech').",
            },
            "title": {
                "type": "string",
                "description": "Short, clear title for the handoff task.",
            },
            "context": {
                "type": "string",
                "description": "Detailed context: customer report, error logs, reproduction steps, or investigation notes.",
            },
            "ticket_id": {
                "type": "string",
                "description": "Optional external ticket ID or reference URL (e.g. '#1234', 'INC-992').",
            },
        },
        "required": ["to_department", "title", "context"],
        "additionalProperties": False,
    },
}


def make_handoff_handler(ctx):
    def handoff_task(args: dict, **kwargs) -> str:
        fleet = load_fleet(ctx.get_config("fleet_path"))
        if not fleet:
            return json.dumps({"ok": False, "error": "No fleet.yaml found for owners plugin."})

        departments = fleet.get("departments") or {}
        my_profile = ctx.profile_name
        my_dept_name = next((k for k, d in departments.items() if d.get("profile") == my_profile), None)

        if not my_dept_name:
            return json.dumps({
                "ok": False,
                "error": f"Current profile '{my_profile}' is not assigned to any department in fleet.yaml.",
            })

        my_dept = departments[my_dept_name]

        canonical_departments = {k.lower(): k for k in departments.keys()}
        to_dept_raw = (args.get("to_department") or "").strip()
        to_dept_key = to_dept_raw.lower()

        if to_dept_key not in canonical_departments:
            return json.dumps({
                "ok": False,
                "error": (
                    f"Unknown department '{to_dept_raw}'. Known departments: "
                    f"{list(departments.keys())}."
                ),
            })

        canonical_target_dept = canonical_departments[to_dept_key]
        if canonical_target_dept == my_dept_name:
            return json.dumps({
                "ok": False,
                "error": f"Department '{my_dept_name}' cannot hand off a task to itself.",
            })

        target_info = departments.get(canonical_target_dept)
        if not target_info or not target_info.get("profile"):
            return json.dumps({
                "ok": False,
                "error": f"Destination department '{canonical_target_dept}' has no profile defined in fleet.yaml.",
            })

        target_profile = target_info["profile"]
        title = (args.get("title") or "").strip()
        context = (args.get("context") or "").strip()
        ticket_id = (args.get("ticket_id") or "").strip()

        if not title:
            return json.dumps({"ok": False, "error": "title is required"})
        if not context:
            return json.dumps({"ok": False, "error": "context is required"})

        body_lines = [
            f"**From Department:** {my_dept_name} (profile: `{my_profile}`)",
            f"**To Department:** {canonical_target_dept} (profile: `{target_profile}`)",
        ]
        if ticket_id:
            body_lines.append(f"**Ticket Ref:** `{ticket_id}`")
        body_lines.append(f"\n### Context & Details\n{context}")

        intake = target_info.get("intake")
        if intake:
            if isinstance(intake, str) and intake.strip():
                body_lines.append(f"\n### Department Intake ({canonical_target_dept})\n{intake.strip()}")
            elif isinstance(intake, list):
                intake_text = "\n".join(f"- {str(item)}" for item in intake if item)
                if intake_text.strip():
                    body_lines.append(f"\n### Department Intake ({canonical_target_dept})\n{intake_text.strip()}")

        body = "\n".join(body_lines)

        # Core's "running" means not parked: the task is born ready. Anything
        # other than "ready" falls back to blocked, never triage.
        if target_info.get("initial_status") == "ready":
            kanban_initial_status, default_real_status = "running", "ready"
        else:
            kanban_initial_status, default_real_status = "blocked", "blocked"

        dispatch_args = {
            "title": title,
            "body": body,
            "assignee": target_profile,
            "initial_status": kanban_initial_status,
        }
        if ticket_id:
            dispatch_args["idempotency_key"] = f"owners:{my_dept_name}:{canonical_target_dept}:{ticket_id}"

        try:
            res = ctx.dispatch_tool("kanban_create", dispatch_args)
            if isinstance(res, str):
                try:
                    data = json.loads(res)
                except Exception:
                    data = {"raw_output": res}
            elif isinstance(res, dict):
                data = res
            else:
                data = {"raw_output": str(res)}

            if isinstance(data, dict) and data.get("error"):
                return json.dumps({"ok": False, "error": data["error"]})

            task_id = data.get("task_id") if isinstance(data, dict) else None
            real_status = (data.get("status") if isinstance(data, dict) else None) or default_real_status
            subscribed = bool(data.get("subscribed", False)) if isinstance(data, dict) else False

            if real_status == "blocked":
                message = (
                    f"Handoff task {task_id or ''} for department '{canonical_target_dept}' "
                    f"(assignee: '{target_profile}') is in blocked status awaiting human review."
                )
            elif real_status == "ready":
                message = (
                    f"Handoff task {task_id or ''} for department '{canonical_target_dept}' "
                    f"(assignee: '{target_profile}') is ready for execution."
                )
            else:
                message = (
                    f"Handoff task {task_id or ''} for ticket '{ticket_id or ''}' exists on Kanban for "
                    f"department '{canonical_target_dept}' (assignee: '{target_profile}') with status '{real_status}'."
                )

            # Notify the target department, unless the task already existed past blocked/ready
            target_notify_url = target_info.get("notification_webhook")
            if target_notify_url and real_status in ("blocked", "ready"):
                _send_notification(target_notify_url, {
                    "event": "handoff_created",
                    "task_id": task_id,
                    "title": title,
                    "ticket_id": ticket_id,
                    "from_department": my_dept_name,
                    "to_department": canonical_target_dept,
                    "assignee": target_profile,
                    "status": real_status,
                })

            return json.dumps({
                "ok": True,
                "task_id": task_id,
                "to_department": canonical_target_dept,
                "assignee": target_profile,
                "status": real_status,
                "subscribed": subscribed,
                "message": message,
            })
        except Exception as e:
            logger.exception("Failed to dispatch kanban_create in owners handoff")
            return json.dumps({"ok": False, "error": f"Failed to dispatch kanban task: {e}"})

    return handoff_task


def make_task_completed_handler(ctx):
    """Observer for kanban_task_completed: tell the origin department its handoff is done."""
    def on_kanban_task_completed(task_id: str, summary: str | None = None, **kwargs: Any) -> None:
        # kanban_show resolves the board the same way the worker does (HERMES_KANBAN_DB, current board).
        try:
            data = json.loads(ctx.dispatch_tool("kanban_show", {"task_id": task_id}))
        except Exception as e:
            logger.warning("owners: could not read task %s: %s", task_id, e)
            return
        task = data.get("task") or {}
        meta = parse_handoff_body(task.get("body"))
        from_dept = meta.get("from_department")
        if not from_dept:
            return  # Not an owners handoff task

        logger.info("owners: handoff %s (ticket %s) done, %s -> %s",
                    task_id, meta.get("ticket_id"), from_dept, meta.get("to_department"))

        dept_info = (load_fleet(ctx.get_config("fleet_path")).get("departments") or {}).get(from_dept) or {}
        origin_notify_url = dept_info.get("notification_webhook")
        if origin_notify_url:
            _send_notification(origin_notify_url, {
                "event": "handoff_completed",
                "task_id": task_id,
                "title": task.get("title"),
                "ticket_id": meta.get("ticket_id"),
                "from_department": from_dept,
                "to_department": meta.get("to_department"),
                "assignee": task.get("assignee"),
                "summary": summary or task.get("result"),
            })

    return on_kanban_task_completed


def register(ctx):
    configured = ctx.get_config("fleet_path")
    if not load_fleet(configured):
        logger.warning("owners: no fleet.yaml at %s, nothing to inject",
                       configured or "~/.hermes/fleet.yaml or the plugin directory")

    def inject_charter(**kwargs):
        current_fleet = load_fleet(ctx.get_config("fleet_path"))
        if not current_fleet:
            return None
        charter = build_charter(current_fleet, ctx.profile_name)
        if charter is None:
            return None
        return {"context": "[Company charter]\n" + charter}

    ctx.register_hook("pre_llm_call", inject_charter)
    ctx.register_hook("kanban_task_completed", make_task_completed_handler(ctx))
    ctx.register_tool(
        name="handoff_task",
        toolset="owners",
        schema=HANDOFF_TASK_SCHEMA,
        handler=make_handoff_handler(ctx),
        description=HANDOFF_TASK_SCHEMA["description"],
        emoji="📋",
    )
