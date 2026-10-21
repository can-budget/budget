# Implementation contract 1.4.0

`openapi.yaml` is the active API contract. Version-2 calculation engines and
their unchanged fixtures live in `reference/calculations/`; the historical
transport schema lives in `reference/contracts/`. Transport clarifications are listed below. The owner
approved an additional same-day topup policy on 2026-10-20.

- Cabinet timezone is fixed to `Asia/Shanghai`, as required by the product rules.
- `User.active_membership_id` and `Member.membership_id` identify the membership
  epoch. Every `add_entry` records that epoch. An expense created before leaving
  cannot be silently submitted after rejoining, even to the same cabinet.
- A sent command is immutable. Recovery after an authoritative rejection creates
  a new command with `recovery_of_command_id`. An explicitly corrected date also
  retains `original_occurred_at`. The server validates the link and preserves the
  original receipt. Unknown outcomes must be resolved before recovery is offered.
- Receipt schemas enforce status consistency: applied has a result; rejected has
  an error; unknown has neither. Clients must additionally match command IDs and
  reconcile against the revision of the snapshot from the same sync response.
- Entries include nullable `author_name` and `last_editor_name` so that current
  members can read history attributed to a former member. The entry revision
  endpoint returns the before/after audit and actor name under the same
  current-membership authorization as entry history. Financial audit values are
  immutable; identity links are removed under the approved deletion policy.
- `GET /v1/commands/{command_id}/receipt` reads only the authenticated user's own
  outcome. It does not execute the command or return shared budget data. Clients
  use it to check quarantined intentions after a membership change; `unknown`
  never grants permission to delete an unresolved financial intention.
- The owner's final R-01 decision preserves shared financial operations without
  the deleted account's name or identity links. Amounts, dates, accepted bank
  adjustments and carry choices do not change. Identity removal also covers
  embedded audit/receipt data, credentials, memberships and private device data;
  a nullable `author_id` alone does not implement the policy.
- `DELETE /v1/me` requires explicit confirmation and fresh same-user reauthentication
  on first acceptance. A 202 acknowledges the durable deletion job, not its final
  completion. The original command ID, body and reauthentication proof allow an
  exact accepted-request retry after session revocation. That narrow capability
  cannot authorize new deletion or read a budget. Clients persist it securely
  before sending, and never interpret a generic 401 as deletion confirmation.
- Deletion recovery records must survive independently of the database backup.
  Restoration applies retained records before opening the service, preserving
  financial data and removing the old account incarnation's identity links.
- Apple server notifications accept the documented `payload` wrapper and the
  original handoff's `signedPayload` alias, exactly one per request. The verified
  `account-deleted` event schedules the same preservation policy for the account
  incarnation that existed at the event time. `consent-revoked` revokes access
  without erasing financial history or treating it as an account deletion.
  See [Apple's notification format](https://developer.apple.com/documentation/signinwithapple/processing-changes-for-sign-in-with-apple-accounts).

`make contract-check` validates both the original handoff and this contract with
date/UUID format checking and negative receipt/recovery cases. PostgreSQL and
native client tests cover behavior beyond JSON Schema validation.

## Same-day topups — calculation version 3

Version 3 adds the entire topup to its effective day's allowance. Bank accounting,
carry decisions, signed bank corrections and integer rounding remain unchanged.
Sprint `topups_today_from` is the cutoff: earlier days replay version 2 arithmetic;
closed legacy sprints stay version 2. Migration 0003 upgrades active sprints at
the current Shanghai business date and advances their cabinet revisions. No entry,
amount, receipt or carry decision is rewritten. New sprints start at version 3.
Clients must use the supplied version and cutoff for both local previews and
pending projections. A draft never enters the queue or widget snapshot; Enter
queues exactly one immutable entry. Both versions remain accepted by WidgetKit.

The original 240 version-2 fixtures remain unchanged. Shared `today_topup_cases.json`
adds 12 version-3 examples used by both Python and Swift tests.

## Bank spending limit

The owner's 2026-10-20 correction allows spending more than today's allowance,
but not more than the remaining total bank. Entry mutations run under the
cabinet lock and reject with `insufficient_funds` (409) if their resulting bank
would be negative. The same rule covers corrections and cancellations of topups;
explicit bank targets must be nonnegative. A rejection rolls back the mutation,
preserves the cabinet revision and is retained as an immutable rejected receipt.
Old negative ledger balances remain readable and may be improved, but new entry
mutations cannot deepen them. Neither historical calculation engine is clamped.
The client validates against its projection including pending entries and marks
an excessive draft as invalid without changing its amount or enqueuing it.

## Fresh budget on amount/date save

The owner's clarified rule on 2026-10-20 is that saving a new amount and end date
starts a new process from the current Shanghai day. The app now sends
`create_sprint` with `replaces_sprint_id`, `expected_business_date` and the previewed
cabinet revision. Under the cabinet lock, the server completes that specific
budget using its existing `closed_at` field and inserts a new version-3 budget in
one command savepoint. A stale revision/date, conflicting ID or invalid input
leaves the previous budget active. Receipt replay cannot create a second budget.
Legacy creates without a replacement ID still reject an active budget.

The new bank is exactly `initial_minor`. Carry decisions, topups, expenses and
adjustments belong to the previous sprint ID and are never inherited. Explicitly
completed budgets are listed as completed even when their original planned end
date is in the future. Their projections stop at the completion day and their
original entries and planned dates remain intact. Delayed old entries require
completed-budget confirmation and an occurrence time no later than completion;
they affect only that archived budget. Its settings/carry cannot be reopened.

The client waits for the confirmed snapshot before forgetting the durable start
intent. A snapshot switching budgets clears the previous draft and excludes old
queued entries from the new budget while preserving their immutable commands.
No schema migration or automatic reset of an existing budget is performed.

## Private password login

The owner selected free SideStore delivery on 2026-10-20 and approved migration
0004. `POST /v1/auth/password` accepts an owner-provisioned login and password and
returns the existing LoginResult/session/reauthentication format. It does not
register accounts. Existing Apple routes remain compatible for legacy clients;
the current iPhone UI uses password login, including account-deletion confirmation.

Logins are canonical ASCII strings; passwords are salted PBKDF2-HMAC-SHA256 hashes
with 600,000 rounds. Unknown and wrong credentials share the same error and KDF
cost. Failed attempts consume separately committed IP, login and global limits.
Password reset revokes sessions/widget tokens and old reauthentication proofs;
login serializes against reset/deletion and rechecks the credential version.
Migration 0004 adds local_credentials and permits NULL apple_subject; existing
accounts and financial records are unchanged. Account erasure cascades to local
credentials without requiring an Apple revocation for a password-only account.
