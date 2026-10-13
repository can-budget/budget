# Implementation contract 1.1.1

`openapi.yaml` is the active API contract. The complete supplied v1.1 package in
`Can_Developer_Handoff_v1.1/` remains unchanged, including its calculation engine
and fixtures. These changes clarify transport and reconciliation; they do not
change budget rules.

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
- `DELETE /v1/me` requires explicit confirmation and fresh Apple reauthentication
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
