# AGENTS.md

Guide for any agent (Claude Code, Hermes, others) working in this repo.

## What this is

A Hermes plugin that makes a fleet of profiles work as the departments of a human company, with no second orchestrator beside it. Scope and charter format: [`README.md`](./README.md) and [`fleet.example.yaml`](./fleet.example.yaml). Hermes internals the plugin relies on: [`docs/hermes-internals.md`](./docs/hermes-internals.md).

## Layout

- `plugin.yaml` + `__init__.py`: the native plugin. The repo root is the plugin directory (`~/.hermes/plugins/owners`).
- `fleet.example.yaml`: fictional company (Acme Solar). A real `fleet.yaml` lives only in the local install and is git-ignored. Without it the plugin injects nothing; it never falls back to the example.
- `docs/hermes-internals.md`: study notebook, in Portuguese, pinned to a `hermes-agent` commit.
- `ROADMAP.md`: where the project goes and what comes next. Check it before proposing scope; anything under "Not doing" needs a decision first.

## Rules

- **Public repo.** No real company, person, profile name, user id or data from a real `state.db`. Examples use Acme Solar only.
- **Public SDK surface only.** `register(ctx)`, `ctx.register_hook`, `ctx.register_tool`, `ctx.profile_name`, `ctx.dispatch_tool`. No imports from Hermes internals (`hermes_cli.*`, `hermes_yaml`, ...). Third-party libs Hermes already ships are fine (`ruamel.yaml`).
- **Manifest matches code.** Every hook or tool in `plugin.yaml` is registered unconditionally in `register(ctx)`.
- **Tests guard rules, not syntax.** Write a test only for a rule that could silently break: who may escalate to whom, idempotency, who gets told when a handoff ends. No tests that restate a string template, a parser on its own happy path, or the standard library. Few tests, each one worth reading.
- **Validate before committing:** `hermes plugins doctor . --ci`.
- **Never test on a production profile or through a running gateway.** Use a test profile and `hermes -p <profile> chat -q "..."`.
- **Facts about Hermes come from code, with the commit.** Clone `hermes-agent` locally and grep; do not fetch files one by one through the GitHub API.
- **Review PRs locally:** fetch the branch and read it on disk.
- **Issues, PRs and comments are short.** Problem in two or three lines, proposal in one or two. No headings, no background the code already shows, no list of everything checked. If a sentence can go without losing a decision, it goes.
