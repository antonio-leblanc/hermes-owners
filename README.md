# hermes-workforce

> Status: in active development. Works on a test profile.

A [Hermes](https://github.com/NousResearch/hermes-agent) plugin where each department of a company gets an agent that knows what it owns. Talk to any of them: if the request isn't theirs, they hand it to the owner, with a trail and a human approving.

People should not need to know which bot handles what. The bots should. Handoff is a task on Hermes' native kanban, with a trail of who asked and where it stands. No second orchestrator beside Hermes.

## Why

Telling a bot what is not its job is half the problem. Without a way to pass the request on, it either tries to solve it anyway or says it escalated when nothing happened. Boundaries and handoff only work together.

## Ownership follows the problem, tools follow the work

A department can use another department's tools to finish its own work: tech closes the support ticket for the bug it just fixed. When the problem itself belongs to another department, it hands off instead. `owns` in the charter is responsibility, not access.

## Scope

1. **Company charter:** a `fleet.yaml` (under `~/.hermes/fleet.yaml` or plugin root) defines departments, what each one owns and does not own, and where it escalates. It is injected into each profile's turn through the `pre_llm_call` hook with mtime dynamic reload (no gateway restart required).
2. **Handoff:** a tool that opens a kanban task for the department in `escalates_to`, carrying the source reference (a ticket ID, for example), in blocked status for human approval.
3. **Closed-loop resolution:** when a handoff task finishes on Kanban, the `kanban_task_completed` lifecycle hook captures the resolution summary, writes the durable audit log, and notifies the originating department.

Later: a dashboard with the live org chart and per-department adoption by real people, as proof that the structure serves them.

See [`fleet.example.yaml`](./fleet.example.yaml) for the charter format.

## License

MIT
