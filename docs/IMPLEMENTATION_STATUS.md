# Implementation verification — 2026-10-13

This report describes the current working checkout, not a deployed release.
The owner's current scope is personal/family use with one production server.
Permanent staging and commercial estimates are superseded by that instruction.
The owner has authorized finishing implementation here and deferred release,
installation and physical-device checks until the implementation is ready.

## Implemented source

- Native SwiftUI iPhone app, Apple authentication, shared-budget screens,
  invitations, history, immutable offline commands and explicit conflict recovery.
- Medium WidgetKit extension with separate read-only credentials and shared
  snapshots. The full app session remains in the app's private Keychain group.
- FastAPI/PostgreSQL API, transactional ledger/receipts/audit, membership epochs,
  Apple token verification, refresh rotation and an APNs outbox worker.
- Production Compose, HTTPS configuration, encrypted off-host backup, isolated
  restore tooling and owner release/recovery instructions.
- Account deletion under the approved preserve-ledger policy: durable jobs,
  identity erasure, Apple credential revocation, exact lost-response replay,
  independent recovery journal and scoped, resumable iPhone cleanup.
- CI definitions for Python, disposable PostgreSQL and unsigned Xcode checks;
  main-only GHCR publication and digest-based backend delivery after all gates.
  The receiver/deployer validate source, serialize releases, verify off-host
  backup before migration, check container/public HTTPS health and preserve
  failure state without an automatic SQL rollback. GitHub/VPS activation is
  documented in `infra/CI_CD.md`; no remote workflow has run in this session.

The original calculation rules and source handoff remain unchanged. The active
transport contract is in `contracts/openapi.yaml`; clarifications are documented
in `contracts/README.md`.

## Executed checks

Final `make verify` completed successfully in this Linux environment:

| Check | Observed result |
| --- | --- |
| Ruff | PASS |
| mypy | PASS, 14 backend source files |
| API unit tests | 293 PASS; 18 PostgreSQL cases deliberately deselected |
| Original calculation suite | 7 methods PASS, including 240 golden cases and randomized invariants |
| Original contract verifier | PASS, 48 schemas / 246 references / 23 operations |
| Active implementation contract | PASS, 50 schemas / 267 references / 25 operations |
| Infrastructure static checks | PASS, 2 Compose files, CI/CD policy, 7 shell scripts and infrastructure Python syntax |
| Infrastructure unit tests | 111 PASS: 35 journal protocol tests, 51 deployment safety tests and 25 transport/head-gate tests; all use temporary state and controlled transports |
| iOS project static checks | PASS, 3 targets, scheme, entitlements, icon and fixture identity; 25 XCTest methods registered |
| Recognizable credential-pattern check | PASS, 106 authored text files; this is a bounded pattern check, not proof that arbitrary secrets are absent |

The Python suite emits one Starlette TestClient deprecation warning about its
httpx adapter. It does not affect the recorded test result.

Additional executed checks:

- Offline Alembic SQL generation through revision 0002 and PostgreSQL syntax
  parsing with pglast: 65 migration statements and 158 constant application
  queries passed. Six dynamically composed queries were excluded from this
  final constant-query pass. No database connection or migration was executed.
- The credential checker rejected a synthetic token in a temporary fixture and
  did not include its value in the error output.
- `git diff --cached --check` passed for the local commit groups. Implementation
  and CI/CD tooling are committed through `5283203`; the following
  documentation commit records this report. No release build has been produced.
- Earlier in this implementation, an in-process `/health/live` check and Python
  calculation AST comparison passed. Swift calculation/fixture byte identity
  is also checked by the current project verifier.

Journal tests use a controlled object-store transport and temporary files. The
interoperability test invokes the real backend and host-tool protocol functions;
it does not perform a real off-host transfer. SQL parsing does not establish
transaction or migration correctness on a running database.

## Not yet verified

- PostgreSQL migration execution and all 18 database integration cases:
  `make test-postgres` requires an explicitly selected disposable database.
- Docker image build and actual Compose startup, DNS/TLS and production health.
- GitHub environment/branch rules, GHCR publication, dedicated SSH credentials,
  server bootstrap and a real main-to-production workflow run. The deploy tests
  use fake commands and temporary files, not a production Docker daemon.
- Swift compilation, the 25 XCTest methods, simulator behavior and two-phone
  offline/shared-budget acceptance. Run `make ios-build` and `make ios-test`
  with the Xcode/SDK described in `apps/ios/README.md`.
- Owner Apple membership, identifiers, signing, Apple login, APNs and TestFlight.
- A real encrypted backup/off-host transfer and isolated restore rehearsal.

No remote push, deployment, live migration or TestFlight upload was performed.
The bound Git broker reports no company Git mapping. Owner infrastructure and
Apple connections have not been made available to this task.
The owner's publication request was handled by creating local commits on
`agent/mvp-a31260f0`. Remote publication remains blocked by the missing HQ Git
connection; this managed environment does not permit a direct default-branch
push or merge. No remote URL or credential configuration was substituted.

## Account deletion: implemented decision and boundaries

The owner's final R01 decision is to preserve published financial operations
without the deleted account's name or identity links. Amounts, occurrence dates,
accepted bank corrections and carry remain unchanged. The earlier suggestion
to delete operations was superseded by that explicit correction.

- A fresh Apple reauthentication accepts one immutable deletion command,
  revokes app/widget access and schedules a durable job. The exact original
  proof can retrieve the same accepted receipt after the session expires or the
  account is erased. A 202 means accepted, not completed; a 401 is not proof of
  deletion. The published primary erasure deadline remains 24 hours.
- The worker erases account attribution from relational links and embedded
  audit/receipt copies, preserving other members and the shared financial
  facts. Encrypted Apple credentials are revoked in bounded retries; missing
  legacy credentials have an explicit manual-revocation fallback state.
- An independently replicated, authenticated deletion journal is required
  before primary erasure. Restore validates its complete latest inventory and
  targets the exact old user UUID and creation timestamp, preserving a later
  account created with the same Apple identity. Restored sessions are revoked;
  isolated replay/export/finalize/check runs precede API exposure.
- Retention requires both 30 days after erasure and verified retirement of older
  backups. The signed recovery cutoff uses snapshot-start time. Repeated restore
  can extend completion retention, never shorten it. Missing, corrupt, partial
  or regressed journal material blocks recovery. The owner must configure the
  journal timer and operate backup retention as described in the runbook.
- API readiness checks migration 0002, the authenticated journal's fresh
  replication status and overdue deletion jobs. These checks are implemented
  and unit-tested; production health and monitoring are not yet verified.
- The iPhone saves its replay proof before sending and its accepted marker before
  local erasure, so interrupted cleanup can resume. Cleanup targets only the
  account's own files and credentials. A changed user UUID after a later Apple
  login does not automatically erase the old offline queue: explicit local
  cleanup is available and preserves the new account's session/widget.

This closes the implementation of R01, not its end-to-end acceptance. Real
PostgreSQL erasure, Apple revocation, two-phone behavior and restored backups
still need the runtime verification listed above.

## Release references

- [Production, backup and recovery](../infra/README.md)
- [Backend CI/CD activation and retry](../infra/CI_CD.md)
- [Two-phone release choices and expiry](IOS_DELIVERY.md)
- [iOS configuration, signing and TestFlight](../apps/ios/README.md)
- [Backend setup and tests](../services/api/README.md)
- [Original acceptance criteria](../Can_Developer_Handoff_v1.1/docs/ACCEPTANCE.md)
- [Prepared release acceptance record](ACCEPTANCE_RECORD.md)

End-to-end acceptance remains open. Unit tests and static source review do not
substitute for the runtime checks above.
