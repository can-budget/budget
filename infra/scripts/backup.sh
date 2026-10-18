#!/usr/bin/env bash
set -euo pipefail
umask 077

# Only public recipient material is required on the production server.
: "${CAN_REPO_DIR:?Set the absolute checked-out release directory}"
: "${CAN_BACKUP_DIR:?Set an absolute private backup spool directory}"
: "${CAN_BACKUP_RECIPIENTS_FILE:?Set the age public recipients file}"
: "${CAN_RCLONE_CONFIG_FILE:?Set the owner-managed off-host storage credential file}"
: "${CAN_BACKUP_REMOTE:?Set an off-host rclone remote:path, never a local path}"
: "${CAN_DELETION_JOURNAL_DIR:?Set the private persistent deletion journal directory}"
[[ "$CAN_BACKUP_DIR" == /* && "$CAN_REPO_DIR" == /* ]] || exit 64
[[ "$CAN_BACKUP_REMOTE" =~ ^[a-zA-Z0-9_-]+:[^:] ]] || {
  echo 'Backup destination must be an explicit configured off-host remote.' >&2
  exit 64
}
for tool in docker age rclone flock sha256sum python3; do command -v "$tool" >/dev/null; done
[[ -r "$CAN_BACKUP_RECIPIENTS_FILE" && -r "$CAN_RCLONE_CONFIG_FILE" ]] || exit 66
mkdir -p -- "$CAN_BACKUP_DIR"
exec 9>"$CAN_BACKUP_DIR/.backup.lock"
flock -n 9 || { echo 'A backup is already in progress.' >&2; exit 75; }

# A backup never bypasses a broken independent deletion journal.
python3 "$CAN_REPO_DIR/infra/deletion_journal.py" replicate

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$CAN_BACKUP_DIR/can-$stamp.dump.age"
[[ ! -e "$archive" && ! -e "$archive.partial" ]] || exit 73
python3 - "$CAN_DELETION_JOURNAL_DIR" "$archive.journal-checkpoint.json" <<'PY'
import json
import pathlib
import sys

root, target = map(pathlib.Path, sys.argv[1:])
status = json.loads((root / 'status.json').read_bytes())
checkpoint = root / 'checkpoints' / (status['checkpoint_sha256'] + '.json')
with target.open('xb') as stream:
    stream.write(checkpoint.read_bytes())
PY
compose=(docker compose --project-name can-production --env-file /dev/null -f "$CAN_REPO_DIR/infra/compose.yaml")
# pipefail prevents an empty/partial pg_dump from becoming a successful backup.
snapshot_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"${compose[@]}" exec -T --user postgres db \
  pg_dump --username=postgres --dbname=can --format=custom --no-owner --no-acl \
  | age --encrypt --recipients-file "$CAN_BACKUP_RECIPIENTS_FILE" >"$archive.partial"
mv -- "$archive.partial" "$archive"
python3 "$CAN_REPO_DIR/infra/deletion_journal.py" seal-backup \
  --archive "$archive" --minimum-checkpoint "$archive.journal-checkpoint.json" \
  --snapshot-started-at "$snapshot_started_at"
digest="$(sha256sum "$archive" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$digest" "$(basename "$archive")" >"$archive.sha256"
remote="${CAN_BACKUP_REMOTE%/}/$(basename "$archive")"
rclone --config "$CAN_RCLONE_CONFIG_FILE" copyto "$archive" "$remote" --immutable
rclone --config "$CAN_RCLONE_CONFIG_FILE" copyto "$archive.sha256" "$remote.sha256" --immutable
rclone --config "$CAN_RCLONE_CONFIG_FILE" copyto "$archive.journal-checkpoint.json" "$remote.journal-checkpoint.json" --immutable
rclone --config "$CAN_RCLONE_CONFIG_FILE" copyto "$archive.recovery.json" "$remote.recovery.json" --immutable
# Verify downloaded ciphertext, not only the upload exit code or remote metadata.
remote_digest="$(rclone --config "$CAN_RCLONE_CONFIG_FILE" cat "$remote" | sha256sum | cut -d ' ' -f 1)"
[[ "$remote_digest" == "$digest" ]] || { echo 'Off-host checksum mismatch.' >&2; exit 74; }
journal_digest="$(sha256sum "$archive.journal-checkpoint.json" | cut -d ' ' -f 1)"
remote_journal_digest="$(rclone --config "$CAN_RCLONE_CONFIG_FILE" cat "$remote.journal-checkpoint.json" | sha256sum | cut -d ' ' -f 1)"
[[ "$remote_journal_digest" == "$journal_digest" ]] || { echo 'Off-host journal anchor mismatch.' >&2; exit 74; }
envelope_digest="$(sha256sum "$archive.recovery.json" | cut -d ' ' -f 1)"
remote_envelope_digest="$(rclone --config "$CAN_RCLONE_CONFIG_FILE" cat "$remote.recovery.json" | sha256sum | cut -d ' ' -f 1)"
[[ "$remote_envelope_digest" == "$envelope_digest" ]] || { echo 'Off-host recovery envelope mismatch.' >&2; exit 74; }
printf '%s\n' "$stamp" >"$CAN_BACKUP_DIR/last-success.tmp"
mv -- "$CAN_BACKUP_DIR/last-success.tmp" "$CAN_BACKUP_DIR/last-success"
echo "Encrypted off-host backup verified: can-$stamp.dump.age"
# Retention is configured on the storage provider. This script deletes nothing.
