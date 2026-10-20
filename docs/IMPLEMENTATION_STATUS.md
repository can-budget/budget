# Implementation and deployment verification

## Live calculator and same-day topups — 2026-10-20

The owner approved a live draft preview, full-area Enter button, removal of the
approximately-equal glyph, and full same-day topups. Version 3 records a per-sprint
cutoff, preserving version-2 arithmetic for earlier days and closed legacy sprints.
Migration 0003 advances active cabinets once without rewriting entries or amounts.
The owner explicitly authorized the migration, GitHub publication and production
CI/CD deployment after checks. Deployment results will be recorded after CI.

Local checks passed: 44 XCTest methods (240 immutable original fixtures plus
12 shared v3 examples), 313 API unit tests, 186 infrastructure tests and all
contract/static checks. 21 PostgreSQL cases are delegated to disposable CI.
A temporary native XCUITest exercised nine points across the actual Enter
component and verified that a disabled control does not submit. No production
financial record was created by those tests.

## Backend activation — 2026-10-19

The owner explicitly selected one VPS with no backups or external storage for
this personal application and accepted rebuilding after server loss. Production
uses `CAN_RECOVERY_MODE=single_server`; external backup/journal timers are absent.
Apple enrollment, provider keys and iPhone delivery remain a separate stage.

Observed first healthy backend baseline:

- Source: `5a5afd814d567340c93dba58fc940c4312147432`, merged PR #2.
- Published image: `ghcr.io/can-budget/budget-api@sha256:a7eb291c1cd6c99065563e499ef42fbfa44489dc473211ba778b718d93884851`.
  Its Linux amd64/source identity was verified, then it was loaded over pinned
  SSH without storing a permanent registry credential on the VPS.
- The real PostgreSQL schema was bootstrapped to revision 0002. No user or
  financial fixture was seeded; the initial user count was zero.
- `can-production-db-1`, `api-1`, `worker-1` and `caddy-1` all reported healthy.
- Public HTTPS from the workstation returned 200 at `/health/ready` with
  `status=ok` and `recovery_mode=single_server`. PostgreSQL and API have no
  directly published host ports. Docker is enabled for startup.
- Runtime configuration is root-owned, readable by `can-operator` and contains
  paths/non-secret settings only. Existing internal keys were preserved.
- Backend and iOS verification are separate workflows. Backend publication is
  gated by API/static checks and real disposable PostgreSQL tests.
- Local verification passed 299 API unit tests, reference/contracts/static checks,
  and 178 infrastructure tests after the Compose-profile regression was added.
  Main CI passed all 19 PostgreSQL cases, including single-server deletion,
  preservation of money/history, exact accepted retries and retention cleanup.

The first update attempt exposed that real Compose excludes inactive profile
services from `config`. Validation now explicitly enables `maintenance` so it
can inspect `migrate`; the regression test rejects a migration image mismatch
before any pull or service stop. Current automatic delivery results are recorded
in the repository's `Verify and deploy` workflow. Initial bootstrap and a
successful automatic update are distinct evidence.

In this mode readiness still checks schema and overdue deletion jobs; durable
erasure preserves financial facts and removes identity without claiming an
independently replicated journal. The legacy 30-day deletion response is an upper
retention bound, not a claim that backups exist. The optional `off_host` behavior
remains the application default until an operator explicitly selects a mode.

## Historical implementation report — 2026-10-17

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

`make verify` completed successfully through `d36768f` in this Linux environment.
The table records that complete run:

| Check | Observed result |
| --- | --- |
| Ruff | PASS |
| mypy | PASS, 14 backend source files |
| API unit tests | 294 PASS; 18 PostgreSQL cases deliberately deselected and subsequently run on the VPS as described below |
| Original calculation suite | 7 methods PASS, including 240 golden cases and randomized invariants |
| Original contract verifier | PASS, 48 schemas / 246 references / 23 operations |
| Active implementation contract | PASS, 50 schemas / 267 references / 25 operations |
| Infrastructure static checks | PASS, 3 Compose files, CI/CD policy, 8 shell scripts and infrastructure Python syntax |
| Infrastructure unit tests | 162 PASS: 35 journal protocol tests, 88 deployment safety tests, 25 transport/head-gate tests and 14 host-preparation tests; all use temporary state and controlled commands/transports |
| iOS project static checks | PASS, 3 targets, scheme, entitlements, strict IP ATS/origin agreement, icon and fixture identity; 25 XCTest methods registered |
| Recognizable credential-pattern check | PASS, 109 authored text files; this is a bounded pattern check, not proof that arbitrary secrets are absent |

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
  CI/CD tooling, host preparation, IP HTTPS and validation fixes are committed through `d36768f`; the
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

## Observed public IP HTTPS — 2026-10-17

The owner selected HTTPS directly on `151.243.217.6`. The reviewed Caddyfile and
ingress-only Compose from `bf95dbe` are installed under
`/opt/can/bootstrap/ip-https-bf95dbe`. The running `can-production-caddy-1` uses the
production project, edge network and persistent certificate volumes. Backend,
worker and database containers have not been started.

- Docker pulled and ran Caddy 2.11.7. Recorded image digest:
  `sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f`.
- Actual Compose schema check, Caddy configuration validation and ingress startup
  passed. The container's administrative health check passed.
- A public Let's Encrypt certificate (issuer YE2) was issued with IP SAN
  `151.243.217.6`, valid from 2026-10-17 03:00:23 UTC to 2026-10-23 19:00:22 UTC.
  Initial validity is approximately 160 hours. These dates record the first
  certificate, not a fixed release configuration.
- Python/system trust and IP identity verification passed with TLS 1.3.
  An explicit OpenSSL handshake without SNI passed certificate verification.
- An HTTPS request from this task environment, outside the VPS, succeeded with
  `ssl_verify_result=0` and HTTP 503 at `/health/ready`. The 503 is expected while
  the real API is absent; API readiness has not passed.
- Live Caddy configuration confirmed the public ACME `shortlived` policy and the
  production certificate volumes. After a container restart, verified HTTPS
  served the same certificate fingerprint from persistent storage.

Caddy is configured for automatic renewal with the public ACME `shortlived`
profile and persistent volumes. A scheduled renewal cycle has not yet elapsed
or been observed. China without VPN and iPhone URLSession trust remain untested.
Both iOS targets now use the selected IP and exact ATS declarations preserving
HTTPS/TLS requirements. No production release-success marker was created.

## Not yet verified

- First production database bootstrap, complete application Compose startup and
  production API/worker/database health. The real isolated database tests and
  backend image build below do not constitute a production deployment.
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

Apple settings/keys, the independent backup destination, registry pull credential
and dedicated CI key still require owner provisioning through an approved secure
path. A domain and ACME contact email are no longer required for the selected IP
deployment. Server access alone does not configure the remaining external services.

## Real container and PostgreSQL verification — 2026-10-17

The authorized VPS built the production Dockerfile from a clean, SHA256-verified
archive of `d36768fc85d63dfd1a75479614398191c04e7764`. The new `.dockerignore`
allowlist limits normal builds to runtime API, package metadata/lock and migration
sources; local environments, credentials, tests and unrelated files are excluded.

- Built image: `can-api:d36768fc85d63dfd1a75479614398191c04e7764`;
  local image ID `sha256:3b2d32ed5bc1c2fa6409cb4e4244d5f6aeb06a08b4d7e3bf871920d3fad2e5c0`.
  This is a local image, not a published GHCR release digest.
- Verified Linux amd64, Python 3.13.16, non-root UID/GID 10001, installed API
  imports and packaged migration SQL. A disposable container using the real
  Uvicorn command, read-only filesystem, no network and no capabilities returned
  HTTP 200 at `/health/live`; missing dependencies correctly returned HTTP 503
  at `/health/ready`. No public application listener was enabled.
- PostgreSQL 18.6 image digest:
  `sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c`.
  An internal-only disposable network and tmpfs database ran all **18 integration
  tests: PASS, zero skipped/errors/failures**, in 9.168 seconds. The fixture applies
  the real migrations twice in each isolated schema. Apple calls and journal
  acknowledgments remain test doubles; no Apple or off-host recovery claim follows.
- The first run exposed one invalid test checkpoint: two separate clock reads put
  its recovery floor after its creation timestamp. `d36768f` uses one frozen test
  instant and adds a one-microsecond rejection case; production validation is
  unchanged. An intermediate test-container file-permission problem was corrected
  before the final successful run; failed job starts were not counted as passes.
- Final job `can-live-retest-d36768f`: `Result=success`, `ExecMainStatus=0`;
  test container exit 0, no OOM. JUnit evidence is retained at
  `/opt/can/validation/d36768fc85d63dfd1a75479614398191c04e7764/results/postgres-results.xml`.
  The temporary test containers/network were removed; no production schema or
  financial data was created. Alembic also emitted its legacy `path_separator`
  deprecation warning; tests still passed.
- Generated four independent internal secrets directly on the VPS in the private
  `/etc/can/secrets` directory: database admin/app passwords, application encryption
  key and invitation HMAC key. Values were not returned or stored in Git. Existing
  files are preserved. Owner recovery-vault copies have not yet been provisioned.

The owner confirmed an ordinary Apple ID only; paid Developer membership and real
Sign in with Apple/APNs keys are absent. Independent backup storage and its public
recovery recipient remain unspecified. `runtime.conf`, the healthy `current`
baseline, backup/journal timers, GHCR pull access and dedicated CI SSH delivery are
still missing. The private organization's GitHub environment availability must also
be checked as documented in `infra/CI_CD.md`. No main merge or automatic release was
performed, and the external API readiness endpoint still returns HTTP 503.

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
