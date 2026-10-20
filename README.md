# Can / «Можно»

Native iPhone application and shared family-budget API, based on the supplied
v1.1 specification. The current deployment target is **one owner-controlled
production server**. The later owner instruction removes permanent staging and
commercial planning from the implementation scope.

The owner's 2026-10-19 decision is one VPS with no backups for this personal app.
The deployment explicitly uses `CAN_RECOVERY_MODE=single_server`; loss of the VPS
means loss of application data. Backend CI/CD and iOS builds are independent.

See [implementation and verification status](docs/IMPLEMENTATION_STATUS.md) for
executed checks and the remaining release work.

## Source layout

| Path | Purpose |
| --- | --- |
| `apps/ios/` | SwiftUI application, WidgetKit extension, shared calculation and durable offline state |
| `services/api/` | FastAPI, PostgreSQL ledger, Apple identity verification, migrations and APNs worker |
| `contracts/` | Active API contract and documented transport clarifications |
| `infra/` | Production Compose, HTTPS, encrypted backup and isolated restore tools |
| `.github/workflows/verify.yml` | Verification on pushes/PRs; main-only image publication and backend deployment |
| `.github/workflows/ios.yml` | Independent unsigned iOS build/tests; does not gate backend deployment |
| `Can_Developer_Handoff_v1.1/` | Unmodified source specification, acceptance criteria and calculation fixtures |

No categories, bank integrations, administrator role, alternative login or
multiple active cabinets are introduced. Amounts use integer minor units;
business dates use `Asia/Shanghai`. Version 2 preserves historical calculations;
version 3 adds topups fully to their effective day from each sprint's recorded cutoff.

## Local verification

Python 3.11 or later:

```sh
make install
make verify
```

Installation creates an isolated `.venv` with pinned dependencies. No system
packages are installed. The verifier runs the API unit tests, both contracts,
the original calculation tests, static analysis, recovery-journal tests,
recognizable credential-pattern checks and iOS project metadata checks.
The latter verifies files, targets, entitlements and
fixtures; it does not compile Swift.
`make verify` excludes database integration tests, even if database settings are
present. Run `make test-postgres` separately with an explicitly authorized,
disposable `TEST_DATABASE_URL`; this creates and removes its own isolated schema.
`make test` runs the complete suite and reports PostgreSQL cases as skipped when
that variable is absent. There is no fallback to the production database.

On a Mac with Xcode and an installed iPhone simulator:

```sh
make ios-build
make ios-test
```

`ios-test` selects an available iPhone simulator; `IOS_DESTINATION` can select
one explicitly. These commands do not upload a build or provision owner accounts.
Configure app identifiers, App Group, Keychain group and API URL using the iOS
configuration instructions before running against your server.

For an interactive owner-operated test before Apple activation, run
`make ios-simulator` or open `apps/ios/Run Simulator.command`. This creates a
separate synthetic account on the current API through owner SSH and imports an
eight-hour session into a dedicated Debug Simulator app. No public login route
is added. See the [Simulator instructions](apps/ios/README.md#интерактивная-проверка-на-mac-без-apple-developer).

## Run and release

For API development, configure an authorized PostgreSQL database and the required
`CAN_*` settings from `services/api/can_api/config.py`, apply the reviewed Alembic
migration to that database, then run `make dev`. Real Apple login requires owner
credentials. Backend infrastructure can start before Apple activation; provider
settings remain empty and login/push are unavailable until real keys are added.
See the optional provider configuration in `infra/README.md`. There is no
fake-login route or seeded runtime budget.

Follow [production operations](infra/README.md) for the single-server deployment,
credential-file mounts, first migration, updates, backup and isolated recovery.
Follow [backend CI/CD activation](infra/CI_CD.md) for branch checks and automatic
`main` deployment. The owner selected `https://151.243.217.6` without a purchased
domain. On 2026-10-19 PostgreSQL, API, worker and Caddy were started and verified
healthy; the public readiness endpoint returned 200 with `recovery_mode=single_server`.
Database schema revision is 0002. No backups or external storage are configured,
as explicitly selected by the owner. Apple login and APNs remain unconfigured
until the separate owner enrollment/key setup. Current automation results are
available in the repository's `Verify and deploy` workflow.
Keep credentials outside the repository. Follow [iOS instructions](apps/ios/README.md)
for owner signing and [two-phone delivery](docs/IOS_DELIVERY.md) for manual
TestFlight/Ad Hoc choices and their expiry requirements. Use the original
[acceptance checklist](Can_Developer_Handoff_v1.1/docs/ACCEPTANCE.md)
for the final two-phone run; the staging-specific requirements are superseded by
the owner's single-production decision.

This checkout is implementation work, not evidence of a released application.
Passing Python/static checks does not prove PostgreSQL concurrency, an Xcode
build, signing, APNs delivery, TestFlight installation or a successful live
restore. The approved account-deletion policy preserves shared operations,
amounts and dates while removing the deleted account's identity links. Deletion
uses a durable job and Apple credential revocation. A 202 response means accepted,
not completed. In the selected `single_server` mode the database is the only
durable store, and no backup or external-journal guarantee is made. Independent
recovery tooling is retained only for a future explicit switch to `off_host` mode.
