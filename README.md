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
| `services/api/` | FastAPI, PostgreSQL ledger, private password accounts, legacy Apple identity verification, migrations and APNs worker |
| `contracts/` | Active API contract and documented transport clarifications |
| `infra/` | Production Compose, HTTPS, encrypted backup and isolated restore tools |
| `.github/workflows/verify.yml` | Verification on pushes/PRs; main-only image publication and backend deployment |
| `.github/workflows/ios.yml` | Independent unsigned iOS build/tests; does not gate backend deployment |
| `Can_Developer_Handoff_v1.1/` | Unmodified source specification, acceptance criteria and calculation fixtures |

No categories, bank integrations, administrator role or multiple active cabinets are introduced. Amounts use integer minor units;
business dates use `Asia/Shanghai`. Version 2 preserves historical calculations;
version 3 adds topups fully to their effective day from each sprint's recorded cutoff.
Saving an amount and end date in the app starts a fresh budget from today. The
previous budget is completed atomically and remains in history with its entries;
its carry, topups and expenses do not enter the new calculation. The new budget's
amount is the complete starting bank, with no implicit transfer from the old one.
For example, 11,000 over 30 days starts at 366.66 today. The app labels this flow
"Новый бюджет" / "Начать" and previews the fresh calculation before confirmation.
Expenses may exceed today's allowance using the remaining bank, but cannot make
the total bank negative. The app checks drafts against the bank including local
pending entries; the server checks mutations under the cabinet lock. Corrections,
topup cancellations and balance edits cannot bypass this limit. Rejected commands
retain their rejection on replay. Existing historical negative balances are kept
and can be reduced, but cannot be made more negative by new entry mutations.
The forecast for tomorrow divides the bank after the draft expense by the number
of days after today; today's carry and same-day topups can make today's allowance
different from that future daily amount.

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

For the selected free delivery to two iPhones:

```sh
make ios-sideload
```

This produces `Can-SideStore.ipa` under ignored `build/ios-sideload/`. Import it
into SideStore and keep the widget extension; SideStore signs it using a free
Apple Account and periodically refreshes the seven-day signature. The app now
uses owner-provisioned login/password accounts, with no public registration or
Apple/APNs requirement for this build. See [one-time installation, automatic
refresh and limits](docs/IOS_DELIVERY.md). Actual installation and autonomous
refresh on the two iPhones still require device verification.

The previous paid Ad Hoc Make commands remain available as an optional path;
`ios-release` and `ios-install` do not implement free SideStore installation.

For an interactive owner-operated test before Apple activation, run
`make ios-simulator` or open `apps/ios/Run Simulator.command`. This creates a
separate synthetic account on the current API through owner SSH and imports an
eight-hour session into a dedicated Debug Simulator app. No public login route
is added. See the [Simulator instructions](apps/ios/README.md#интерактивная-проверка-на-mac-без-apple-developer).

## Run and release

For API development, configure an authorized PostgreSQL database and the required
`CAN_*` settings from `services/api/can_api/config.py`, apply the reviewed Alembic
migration to that database, then run `make dev`. Private password accounts are created/reset through `python -m
can_api.local_accounts` inside the configured API container. No email service
or paid Apple membership is needed. Legacy Apple login/APNs remain optional
and unavailable until real provider keys are added.
See the optional provider configuration in `infra/README.md`. There is no
fake-login route or seeded runtime budget.

Follow [production operations](infra/README.md) for the single-server deployment,
credential-file mounts, first migration, updates, backup and isolated recovery.
Follow [backend CI/CD activation](infra/CI_CD.md) for branch checks and automatic
`main` deployment. The owner selected `https://151.243.217.6` without a purchased
domain. On 2026-10-19 PostgreSQL, API, worker and Caddy were started and verified
healthy; the public readiness endpoint returned 200 with `recovery_mode=single_server`.
The initial database schema revision was 0002; current release readiness requires 0004. No backups or external storage are configured,
as explicitly selected by the owner. Apple login and APNs remain unconfigured
until the separate owner enrollment/key setup. Current automation results are
available in the repository's `Verify and deploy` workflow.
Keep credentials outside the repository. Follow [iOS instructions](apps/ios/README.md)
for owner signing and [two-phone delivery](docs/IOS_DELIVERY.md) for the selected free
SideStore path and its refresh requirements. Use the original
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
