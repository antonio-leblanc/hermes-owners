# hermes-owners

> Status: in active development. First production deployment under way.

A [Hermes](https://github.com/NousResearch/hermes-agent) plugin where each department of a company gets an agent that knows what it owns. Talk to any of them: if the request isn't theirs, they hand it off to the owner, with a trail and a human deciding what changes.

People should not need to know which bot handles what. The bots should. Handoff is a task on Hermes' native kanban, with a trail of who asked and where it stands. No second orchestrator beside Hermes.

## Why

Telling a bot what is not its job is half the problem. Without a way to pass the request on, it either tries to solve it anyway or says it escalated when nothing happened. Boundaries and handoff only work together.

## Ownership follows the problem

Each team talks to its own agent. When a problem, or a step of it, belongs to another department, the agent hands it off and leaves the knowledge with that department's agent. Tech does not close the support ticket for the bug it fixed: it hands it back to support, who owns the reply to the customer.

## Scope

1. **Company charter:** a `fleet.yaml` (the `fleet_path` setting, under Settings ▸ Plugins in the Desktop; default `~/.hermes/fleet.yaml`, then plugin root) defines departments, what each one owns and does not own, where it escalates, and optional departmental intake guardrails. It is injected into each profile's turn through the `pre_llm_call` hook with mtime dynamic reload (no gateway restart required).
2. **Handoff:** a tool that opens a kanban task for the owner department (`escalates_to` suggested by charter, or any other department in the fleet), carrying the source reference (a ticket ID, for example) and target department intake rules in the task body, in `ready` or `blocked` status as configured by the destination department in `fleet.yaml`.
3. **Hand back on the same card:** when the receiving department finishes its stage, it hands the card back to the origin with `kanban_request_review`, instead of completing it. One request stays one card, and only the origin completes it. Hermes' native kanban notifications tell each side; the trail is the card and its event log.

The read-only Areas dashboard shows ownership and native handoff evidence. Later: per-department adoption by real people, as proof that the structure serves them.

See [`fleet.example.yaml`](./fleet.example.yaml) for the charter format.

## Areas dashboard

Install this repository as the native `owners` plugin (the repository root is
`~/.hermes/plugins/owners`, not a Python package). Enable it in the profile serving
the dashboard with `hermes -p <profile> plugins enable owners`, then restart
`hermes dashboard`. Open **Areas** at `/owners`. Native discovery can show the tab
while its API remains unavailable: user plugin routes require `plugins.enabled`
and must not be in `plugins.disabled` in the requesting profile. The host supplies
authentication; the bundle uses the native React/UI SDK and authenticated
`SDK.fetchJSON`, without its own token or settings UI.

Configure `fleet_path` in the native Owners plugin settings. The selected profile's
`plugins.entries.owners.settings.fleet_path` takes precedence over legacy `config`.
`${VAR}` and `${env:VAR}` references expand from the dashboard process environment;
unresolved references stay literal. An explicit missing path never falls back.
An unset or empty setting checks the global `~/.hermes/fleet.yaml`, then the plugin
root, exactly as the native Owners plugin does: neither the selected profile's
home nor a custom `HERMES_HOME` changes these default charter locations.
`fleet.example.yaml` is never used. Relative configured paths resolve against the
process working directory, not the selected home; prefer an absolute path when
bots and the dashboard run from different directories. The URL's `?profile=`
follows the dashboard's profile switcher; unknown identifiers and paths escaping
the shared profile root are rejected. Charters and status files are reread on
refresh, with no last-good fallback.

The company map is organized by **area**, with responsibilities and exclusions,
profile status/freshness, configured suggestions and separately observed handoffs.
Select an area by mouse or keyboard for its bots, pair summaries and related native
cards. Gateway state older than 120 seconds is unknown, not proof of an offline bot.
Fresh status is a reported snapshot, not a process-liveness probe; missing named
profile state is unknown, including profiles served only by a multiplexed gateway.

Only the native default-board SQLite database is read, using `mode=ro` and
`query_only`. Shared `kanban.db` follows the Hermes root (or operator-configured
`HERMES_KANBAN_HOME` / `HERMES_KANBAN_DB`); no database is created. Observations
cover at most the latest 1,000 board cards, with 50 handoff-card details and 100
transfer events per card. Limits and unavailable sources are explicit. Cards
must carry Owners **From Department** / **To Department** provenance. Titles,
customer context, ticket references, run output and event summaries are not
returned. Charter responsibilities are intentionally visible to authenticated
viewers; do not place secrets in them.

A historical `assigned` event transferring the destination profile to the origin,
or `review_requested` with destination implementer and origin reviewer, is
**observed handback evidence**. This is not a lifecycle policy: `done` alone is
not a handback and `blocked` alone is not stalled. Absent/malformed event history
leaves evidence unknown. Configured routes are suggestions, not enforced edges.
The profiles array wraps today's single profile per area; multi-profile routing
[#10](https://github.com/antonio-leblanc/hermes-owners/issues/10), lifecycle selection
[#16](https://github.com/antonio-leblanc/hermes-owners/issues/16), adoption/cost
[#19](https://github.com/antonio-leblanc/hermes-owners/issues/19), additional boards,
and the Desktop frontend are not implemented here. `dashboard/plugin_api.py`
exports the shared backend router for a future Desktop view.

Storage and web SDK contracts were checked against `hermes-agent` commit
`ee8dd6c886`. Validate locally without root packaging:

```bash
uv run --with pytest --with ruamel.yaml --with fastapi --with httpx pytest
node --check dashboard/dist/index.js
hermes plugins doctor . --ci
```

## License

MIT
