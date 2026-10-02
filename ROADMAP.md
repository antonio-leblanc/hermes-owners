# Roadmap

## Where this goes

Any company running Hermes installs the plugin, writes a charter, and its profiles work as departments: each one knows what it owns and hands the rest to the owner. If an idea proves itself here and belongs in Hermes itself, it moves to core and the plugin gets thinner.

## Done means

1. Real escalations go through the kanban instead of a human chat, each one carrying its ticket reference.
2. Every department of a real fleet is in the charter and can hand off to any other.
3. Adoption by real people is measured per department.

## Now: first real deployment (support → tech)

- **Hand off to the owner, not only to `escalates_to`.** Any department in the charter is a valid target, and `escalates_to` becomes the default the charter suggests. Every task is born `blocked` with a human approving, so a whitelist adds little safety. The README changes with the code.
- **Check whether core already closes the loop.** `kanban_create` called from a gateway session subscribes that chat to completion and block events. If the origin chat gets notified on completion, drop the webhook, the `kanban_task_completed` hook and the body parser.
- **Neutral message on a repeated handoff.** `kanban_create` does not say whether the task is new, so a ticket handed off again while still `blocked` must not read as "Successfully created".
- **Run on a production support profile**, not only a test profile.

## Next

- Full charter of a real fleet: what each department owns, does not own, and where it escalates.
- Adoption counting rule, fixed before the dashboard: a human message is `role='user'` with `sessions.source` outside `cli, cron, kanban, acp, api_server, subagent, tool, recovered`, with an option to exclude admins.
- Dashboard: live org chart and adoption per department, read-only over each profile's `state.db` and the shared `kanban.db`. It proves the structure serves people; it is not the product.

## Later

- Install in minutes: docs, a charter walkthrough, entry in the plugin catalog.
- Demo mode that anonymizes users.

## Open questions

- **A reopened ticket does not hand off again.** The idempotency key returns any non-archived task, `done` included. For now a human decides.
- **Skills across departments.** A department that hands off should not need its own copy of the owner's skills. Hermes Business already ships a team skill library, so the question is what is left for the plugin, if anything.
- **Name.** Provisional.

## Not doing

- Goals that flow down from management and reporting hierarchy.
- Budget circuit breakers: Hermes Business and `hermes-telemetry` cover cost.
