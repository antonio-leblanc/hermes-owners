# Roadmap

## Where this goes

Any company running Hermes installs the plugin, writes a charter, and its profiles work as departments: each one knows what it owns and hands the rest to the owner. If an idea proves itself here and belongs in Hermes itself, it moves to core and the plugin gets thinner.

## Done means

1. Real escalations go through the kanban instead of a human chat, each one carrying its ticket reference.
2. Every department of a real fleet is in the charter and can hand off to any other.
3. Adoption by real people is measured per department.

## Now: first real deployment (support → tech)

The flow: support hands a ticket to tech; tech's agent diagnoses right away without touching code, opens an issue and blocks the card; a human fixes it and closes the issue; tech hands the same card back to support, whose agent drafts the reply and a person sends it and completes the card.

- **First real escalation, end to end,** on one card, opened by a person on the support team.
- **One-shot delegation** ([#16](https://github.com/antonio-leblanc/hermes-owners/issues/16)): the receiver completes the card and native notifications tell the origin, with no hand back. Comes with its first real case.
- **Ownership by repository, not only by topic.** A charter that says fixes belong to tech sent a merge on support's own repository to tech. The charter has to name which repositories each department owns.

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
