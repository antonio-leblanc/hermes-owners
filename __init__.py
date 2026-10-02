"""hermes-workforce: injects the company charter into each profile's turn."""

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
    if dept.get("escalates_to"):
        lines.append(
            f"When a request is not yours, do not try to solve it: say it belongs to {dept['escalates_to']}. "
            "Only say you escalated or registered something if a tool call actually did it."
        )

    others = [f"- {k}: " + "; ".join(d.get("owns") or []) for k, d in departments.items() if k != mine]
    if others:
        lines.append("Other departments:\n" + "\n".join(others))
    return "\n".join(lines)


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
