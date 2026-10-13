# Can production operations

This directory targets one owner-controlled production server. The user's later
instruction supersedes the handoff's permanent staging requirement. Disposable CI
databases and isolated recovery drills are not permanent staging environments.

These are executable configuration and recovery tools, not evidence of a deployed
service. Docker, DNS, TLS issuance, Apple credentials, live APNs and a real restore
must be validated on the owner's infrastructure before release.

## Runtime and credentials

`compose.yaml` runs FastAPI, a separate durable-outbox worker, PostgreSQL 18 and
Caddy. Only Caddy publishes ports 80/TCP, 443/TCP and 443/UDP. The database is on
an internal network. API and worker run as UID/GID 10001 with a read-only root
filesystem and no capabilities. PostgreSQL drops to its image's `postgres` user
after initializing the volume. Caddy retains only the bind-port capability.

Uvicorn accepts proxy headers only because its port is never published and its
Docker networks contain owner-controlled services. Caddy sets the upstream client
address so unrelated callers do not share a single proxy-IP rate-limit bucket.
Do not publish API port 8000 or attach untrusted containers to these networks
without restricting `--forwarded-allow-ips` to an explicit trusted proxy address.

Prerequisites: an owner-controlled Linux server with host Python 3.11+ available
as `/usr/bin/python3`/`python3`, `curl`, Docker Engine and Compose v2,
DNS pointing to it, inbound 80/443, outbound Apple/ACME access, synchronized clock
and disk monitoring. No package here provisions a server or firewall.

Set these **non-secret** variables through the operator's service manager or
interactive environment, without storing them in tracked files:

| Variable | Meaning |
| --- | --- |
| `CAN_RELEASE` | Reviewed source revision / fallback local image tag; keep it stable during recovery |
| `CAN_IMAGE` | Optional immutable GHCR image reference; required by automatic delivery, overrides the local tag |
| `CAN_DOMAIN`, `CAN_ACME_EMAIL` | Public API hostname and owner's certificate contact |
| `CAN_SECRETS_DIR` | Absolute owner-managed directory outside the checkout |
| `CAN_DELETION_JOURNAL_DIR` | Persistent private journal directory, normally `/var/lib/can-deletions` |
| `CAN_APPLE_CLIENT_ID`, `CAN_APPLE_TEAM_ID`, `CAN_APPLE_KEY_ID` | Owner app Bundle ID, team and Sign in with Apple key identifiers |
| `CAN_APNS_KEY_ID`, `CAN_APNS_WIDGET_TOPIC` | APNs key identifier; optional verified WidgetKit push topic for supported iOS versions |

The runtime reads files in `CAN_SECRETS_DIR`; secret values never belong in shell
arguments, Git, `.env` files, CI or application logs:

| File | Purpose |
| --- | --- |
| `database_admin_password` | PostgreSQL bootstrap superuser; separate from app access |
| `database_password` | At least 24 random characters for the non-superuser `can` role |
| `encryption_key` | Fernet key used to decrypt stored credentials; preserve with backups |
| `invitation_hmac_key` | Independent random invitation lookup key |
| `apple_private_key` | Owner Sign in with Apple signing key |
| `apns_private_key` | Separate owner APNs signing key |

Provision files via the owner's approved secret delivery mechanism. This repository
does not generate or contain those values. Keep the host directory inaccessible to
other users; mounted files must be readable by the service UIDs that receive them.
Standalone Compose mounts existing files: do not assume `uid`/`gid` remapping on
file-backed secrets. A host directory mode 0700 with individual files readable
through their container mounts is one usable operator arrangement. Validate this
without printing contents. Only declared services receive each secret.

The application role owns the application database so the explicit migration job
can create schema objects; it is not a PostgreSQL superuser. Database bootstrap
reads its password inside PostgreSQL. Initialization occurs only on an empty
volume. Changing a secret file does **not** rotate an existing PostgreSQL password;
rotation needs a coordinated owner-controlled database action and service restart.
Encryption-key replacement also requires data re-encryption or retained old keys.

## First authorized deployment

First provision and initialize the independent deletion journal as described below;
API readiness requires its verified heartbeat. Run from the repository root in an
environment containing the variables above.
`--env-file /dev/null` prevents implicit `.env` loading. `config --quiet` checks
interpolation without dumping settings. An unknown command failure is a stop.

```sh
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml config --quiet
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml build api
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml up -d --wait db
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml --profile maintenance run --rm migrate
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml up -d --wait api worker caddy
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml ps
curl --fail --silent --show-error "https://$CAN_DOMAIN/health/live"
curl --fail --silent --show-error "https://$CAN_DOMAIN/health/ready"
```

Migration is an explicit operator step; API/worker startup does not silently
migrate production. PostgreSQL readiness is connection readiness, API readiness
must also establish usable application schema. Worker health checks a heartbeat
updated only after a successful database cycle. Health does not prove Apple login,
push delivery, two-device synchronization or recovery acceptance.

Store deployed source revision, image IDs/digests, database migration revision,
Xcode/app build and recovery evidence in the release record. Image release tags
are fixed in source, but tags can be republished: record resolved digests and
review their replacement as a normal update. Build/install uses the committed
`services/api/requirements.lock`; never regenerate it on the production server.

The PostgreSQL 18 volume mount is `/var/lib/postgresql`, with versioned PGDATA
inside it. Do not reuse older image assumptions or change major versions by
editing the tag alone. Caddy's `/data` volume contains certificate state; persist
it across container replacements. Access logging is disabled by default; the API
also disables Uvicorn access logs. Do not turn on financial payload/token logging.

Account deletion uses the approved R-01 policy: retain the shared financial ledger,
remove the account and author identity/link. Durable encrypted tombstones identify
an account incarnation by UUID and creation time, never by Apple subject. The
worker must verify an authenticated off-host acknowledgment before completing a
normal deletion. It also retries Apple's credential revocation. Failure keeps the
job pending and health exposes overdue jobs; it never means deletion completed.

## Encrypted off-host backup

The operator installs/reviews `age`, `rclone`, `flock` and `sha256sum` on the backup
host. The production server stores only age public recipients, never the private
recovery identity. Off-host storage must be in the owner's account and a different
failure domain from this server. Keep recovery identities and application
encryption/HMAC keys in a separate owner-controlled recovery vault: a database
archive alone cannot recover encrypted credentials.

Additional non-secret variables:

| Variable | Meaning |
| --- | --- |
| `CAN_REPO_DIR` | Absolute release checkout directory |
| `CAN_BACKUP_DIR` | Private encrypted spool, e.g. `/var/lib/can-backup` |
| `CAN_BACKUP_RECIPIENTS_FILE` | Age public recipients file |
| `CAN_RCLONE_CONFIG_FILE` | Credential-file path for owner off-host storage |
| `CAN_BACKUP_REMOTE` | Dedicated off-host rclone `remote:path` containing only this database's backup objects |
| `CAN_DELETION_JOURNAL_REMOTE` | Separate dedicated off-host `remote:path` for the independent deletion journal |
| `CAN_ENCRYPTION_KEY_FILE` | Absolute path to the same application Fernet key; also authenticates recovery metadata |

```sh
bash infra/scripts/backup.sh
```

The script streams a consistent PostgreSQL custom dump directly into age, uploads
ciphertext, a SHA256 sidecar, the journal anchor and a signed `.recovery.json`
envelope. The envelope binds the ciphertext hash, journal anchor and conservative
snapshot start time recorded **before** `pg_dump`. It then downloads ciphertext
and metadata to verify them. A failed producer/upload/check never updates `last-success`.
Concurrent backups are excluded by `flock`; no plain database dump is written.
Failed `.partial` files are encrypted and retained for operator inspection.

`infra/systemd/can-backup.*` are opt-in units for a systemd host. They assume the
reviewed checkout at `/opt/can/current`, a `can-operator` account, a writable private
spool at `/var/lib/can-backup` and `/etc/can/runtime.conf` containing only the
non-secret variables above. Adapt paths before the owner installs/enables the
units. Docker-group membership grants host-level control: give it only to the
operator, not an API container. The timer runs twice daily with bounded jitter.

Configure owner alerts for failed service runs and `last-success` older than 24h,
disk space, HTTPS expiry, API failures and push backlog. `last-success` must be
read from persistent storage, not inferred from cron activity. Set remote storage
lifecycle to the approved 30-day backup retention. Keep database-backup credentials
restricted to upload/read when the provider supports it; journal retention needs
explicit delete permission for eligible journal objects.
The script deliberately performs no deletion. Local encrypted spool retention
requires a separately reviewed operator policy; monitor disk growth meanwhile.
Neither RPO <=24h nor RTO <=4h is proven until the owner performs timed drills.

## Independent deletion journal and bounded retention

The journal is independent of database snapshots: a restore downloads its current
signed inventory, including deletions accepted after the selected backup. Each
event is Fernet encrypted; inventory and completion metadata are HMAC authenticated
using a domain-separated key derived from the application encryption key. The
replicator uploads each event, verifies readback, commits and verifies the entire
inventory, then writes a local ACK referencing that signed inventory. A missing,
corrupt, unlisted, regressed or unavailable object blocks recovery. Replication
can finish an interrupted publication only when its unlisted remote event matches
the local producer; it never treats a partial inventory as complete. An orphan
without its producer requires an owner investigation, not deleting the object
or inventing an unsigned checkpoint. An absent
remote never means an empty journal. `initialize` is an explicit one-time action
and refuses a nonempty remote.

The owner provisions a `can-runtime` group with GID 10001 and gives `can-operator`
that supplementary group. Provision the journal root and `records`, `completed`,
`acks`, `checkpoints` children as owner `can-operator`, group `can-runtime`, mode
2770. Files are 0640; worker-created records inherit GID 10001, so the host can read
and remove them. API and migration mounts are read-only; only the worker writes
new events. This is private operational state, outside the checkout and backups.
Keep the encryption key readable only by the authorized operator/runtime.

Before first production startup, on the single journal/backup publisher host:

```sh
python3 infra/deletion_journal.py initialize
python3 infra/deletion_journal.py replicate
```

Then enable the reviewed `can-deletion-journal.timer`: it runs once per minute.
The backend detects stale journal status and overdue deletion jobs; monitor both
alongside systemd failures. The service uses the same non-secret runtime config as
the backup timer. `can-backup.service` also needs write access to the journal and
membership in `can-runtime`. Do not run competing publishers against one remote.
Use storage with verified read-after-write/list consistency; protect its account
and current inventory from rollback. Checkpoint signatures authenticate content,
not an unavailable provider or an externally restored obsolete `LATEST.json`.

Tombstones are retained at least 30 days after actual data deletion and while any
retained backup can restore that account. To retire eligible events, first apply
the owner's approved backup lifecycle and inspect any failed/orphan upload. Then
run the explicit maintenance command with the earliest permitted UTC snapshot
start boundary (replace the example with the reviewed actual boundary):

```sh
python3 infra/deletion_journal.py prune --backup-cutoff 2026-01-01T00:00:00Z
```

The command enumerates **all** objects under `CAN_BACKUP_REMOTE`, validates signed
backup envelopes and refuses missing/orphan/partial metadata or a retained dump
whose snapshot started before the proposed boundary. It never deletes database
backups automatically. It shares the host backup lock, preventing an older dump
in progress from being uploaded after the floor advances. A signed
`restore_not_before` floor is durably committed before eligible encrypted records,
completion metadata and obsolete inventories are erased locally and off-host.
The backend then removes the corresponding DB tombstone only for completed jobs
after the same age/floor checks. Crashes can be retried without resurrecting a
retired event. Object-store versioning/lifecycle must also expire noncurrent
versions under the approved policy; deleting a current object alone does not
physically purge provider-retained versions.

Backups older than that authenticated floor can never be restored by these tools,
even if their filenames or modification times change. Treat local spool copies
and recovery-vault copies as subject to the same boundary and erase expired copies
through the owner's approved retention process. A manual unsigned checkpoint or
an unchecked cutoff is not accepted as recovery proof.

## Isolated restore and verification

Download the selected archive plus `.sha256`, `.journal-checkpoint.json` and
`.recovery.json` sidecars. Obtain the age recovery identity and application
Fernet key through the owner's recovery vault. Set `CAN_RELEASE` to the reviewed
schema-current image, `CAN_ENCRYPTION_KEY_FILE`, `CAN_RCLONE_CONFIG_FILE` and the
same `CAN_DELETION_JOURNAL_REMOTE`. Set `CAN_RESTORE_JOURNAL_DIR` to a fresh absolute
child of a private owner-provisioned mode-2770 recovery directory with GID 10001.
The runtime UID must be able to read the mounted restore password/key files.

For a production recovery, stop the old API, worker and journal/backup timers
before making the recovery host the sole journal publisher. For a drill, arrange
an exclusive publisher window; do not race a production timer. The scripts do
not stop any live service or authorize a production switch.

Use a new isolated project and a separate database-password file for each drill:

```sh
sha256sum --check /absolute/path/can-TIMESTAMP.dump.age.sha256
bash infra/scripts/restore.sh /absolute/path/can-TIMESTAMP.dump.age can-restore-drill-001 /absolute/path/age-identity /absolute/path/restore-password
bash infra/scripts/replay-deletions.sh can-restore-drill-001 /absolute/path/restore-password "$CAN_RESTORE_JOURNAL_DIR" finalize
bash infra/scripts/verify-restore.sh can-restore-drill-001 /absolute/path/restore-password
```

Run checksum validation in the downloaded archive's directory. Before starting
the database, restore authenticates the archive envelope and its snapshot boundary,
fetches **the latest independent journal** and verifies every listed encrypted
object. It refuses stale/incomplete/unavailable journals. The target project must
have no existing containers or volumes; the explicit project flag overrides
`COMPOSE_PROJECT_NAME`. Its separate Compose file has an internal network and no
public ports. The dump is restored transactionally, then only this isolated target
receives the reviewed forward migrations and journal replay. Replay anonymizes the
ledger, removes matching old account incarnations and revokes **all** restored
sessions/widget credentials. A newly created account UUID cannot be deleted by an
old account's tombstone.

The script exports any accepted deletion jobs present in the dump but not yet in
the journal, then replicates them off-host before they can complete. If that step
fails, rerun `replay-deletions.sh ... export-pending`, followed by
`CAN_DELETION_JOURNAL_DIR="$CAN_RESTORE_JOURNAL_DIR" python3 infra/deletion_journal.py replicate`.
No API or general worker is started. Keep the target isolated after any error.

`finalize` is the only recovery operation that enables temporary outbound access
for Apple's revocation endpoint. It requires `CAN_APPLE_CLIENT_ID`,
`CAN_APPLE_TEAM_ID`, `CAN_APPLE_KEY_ID`, and `CAN_APPLE_PRIVATE_KEY_FILE`; values are
identifiers/file paths, never private key material in arguments. It processes one
bounded deletion batch without APNs. Exit 2 means pending or failed; retry after
the documented worker backoff. Never bypass it by marking jobs done. A missing
credential or Apple failure keeps recovery blocked. After finalization, replicate
the active production journal on promotion so completed metadata is preserved.

`verify-restore.sh` first checks every journal target and **all** pending DB deletion
jobs, then uses read-only transactions for aggregate row counts, ledger replay,
final-day bank consistency and member capacity. Encrypted completed tombstones
retained for their required retention period do not count as unfinished jobs.
No identity, financial amount or token is printed. Record migration revision,
image, inventory digest, aggregate output and drill duration in the release record.

Before promotion, also prove that an applied command receipt survives without a
second expense, compare selected projections on both phones and perform the
recovery-cache procedure below. Preserve the old volume until recovery is accepted.
There is no overwrite-production, drop-database or automatic promotion operation;
container/volume cleanup is a separate owner action. Passing static/unit checks
here does not establish a timed production restore or the RPO/RTO objectives.

A restored backup can have a lower cabinet revision than the confirmed cache on
a phone. The client's normal monotonic sync deliberately rejects that rollback.
After the owner identifies the restored backup boundary and accepts its potential
RPO loss, each phone has an explicit action in Settings → Synchronization →
"Обновить бюджет после восстановления сервера". Confirm it only for an announced
server recovery. It invalidates the confirmed cache and widget cache, preserving
the user's outbox, drafts and immutable command IDs, then reloads the server state.
If an acknowledged entry's receipt is absent from that restored snapshot, the
phone retains it for review rather than replaying it automatically. After checking
the restored history, "Повторить запись после восстановления" asks separately to
repeat the original command ID and payload. Reconcile these outcomes and compare
both phones before accepting promotion. This procedure has not been validated on
devices; restoring SQL and passing aggregate checks alone does not resolve it.
Do not erase app storage or recreate commands with new IDs as a workaround.

## Update and incident procedure

Before an authorized update: green CI, reviewed source/image, successful off-host
backup and previously successful restore drill. Stop API/worker for a migration
that cannot coexist with writes, apply only the reviewed forward migration, then
start the new image and verify health plus family smoke scenarios. Keep the last
working image available. Application rollback is allowed only if the previous
image supports the current schema. Do not downgrade a database automatically or
restore an old backup over new financial entries. Prefer a forward fix while
preserving the ledger.

If API is unavailable, phones keep their durable outbox. Do not erase app data,
retry commands with new IDs or reset the production database to clear an error.
Inspect health/container status and aggregate metrics without copying private
logs into issue trackers. If recovery is needed, use the isolated path above.

CI verifies every push/PR. After all checks pass, a push to `main` publishes a
backend image and deploys its digest through the restricted server receiver.
See [CI/CD setup and failure handling](CI_CD.md); account-side settings and initial
server bootstrap are required and have not been performed in this task.
Only the main deploy job receives its dedicated SSH key; there are no Apple
signing credentials or TestFlight uploads in this pipeline.
The disposable PostgreSQL service permits trust authentication only on runner
loopback; production uses separate file-backed credentials and SCRAM. The macOS
job selects the installed Xcode 26.3 to compile the widget push branch, builds
app/widget without signing, and runs Swift fixtures and domain tests; it is not
physical-device, signed archive or TestFlight acceptance.

## Upstream references

The PostgreSQL volume and secret-file rules follow the [official image guide](https://hub.docker.com/_/postgres).
Caddy configuration follows its [official image guide](https://hub.docker.com/_/caddy)
and [reverse-proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
File-backed secret handling follows [Docker Compose documentation](https://docs.docker.com/compose/how-tos/use-secrets/).
Recovery uses [PostgreSQL pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html),
[age](https://github.com/FiloSottile/age) and [rclone copyto](https://rclone.org/commands/rclone_copyto/).
The fixed Python image tag is listed in the [official Python image guide](https://hub.docker.com/_/python).
CI action commits were checked against [checkout v5.0.0](https://github.com/actions/checkout/releases/tag/v5.0.0)
and [setup-python v6.0.0](https://github.com/actions/setup-python/releases/tag/v6.0.0).
The Xcode selection follows the [macOS 15 runner image inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md).

Recovery networking follows [Docker multiple-network documentation](https://docs.docker.com/engine/network/).
