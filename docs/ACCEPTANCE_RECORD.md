# Release acceptance record

Prepared on 2026-10-13. This is a run sheet, not signed acceptance.
The owner has deferred release, installation and physical-device verification
until implementation is ready. Local evidence is recorded separately in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

The authoritative scenarios and expected results are in the unmodified
[acceptance specification](../Can_Developer_Handoff_v1.1/docs/ACCEPTANCE.md).
Execute the complete scenario for each AC; passing a related unit test does not
mark the full AC as passed.

## Run identification

Fill in before executing the release checks. Do not record credentials or raw
personal/financial data in this document.

| Field | Actual value |
| --- | --- |
| Source commit | Implementation and tooling: `046a92148e00ae1f3b60d781037d0c42f908af21`; replace with the exact release commit when executing this record |
| App version / build / archive | NOT RUN |
| API image digest / migration revision | NOT DEPLOYED |
| Environment / HTTPS origin | NOT CONNECTED |
| Apple signing team / entitlement review | NOT VERIFIED |
| Device A model / iOS | NOT PROVIDED |
| Device B model / iOS | NOT PROVIDED |
| Independent test Apple identities | NOT VERIFIED |
| Networks / RTT / measurement date | NOT MEASURED |
| Disposable PostgreSQL test run | NOT RUN |
| Isolated restore run / backup identifier | NOT RUN |

## Scenario results

For each run record the exact steps, synthetic input, expected result, actual
result and a sanitized evidence reference. Keep the status NOT RUN until that
scenario actually executes. A failing assertion is FAIL, never a presumed PASS.

| AC | Scenario | Required environment | Status | Evidence |
| --- | --- | --- | --- | --- |
| 01 | Apple login, cancellation, session expiry, restored device | Signed app, two Apple identities | NOT RUN | Pending |
| 02 | One to ten equal members, eleventh rejected | PostgreSQL and app | NOT RUN | Pending |
| 03 | Concurrent tenth-slot join and one-user create/join races | Disposable PostgreSQL | NOT RUN | Pending |
| 04 | Creator/member/last-member leave; revoked widget; old queue | PostgreSQL and two apps | NOT RUN | Pending |
| 05 | Invitation expiry, revoke, preview, environment and format | PostgreSQL and app | NOT RUN | Pending |
| 06 | QR share, brightness lifecycle, camera denial, duplicate frames | Two physical iPhones | NOT RUN | Pending |
| 07 | Sprint length and uniqueness, midnight during save | PostgreSQL and app | NOT RUN | Pending |
| 08 | Reference daily allowance, spend and next-day carry | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 09 | Shared first carry choice, stale preview and midnight | PostgreSQL and two apps | NOT RUN | Pending |
| 10 | Aggregated top-ups, floor, last day, negatives | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 11 | Fixed bank delta, unchanged entries, no-op and replay | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 12 | Bank and end-date changes saved atomically | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 13 | Effective date, preserved old carry, completed-sprint guard | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 14 | Concurrent purchase/edit and midnight conflict preserve input | PostgreSQL and two apps | NOT RUN | Pending |
| 15 | Late purchase with immutable delta and selected carry | PostgreSQL and app | NOT RUN | Local calculation evidence only |
| 16 | Multiple corrections, duration changes and historical as-of | PostgreSQL and native tests | NOT RUN | Python reference passed; Swift not executed |
| 17 | Concurrent entry correction/deletion and retained audit | PostgreSQL and two apps | NOT RUN | Pending |
| 18 | Independent concurrent expenses all survive | Disposable PostgreSQL and two apps | NOT RUN | Pending |
| 19 | Twenty retries, payload collision, lost commit response | PostgreSQL and app with controlled network loss | NOT RUN | Pending |
| 20 | Offline convergence, kill/reboot/update, storage failure | Two physical iPhones | NOT RUN | Pending |
| 21 | Snapshot/receipt reconciliation and in-flight new purchase | PostgreSQL and two apps | NOT RUN | Native cases prepared, not executed |
| 22 | Explicit recovery for completed sprint and wrong clock | PostgreSQL and app | NOT RUN | Native cases prepared, not executed |
| 23 | Clean/repeated migrations, constraints, rollback and races | Disposable PostgreSQL | NOT RUN | Offline SQL parsing only |
| 24 | Input, VoiceOver, Dynamic Type, history and archive | Two physical iPhones | NOT RUN | Pending |
| 25 | Installed medium widget under power/network/lock/day changes | Signed TestFlight app on two iPhones | NOT RUN | Pending |
| 26 | Apple/Keychain/APNs/widget scope, revoke and no sensitive logs | Signed app, PostgreSQL and Apple services | NOT RUN | Local validation evidence only |
| 27 | Separate permanent staging and production | Superseded by owner decision | NOT APPLICABLE | One production environment approved |
| 28 | Production reachable on users' actual networks without VPN | Production and both users' networks | NOT RUN | Pending |
| 29 | In-place app update preserves data; reproducible next release | Two successive signed builds | NOT RUN | Pending |
| 30 | Encrypted backup, isolated restore, deletion replay, owner access | Independent backup destination and isolated PostgreSQL | NOT RUN | Journal protocol unit tests only |
| 31 | Approved account deletion, anonymous ledger and Apple revoke | PostgreSQL, Apple and two iPhones | NOT RUN | R01 implementation and local tests complete |
| 32 | Owner handover, critical defects closed, next-release ownership | Owner review of completed evidence | NOT RUN | Pending |

## Account-deletion additions

The final owner decision preserves published operations, amounts, dates and
calculation history, and removes the deleted account's identity and attribution.
Logout and leaving a cabinet remain separate operations. Include these steps in
AC-30/31:

1. Lose the first DELETE response after commit. Repeat the exact original
   command/proof after session expiry and after account erasure; obtain the same
   accepted result without applying a new deletion. A 401 must not clear data.
2. Terminate the app around the accepted marker and local file/Keychain cleanup;
   unlock/relaunch and confirm cleanup resumes for only that account.
3. Verify published entries, numeric audit, bank and carry survive unchanged;
   author/editor links and embedded identity fields disappear. Check another
   member's receipts and history too.
4. Delay Apple and the off-host journal separately. Unconfirmed external work
   must not be reported as completed; stale journal/overdue deletion makes
   readiness fail. Resume and verify completion with real credentials.
5. Restore a backup from before deletion, using the latest independent deletion
   journal. Verify the old account is erased before API access resumes and a
   newly created account with the same Apple identity survives.
6. On the other phone, sign in after deletion has already finished. A new user
   UUID alone must not erase the old offline queue. Explicitly clear only the
   selected previous account and confirm the current session/widget still work.
7. Repeat restoration and verify completion retention can only extend. Reject
   missing, corrupt, incomplete or stale recovery material. Exercise documented
   backup expiration before retiring deletion records.

## Measurements and handover

The specified p95 goals remain unmeasured: local save/display at most 150 ms on
the weaker target iPhone; propagation to a second foreground app at most 5 s
under RTT at most 500 ms; API at most 300 ms excluding the external network.
Record sample count, network conditions, device/build and observed p95 for each.
Widget updates remain best effort.

Use the [iOS release instructions](../apps/ios/README.md),
[production and recovery runbook](../infra/README.md), and
[backend checks](../services/api/README.md). Confirm owner access in the actual
accounts; never copy signing keys, passwords or tokens into this record.

Owner acceptance: **NOT RECORDED**. Critical defects from future runtime checks
must be resolved and their affected scenarios rerun before acceptance.
