"""hermes-owners: injects company charter and provides departmental handoff on a single kanban card."""

import json
import logging
from pathlib import Path

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

        # "delegate": the receiver completes the card and the native subscription
        # tells the origin. Anything else is a stage: request_review ends it and
        # reassigns the card to the origin, so one request stays one card.
        mode = "delegate" if target_info.get("handoff") == "delegate" else "stage"
        if mode == "delegate":
            body_lines.append(
                f"\n### Completion\n"
                f"This card is a delegation from {my_dept_name}. When the work is done, call "
                f"`kanban_complete` with a summary of what you did. Native notifications require "
                f"a subscription; do not assume {my_dept_name} will be notified."
            )
        else:
            body_lines.append(
                f"\n### Hand back\n"
                f"This card is one stage of a request from {my_dept_name}. When your stage is done, "
                f"do not call `kanban_complete`: call `kanban_request_review` with reviewer `{my_profile}` "
                f"and a summary of what you did. Only {my_dept_name} completes the card."
            )

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

            warning = None
            if mode == "delegate" and not subscribed:
                warning = (
                    "No native notification subscription confirmed. Arrange an explicit native "
                    "Kanban subscription or monitor the card; completion may not notify the origin."
                )
                message += f" Warning: {warning}"

            return json.dumps({
                "ok": True,
                "task_id": task_id,
                "to_department": canonical_target_dept,
                "assignee": target_profile,
                "status": real_status,
                "mode": mode,
                "subscribed": subscribed,
                "warning": warning,
                "message": message,
            })
        except Exception as e:
            logger.exception("Failed to dispatch kanban_create in owners handoff")
            return json.dumps({"ok": False, "error": f"Failed to dispatch kanban task: {e}"})

    return handoff_task


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
    ctx.register_tool(
        name="handoff_task",
        toolset="owners",
        schema=HANDOFF_TASK_SCHEMA,
        handler=make_handoff_handler(ctx),
        description=HANDOFF_TASK_SCHEMA["description"],
        emoji="📋",
    )
