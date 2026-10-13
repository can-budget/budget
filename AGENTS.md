# Repo — budget


## Context hierarchy

| Path | Read when |
|---|---|
| `../AGENTS.md` | Cross-repo work, understand role in budget product |
| `../../AGENTS.md` | Company workflow and secrets conventions |
| `../docs/ARCHITECTURE.md` | Deep product architectural context |
| `docs/design.md` | Repo-specific design decisions, patterns |
| `docs/adr/*.md` | Repo-level architectural decisions |

Rule: load parent / docs only when task requires.

Baseline rules live in `~/dotfiles/agent-profiles/BASELINE.md` and are loaded
through the agent profile. Platform references live in
`~/dotfiles/docs/platform/`; read them only when the task needs that workflow.

## What this repo does

TODO: 1 sentence — what is this service/library/app.

## Stack

TODO: auto-fill from manifest (`pyproject.toml` / `package.json` / `go.mod`).

- Language: <version>
- Framework: <if any>
- Key deps: <top 3-5>
- Database: <or "none">

## Commands

When present, `Makefile` is the canonical executable interface for humans,
agents, and `test-tui`. Document existing targets or package scripts; if no
commands are configured, say so. Propose a missing Makefile separately.

- Install: `<cmd>`
- Dev: `<cmd>`
- Test all: `<cmd>` · Test one: `<cmd>`
- Lint: `<cmd>` · Format: `<cmd>` · Typecheck: `<cmd>`
- Full verify (run before commit): `<lint && test && typecheck>`

## Testing contract

- Prefer stable Make targets: `install`, `dev`, `test`, `test-one`, `lint`, `format`, `typecheck`, `verify`.
- `verify` must be local and non-destructive: no migrations, deploys, broad cleanup, or external state mutation.
- When adding a new test suite, runner, package manager, or quality gate, update `Makefile` first and then update this `AGENTS.md` section.
- If no automated tests exist yet, document that fact here. Adding a Makefile or test target is a separate implementation task, not a prerequisite for filling context.
- Optional UI labels live in `.agents/commands.toml`; do not duplicate logic there if a Make target is enough.

## Platform references

- Git workflow: `~/dotfiles/docs/platform/git-workflow.md`
- Secrets/env: `~/dotfiles/docs/platform/secrets-env.md`
- Local infra (dev-stack): `~/dotfiles/services/dev-stack/README.md` (canonical; live list: `dev-stack urls`)
- ML/quant: `~/dotfiles/docs/platform/ml-quant-workflow.md`

## WikiPedik memory

- Read path is automatic when `docs/knowledge/hot.md` exists: SessionStart injects it into new Codex/Claude sessions.
- When you find a durable root cause, production gotcha, failed approach, reusable rule, informal decision, or cross-repo invariant, use `lesson-append` to append one structured capture to `docs/knowledge/_inbox.md`.
- Before the final answer, run a memory checkpoint: if this task produced a durable root cause, production gotcha, failed approach, reusable rule, informal decision, or cross-repo invariant, use `lesson-append`; if not, write nothing.
- Do not edit curated wiki pages directly (`lessons.md`, `gotchas.md`, `debugging-stories.md`, product/shared pages). Use `wiki sync can-budget/budget/budget` from the shell for curator review.
- If `docs/knowledge/_inbox.md` is missing, tell the user to run `wiki bootstrap can-budget budget budget`.
- Skip low-signal notes. A capture must explain what happened, why it happened, and the reusable rule.

## Key files

TODO: 5-10 files.

- Entry: `<path>`
- Routes / handlers: `<path>`
- Config: `<path>`
- Tests: `<path>`

## Local dev

TODO: document only observed local requirements, or explain what is not configured yet.

- Shared dev infra: use **dev-stack** when this repo needs shared services. `dev-stack connect` writes `.envrc` and may create a database; run it only as part of authorized environment setup, not context filling. Check an existing connection with `dev-stack doctor`.
- Required env: TODO (variable names from tracked examples/config, or confirmed none)
- Uses dev-stack: TODO (actual required services, none, or not decided)
- Port: TODO (actual port, or not applicable for a repo without a listening service)
- Run: TODO (existing command, or no runnable application configured)
- Health: TODO (existing check, or not applicable; do not assume an HTTP endpoint)

## Env contract

- Tracked env examples, when present, document required variable names without secret values.
- The secrets layer is being redesigned (`hq secret` is a stub). Creation
  does not provision a secrets backend; follow the company's chosen backend
  and document required variable names without values.
- Agents may document missing env vars, but must not write secret values to
  `.env*`, `.envrc`, docs, logs, or code.

## Non-obvious patterns

TODO: counter-intuitive things agent can't infer from code without reading 20 files.

Record only observed patterns with real source paths. If there is no code yet,
say so; if no special patterns were found, state the scope of that observation.

## Boundaries

🚫 Don't touch (без ask): `migrations/`, `vendor/`, `.env*`, repo-specific TODO
⚠️ Ask first: add new dependency, modify migrations

(Universal safety and workflow rules live in BASELINE.md. Read platform
references when this repo needs git, secrets, local infra, or ML/quant detail.)

## Lessons (organic)

(no entries yet)
