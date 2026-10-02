"""hermes-workforce: injects company charter and provides departmental handoff tool."""

import json
import logging
from pathlib import Path

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

FLEET_PATH = Path(__file__).parent / "fleet.yaml"


def _load_fleet() -> dict:
    """Read fleet.yaml next to the plugin. Never falls back to the example charter."""
    if not FLEET_PATH.exists():
        return {}
    return YAML(typ="safe").load(FLEET_PATH.read_text(encoding="utf-8")) or {}


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
    if escalates:
        escalates_str = ", ".join(escalates) if isinstance(escalates, list) else str(escalates)
        lines.append(
            f"When a request is not yours, do not try to solve it: hand it off to {escalates_str} using the `handoff_task` tool. "
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
        "Opens a task on the Hermes Kanban in blocked status (requiring human approval before execution) "
        "for the destination department's profile, carrying ticket and request details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_department": {
                "type": "string",
                "description": "Destination department name as defined in the company charter (e.g. 'tech'). Must match escalates_to.",
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
        fleet = _load_fleet()
        if not fleet:
            return json.dumps({"ok": False, "error": "No fleet.yaml found for workforce plugin."})

        departments = fleet.get("departments") or {}
        my_profile = ctx.profile_name
        my_dept_name = next((k for k, d in departments.items() if d.get("profile") == my_profile), None)

        if not my_dept_name:
            return json.dumps({
                "ok": False,
                "error": f"Current profile '{my_profile}' is not assigned to any department in fleet.yaml.",
            })

        my_dept = departments[my_dept_name]
        escalates = my_dept.get("escalates_to")
        if not escalates:
            return json.dumps({
                "ok": False,
                "error": f"Department '{my_dept_name}' has no escalates_to target defined.",
            })

        allowed_targets = [escalates] if isinstance(escalates, str) else list(escalates)
        allowed_targets_lower = {t.lower(): t for t in allowed_targets}

        to_dept_raw = (args.get("to_department") or "").strip()
        to_dept_key = to_dept_raw.lower()

        if to_dept_key not in allowed_targets_lower:
            return json.dumps({
                "ok": False,
                "error": (
                    f"Policy violation: department '{my_dept_name}' can only escalate to "
                    f"{allowed_targets}, not '{to_dept_raw}'."
                ),
            })

        canonical_target_dept = allowed_targets_lower[to_dept_key]
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
        body = "\n".join(body_lines)

        dispatch_args = {
            "title": title,
            "body": body,
            "assignee": target_profile,
            "initial_status": "blocked",
        }
        if ticket_id:
            dispatch_args["idempotency_key"] = f"workforce:{my_dept_name}:{ticket_id}"

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

            return json.dumps({
                "ok": True,
                "task_id": task_id,
                "to_department": canonical_target_dept,
                "assignee": target_profile,
                "status": "blocked",
                "message": (
                    f"Successfully created handoff task {task_id or ''} on Kanban for "
                    f"department '{canonical_target_dept}' (assignee: '{target_profile}') "
                    "in blocked status awaiting human review."
                ),
            })
        except Exception as e:
            logger.exception("Failed to dispatch kanban_create in workforce handoff")
            return json.dumps({"ok": False, "error": f"Failed to dispatch kanban task: {e}"})

    return handoff_task


def register(ctx):
    fleet = _load_fleet()
    if not fleet:
        logger.warning("workforce: no fleet.yaml at %s, nothing to inject", FLEET_PATH)
    charter = build_charter(fleet, ctx.profile_name)
    if fleet and charter is None:
        logger.info("workforce: profile %s is not in the charter, nothing to inject", ctx.profile_name)

    def inject_charter(**kwargs):
        if charter is None:
            return None
        return {"context": "[Company charter]\n" + charter}

    ctx.register_hook("pre_llm_call", inject_charter)
    ctx.register_tool(
        name="handoff_task",
        toolset="workforce",
        schema=HANDOFF_TASK_SCHEMA,
        handler=make_handoff_handler(ctx),
        description=HANDOFF_TASK_SCHEMA["description"],
        emoji="📋",
    )
