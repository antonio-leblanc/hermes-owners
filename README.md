# hermes-workforce

> Status: in progress. Not usable yet.

A [Hermes](https://github.com/NousResearch/hermes-agent) plugin that makes each profile in a fleet act as one department of a human company. A department knows what is its own. When a request is not, it hands it off for real: a task on Hermes' native kanban, with a trail of who asked and where it stands, and a human on the receiving side approving before anything runs. No second orchestrator beside Hermes.

## Why

Telling a bot what is not its job is half the problem. Without a way to pass the request on, it either tries to solve it anyway or says it escalated when nothing happened. Boundaries and handoff only work together.

## Scope

1. **Company charter:** a `fleet.yaml` defines departments, what each one owns and does not own, and where it escalates. It is injected into each profile's turn through the `pre_llm_call` hook.
2. **Handoff:** a tool that opens a kanban task for the department in `escalates_to`, carrying the source reference (a ticket ID, for example), for a human there to approve or take.

Later: a dashboard with the live org chart and per-department adoption by real people, as proof that the structure serves them.

See [`fleet.example.yaml`](./fleet.example.yaml) for the charter format.

## License

MIT
