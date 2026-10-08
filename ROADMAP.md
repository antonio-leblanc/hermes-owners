# Roadmap

## Where this goes

Any company running Hermes installs the plugin, writes a charter, and its profiles work as departments: each one knows what it owns and hands the rest to the owner. If an idea proves itself here and belongs in Hermes itself, it moves to core and the plugin gets thinner.

## Done means

1. Real escalations go through the kanban instead of a human chat, each one carrying its ticket reference.
2. Every department of a real fleet is in the charter and can hand off to any other.
3. Adoption by real people is measured per department.

## Now: first real deployment (support → tech)

The flow: support hands a ticket to tech; tech's agent diagnoses right away without touching code and opens an issue; a human works the issue and the PR, then tells the agent to close; tech hands the ticket back to support, whose agent drafts the reply and a person sends it.

- **Hand back to the origin.** The receiving department hands the finished work back with the same ticket reference, and the origin owns the reply.
- ~~**First real escalation, end to end,** opened by a person on the support team.~~
- ~~**Check whether core already closes the loop.**~~ Core's `kanban_notify_subs` handles notification. The plugin now creates a return kanban task (↩️ Retorno) as fallback when no webhook is configured.

## Next

- Cap handoff chains and catch bot-to-bot loops, now that tasks can be born `ready`.
- Full charter of a real fleet: what each department owns, does not own, and where it escalates.
- Adoption counting rule, fixed before the dashboard: a human message is `role='user'` with `sessions.source` outside `cli, cron, kanban, acp, api_server, subagent, tool, recovered`, with an option to exclude admins.
- Dashboard: live org chart and adoption per department, read-only over each profile's `state.db` and the shared `kanban.db`. It proves the structure serves people; it is not the product.

## Later

- Install in minutes: docs, a charter walkthrough, entry in the plugin catalog.
- Demo mode that anonymizes users.
- **Route before the turn.** A decision model ([Laya](https://huggingface.co/blog/sora-2/laya-ai-model-how-it-works-run-it-locally-and-eval), open; Jev, API) on `pre_gateway_dispatch` answers "whose is this?" in milliseconds, before a full LLM turn. Laya needs fine-tuning, and every human-approved handoff is a labeled example for it.

## Open questions

- **A reopened ticket does not hand off again.** The idempotency key returns any non-archived task, `done` included. For now a human decides.
- **Name.** Provisional.

## Not doing

- Goals that flow down from management and reporting hierarchy.
- Budget circuit breakers: Hermes Business and `hermes-telemetry` cover cost.
- Skill management across profiles: Hermes syncs shared skills for multi-member orgs (`~/.hermes/skills/_org/`). A department hands off the request, not its skills.
