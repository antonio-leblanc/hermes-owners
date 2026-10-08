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

Later: a dashboard with the live org chart and per-department adoption by real people, as proof that the structure serves them.

See [`fleet.example.yaml`](./fleet.example.yaml) for the charter format.

## License

MIT
