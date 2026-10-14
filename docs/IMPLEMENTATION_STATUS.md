# Implementation verification — 2026-10-14

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
  documented in `infra/CI_CD.md`; remote workflow results remain unverified.
- Reviewed Ubuntu host-preparation helper for backup tools, deployment accounts,
  directory ownership and the root-owned release receiver. It preserves existing
  compatible accounts and does not create runtime credentials or start the app.

The original calculation rules and source handoff remain unchanged. The active
transport contract is in `contracts/openapi.yaml`; clarifications are documented
in `contracts/README.md`.

## Executed checks

`make verify` completed successfully at `9d16c5d` in this Linux environment.
After the existing-account compatibility fix in `b76ae3f`, `make infra-test`
and `make infra-check` passed again; the table includes those latest results:

| Check | Observed result |
| --- | --- |
| Ruff | PASS |
| mypy | PASS, 14 backend source files |
| API unit tests | 293 PASS; 18 PostgreSQL cases deliberately deselected |
| Original calculation suite | 7 methods PASS, including 240 golden cases and randomized invariants |
| Original contract verifier | PASS, 48 schemas / 246 references / 23 operations |
| Active implementation contract | PASS, 50 schemas / 267 references / 25 operations |
| Infrastructure static checks | PASS, 2 Compose files, CI/CD policy, 8 shell scripts and infrastructure Python syntax |
| Infrastructure unit tests | 125 PASS: 35 journal protocol tests, 51 deployment safety tests, 25 transport/head-gate tests and 14 host-preparation tests; all use temporary state and controlled commands/transports |
| iOS project static checks | PASS, 3 targets, scheme, entitlements, icon and fixture identity; 25 XCTest methods registered |
| Recognizable credential-pattern check | PASS, 108 authored text files; this is a bounded pattern check, not proof that arbitrary secrets are absent |

The Python suite emits one Starlette TestClient deprecation warning about its
httpx adapter. It does not affect the recorded test result.

Additional executed checks:

- Offline Alembic SQL generation through revision 0002 and PostgreSQL syntax
  parsing with pglast: 65 migration statements and 158 constant application
  queries passed. Six dynamically composed queries were excluded from this
  final constant-query pass. No database connection or migration was executed.
- The credential checker rejected a synthetic token in a temporary fixture and
  did not include its value in the error output.
- Git whitespace checks passed for the local commit groups. Implementation,
  CI/CD tooling and host preparation are committed through `b76ae3f`; the
  following documentation commit records this report. No release build has
  been produced.
- Earlier in this implementation, an in-process `/health/live` check and Python
  calculation AST comparison passed. Swift calculation/fixture byte identity
  is also checked by the current project verifier.

Journal tests use a controlled object-store transport and temporary files. The
interoperability test invokes the real backend and host-tool protocol functions;
it does not perform a real off-host transfer. SQL parsing does not establish
transaction or migration correctness on a running database.

## Observed VPS preparation

On 2026-10-14 the owner-enabled bound SSH target `budget` accepted commands with
its pinned identity. The VPS reports Ubuntu 24.04.5 LTS, Linux amd64, 2 CPUs,
1.8 GiB usable RAM, a 58 GiB system disk and no swap. Docker 29.9.0 and Compose
5.6.0 were already installed. Capacity under application load is not measured.

The reviewed helper at `b76ae3f` completed as a remote systemd job with
`Result=success`, `ExecMainStatus=0`, `ActiveState=active`, `SubState=exited`.
Independent read-only checks confirmed:

- Installed `age` 1.1.1-1ubuntu0.24.04.3 and `rclone`
  1.60.1+dfsg-3ubuntu0.24.04.6 from Ubuntu packages.
- Preserved existing `can-operator` UID/GID 1000, home and `/bin/sh`; Docker
  and `can-runtime` supplementary membership verified, runtime GID 10001.
- All 15 managed directory ownership/mode checks passed. The root-owned
  installed receiver's SHA256 matches the reviewed Git source.
- Docker and Compose commands work as `can-operator`.

An earlier preflight at `9d16c5d` refused the existing POSIX shell before package
or account changes. The compatibility fix accepts `/bin/sh` and `/bin/bash` and
has regression coverage. No existing SSH key, shell or password was replaced.
Production `runtime.conf` and the `current` release link remain absent; host
preparation is not application deployment.

## Not yet verified

- PostgreSQL migration execution and all 18 database integration cases:
  `make test-postgres` requires an explicitly selected disposable database.
- Docker image build and actual Compose startup, DNS/TLS and production health.
- GitHub environment/branch rules, GHCR publication, dedicated CI SSH credentials,
  application bootstrap and a real main-to-production workflow run. The deploy
  tests use fake commands and temporary files, not a production Docker daemon.
- Swift compilation, the 25 XCTest methods, simulator behavior and two-phone
  offline/shared-budget acceptance. Run `make ios-build` and `make ios-test`
  with the Xcode/SDK described in `apps/ios/README.md`.
- Owner Apple membership, identifiers, signing, Apple login, APNs and TestFlight.
- A real encrypted backup/off-host transfer and isolated restore rehearsal.

The bound broker successfully published `agent/mvp-a31260f0` through `619023f`
on 2026-10-14 (`published: true`). Its updated `--json-file` interface accepted
the 846,236-byte JSON request containing a complete Git bundle. This resolves
the earlier positional-argument `Argument list too long` failure. Large broker
requests must use a JSON file or stdin through `--json-file`, never a base64
bundle in a command argument.

The draft PR request returned `hosting request returned HTTP 422` without the
underlying hosting response. No PR URL or successful PR creation was confirmed.
The available bound operations did not expose Actions status; remote test runs
and their conclusions remain unknown. Check the repository's Actions view and
the published branch before merging. No direct remote or credential extraction
was used.

The connection permits task-branch push and draft PR creation, but prohibits
direct `main` push/merge. Its allowed prefixes also exclude the requested remote
`dev` branch; `dev` exists only locally. No application deployment, live migration
or TestFlight upload was performed.

The production domain, Apple settings/keys, independent backup destination,
registry pull credential and dedicated CI key still require owner provisioning
through an approved secure path. Server access alone does not configure these.

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
