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

Implementation is in progress. The root Makefile is the executable interface;
only checks actually executed and reported may be treated as passing.

- Install: `make install` (isolated `.venv`, Python 3.11+; no global installation).
- Dev: `make dev` (FastAPI; configured PostgreSQL and owner credentials required).
- Test all: `make test`; local unit tests: `make test-unit`.
- PostgreSQL integration: `make test-postgres` requires an explicitly authorized disposable `TEST_DATABASE_URL` and uses an isolated generated schema.
- Test one: `make test-one TEST=services/api/tests/test_file.py`.
- Lint: `make lint`; format: `make format`; typecheck: `make typecheck`.
- Reference: `make reference-check`; both contracts: `make contract-check`; infrastructure static checks: `make infra-check`.
- Recovery and release safety tests: `make infra-test` (temporary files, hostile archives and fake transport/commands only; no database, SSH, cloud or systemd mutation); included in `make verify`.
- Full local verification: `make verify` (unit/static/reference tests only; no database connection, deploy or migrations).
- Source credential patterns: `make secret-check` (offline, no runtime credential reads); included in `make verify`.
- iOS metadata: `make ios-project-check` (no Swift compilation); included in `make verify`.
- iOS: `make ios-build`, `make ios-test` on a Mac with the required Xcode/SDK.
- Manual Ad Hoc: `make ios-doctor`, `make ios-release`, `make ios-inspect`, `make ios-devices`, `make ios-install IOS_DEVICE="device name or identifier"`. Requires the owner's paid Apple team and signing setup. Release archives/exports locally; install targets only the selected registered device. Neither is part of `verify` or CI. Optional `IOS_IPA` selects an existing IPA; `IOS_BUILD_NUMBER` overrides the generated build number. Signing/release/install helper tests use fake profiles and commands in `make infra-test`.
- Interactive iOS: `make ios-simulator` builds/opens a dedicated Simulator and issues an eight-hour test session through the owner's SSH access. It writes a separate synthetic test account to the selected production API; it is never part of `verify`.
- PostgreSQL integration tests require an explicit disposable `TEST_DATABASE_URL`;
  skipped PostgreSQL tests and unavailable Xcode checks are never reported as PASS.

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
