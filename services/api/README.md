# Can API

FastAPI, SQLAlchemy 2 Core, psycopg and PostgreSQL. The runtime has no authentication bypass, test user login, in-memory ledger, SQLite fallback or financial floats. `can_api/budget.py` ports the supplied calculation-version-2 reference without changing its rules.

Run repository Make targets from the repository root. `make dev` starts the API. `python -m can_api.worker` runs the durable APNs worker with `services/api` on `PYTHONPATH`. Installation consumes the committed dependency pins; dependency updates must review and regenerate both lock files separately.

Migrations are explicit: `alembic -c services/api/alembic.ini upgrade head`. `upgrade head --sql` emits reviewable SQL without connecting to a database. Downgrade deliberately refuses to erase the financial ledger. Apply migrations only to an explicitly selected, authorized database.

## Configuration

All settings use the `CAN_` prefix. No secret files are bundled.

| Name | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL; no other database engine is supported |
| `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD_FILE` | Alternative mounted-secret database configuration; host enables this form |
| `ENVIRONMENT` | `production` by default |
| `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY_FILE` | Owner-controlled Sign in with Apple token exchange |
| `ENCRYPTION_KEY_FILE` | Fernet key for Apple refresh credentials, APNs tokens, invitation codes and short refresh retries |
| `INVITATION_HMAC_KEY_FILE` | Separate unpredictable invitation lookup key |
| `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY_FILE`, `APNS_APP_TOPIC` | App push provider configuration |
| `APNS_WIDGET_TOPIC` | Optional widget push topic ending in `.push-type.widgets`; absent means timeline/app-push fallback |
| `DELETION_JOURNAL_DIR` | Durable encrypted deletion journal, independent off-host signed checkpoints and acknowledgements; required to accept deletions |

Inline secret environment variables are also supported for managed secret injection: `APPLE_PRIVATE_KEY`, `ENCRYPTION_KEY`, `INVITATION_HMAC_KEY`, `APNS_PRIVATE_KEY`. The service never reads a dotenv file. Do not put their values in source, command history, issues or logs. Preserve encryption keys securely for restore; replacing an encryption key without re-encrypting existing ciphertext makes those credentials unavailable.

`/health/live` checks process availability. `/health/ready` checks migration revision 0002, pending deletions overdue by 24 hours, and the independently replicated journal checkpoint plus its success heartbeat (maximum age five minutes). Bootstrap the journal publisher before expecting readiness to pass. Readiness does not establish Apple enrollment, valid provider keys, APNs delivery, a signed application or access from the users' networks.

## Transactions and synchronization

Writes acquire the user lock, cabinet lock, then subordinate rows. Financial state, audit, cabinet revision, receipt and push outbox commit in one database transaction. FastAPI dependencies use function scope so commit finishes before an applied response can be sent. `/sync` locks the cabinet for a consistent snapshot plus one receipt per requested unresolved ID. No-op settings edits store receipts without increasing the cabinet revision.

`add_entry` requires the current `membership_id`, and the occurrence must belong to that membership. Rejoining never grants old unknown commands a new epoch. Already applied commands may return an ownership-checked limited receipt after leave; the API does not return a financial snapshot to an inactive member. `GET /v1/commands/{command_id}/receipt` checks only the signed-in user's receipt without executing the command or requiring cabinet membership. An old unknown command stays quarantined; this read cannot silently grant it a new membership epoch.

Recovery uses a new command ID and the same entry ID, sprint, amount and kind. `recovery_of_command_id` must refer to the user's terminally rejected `add_entry`. Date changes additionally require `original_occurred_at` matching that rejected request. This relationship is audited. A completed sprint requires explicit `allow_completed=true`. Never generate a new command for an unknown outcome; repeat or synchronize the original first.

History uses `(occurred_at,id)` cursors and returns its observed cabinet revision. On a changed revision the client refreshes pages. Voided records remain visible as tombstones. Entry revision history has a separate authorized read endpoint; current/previous editor names are never inferred from administrator roles.

The worker claims durable jobs with leases and fencing tokens, combines obsolete notifications, retries temporary failures and removes invalid device tokens. APNs sends occur outside ledger transactions. Tokens for app and widget delivery are separate; endpoint selection follows each signed installation's APNs environment. Push payloads contain no financial data. Push delivery is best effort.

## Verification and remaining release gates

`make verify` runs only local unit/static/reference checks. `make test` runs arithmetic parity, validation, genuine local JWT signature checks and transaction response-order tests, with PostgreSQL cases explicitly skipped unless `TEST_DATABASE_URL` is supplied. For database verification, run `make test-postgres` with that variable pointing to an explicitly authorized disposable database with permission to create schemas. The tests apply the actual Alembic migration twice, create an isolated generated schema and remove only that test schema afterward. They never substitute SQLite. They cover receipt replay, races, capacity, membership epochs, carry, recovery, refresh and widget revocation.

No live Apple account, APNs provider, PostgreSQL server or phone has been verified by the local unit suite. A passing unit suite is not production acceptance. External deployment, backup restore and the complete owner-account/device tests remain necessary.

## Account deletion and restoration

The approved R01 policy preserves financial operations, amounts, occurrence dates and calculation history, while erasing the account, name and identity links. `DELETE /v1/me` requires an active session, a fresh one-use `X-Reauth-Token`, an immutable `command_id`, and `confirm_shared_history_changes=true`. The accepted transaction revokes sessions/widgets, leaves all memberships, revokes authored invitations and archives a cabinet without remaining members. It does not modify financial facts.

The client must persist the command and original reauthentication proof before sending. An exact retry with that proof returns the same 202 response even after token expiry, session revocation and physical account erasure. This capability only reads an already accepted result; it cannot authorize a new anonymous deletion. The minimal accepted receipt has no user/Apple identifier and remains after private tombstone retention ends. A 401 is never proof of deletion. The 24-hour primary deletion deadline is an operational target: monitor pending jobs and deadline violations, including external journal and Apple failures.

The worker publishes an immutable Fernet-encrypted event containing only the old internal UUID and creation timestamp, request time, event ID and policy version. It waits for a signed checkpoint and acknowledgement proving an independently replicated copy. Then it scrubs authored and edited JSON audit copies and retained receipts, removes membership links and physically deletes the user and cascading private credentials/devices/sessions. Nullable author/editor fields represent a deleted participant; numeric history stays intact. Other members' receipts and financial state survive.

Every successful Apple code exchange retains its distinct refresh/access tokens encrypted. Deletion moves them into a private revocation queue. The worker sends bounded batches to Apple's revoke endpoint outside DB transactions; only HTTP 200, including an empty response for an already invalid token, confirms revocation. Network/configuration errors retry and cannot be reported as successful revocation. Local erasure need not wait for Apple availability once the restore journal is independently durable. A legacy account with no recoverable Apple token is erased with explicit `apple_token_unavailable_manual_revocation` state; the user can remove Can from their Apple Account's Sign in with Apple settings. Verified `account-deleted` notifications schedule the same policy for the incarnation predating the event; `consent-revoked` revokes only sessions and credentials issued before its signed event timestamp, preserving a later authorization of the same user. Both Apple's documented `payload` wrapper and the handoff's `signedPayload` alias are supported, exclusively.

Each completed job emits a signed, nonidentifying completion record. A later restore may extend its timestamps and retention deadline monotonically; signed completion updates never shorten them. Tombstone ciphertext is retained for 30 days after primary erasure and only purged after the infrastructure maintenance command has retired every older eligible backup and advanced the signed `restore_not_before` floor. Backend retention requires both age eligibility and absence from that signed current inventory; age alone never permits purge. The owner-operated, implemented journal maintenance workflow is documented in `infra/README.md`. It is not executed by local verification.

For an isolated database restored from backup, `python -m can_api.deletion_replay --journal-dir PATH` validates the complete signed external inventory before any write, targets exact UUID plus creation timestamp, scrubs matching old incarnations and revokes all restored sessions/widget credentials. It never matches by Apple subject, so a recreated account survives. It retains only encrypted old Apple tokens for the worker. `--check` performs a read-only validation and fails while an old account or any unfinished revocation job remains. `--export-pending` publishes recovered accepted jobs missing from the external inventory into a writable isolated journal; off-host replication and acknowledgement must follow. `--finalize` explicitly contacts Apple for one bounded batch of up to four revocations, without APNs or API ingress; repeat it while it exits 2 for pending work. Restore tooling must first verify the signed backup envelope and its snapshot-start time against `restore_not_before`, keep the restored API inaccessible, finish pending jobs and pass that check before promotion. Running this CLI on a live database is not supported.

Deletion unit tests cover real encryption/HMAC validation, immutable files, forged/incomplete inventories, exact replay capability, numeric-preserving JSON scrubbing and empty-body Apple revocation responses. Prepared PostgreSQL cases additionally cover physical erasure, shared budgets, lost responses, expired proofs, Apple outages, lease fencing, independent pre-deletion backup replay, recreated accounts, retention and Apple notifications. These PostgreSQL cases remain skipped until an explicitly authorized test database is supplied.

Apple references: [token revocation](https://developer.apple.com/documentation/signinwithapplerestapi/revoke-tokens), [account deletion guidance](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple), [server notifications](https://developer.apple.com/documentation/signinwithapple/processing-changes-for-sign-in-with-apple-accounts).
