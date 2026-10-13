#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# != 4 ]]; then
  echo 'Usage: bash infra/scripts/restore.sh ARCHIVE.age can-restore-UNIQUE IDENTITY_FILE RESTORE_PASSWORD_FILE' >&2
  exit 64
fi
archive=$1
export CAN_RESTORE_PROJECT=$2
identity=$3
export CAN_RESTORE_ADMIN_PASSWORD_FILE=$4
[[ "$CAN_RESTORE_PROJECT" =~ ^can-restore-[a-z0-9][a-z0-9-]{2,40}$ ]] || {
  echo 'Only a new can-restore-* project is accepted.' >&2
  exit 64
}
[[ "$archive" == /* && "$identity" == /* && "$CAN_RESTORE_ADMIN_PASSWORD_FILE" == /* ]] || {
  echo 'Archive and credential file paths must be absolute.' >&2
  exit 64
}
[[ -r "$archive" && -r "$identity" && -s "$CAN_RESTORE_ADMIN_PASSWORD_FILE" ]] || exit 66
: "${CAN_RESTORE_JOURNAL_DIR:?Set a fresh absolute recovery journal directory}"
: "${CAN_RELEASE:?Set the reviewed schema-current recovery image tag}"
[[ "$CAN_RESTORE_JOURNAL_DIR" == /* && ! -e "$CAN_RESTORE_JOURNAL_DIR" ]] || exit 73
for tool in docker age python3 rclone; do command -v "$tool" >/dev/null; done
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
compose=(docker compose --project-name "$CAN_RESTORE_PROJECT" --env-file /dev/null -f "$root/infra/compose.restore.yaml")
docker info >/dev/null
[[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$CAN_RESTORE_PROJECT")" ]] || {
  echo 'Refusing an existing restore project, including stopped containers.' >&2
  exit 73
}
[[ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$CAN_RESTORE_PROJECT")" ]] || {
  echo 'Refusing existing restore data. Choose a new isolated project.' >&2
  exit 73
}
python3 "$root/infra/deletion_journal.py" fetch --archive "$archive" \
  --minimum-checkpoint "$archive.journal-checkpoint.json" --destination "$CAN_RESTORE_JOURNAL_DIR"
"${compose[@]}" up -d --wait --wait-timeout 120
age --decrypt --identity "$identity" "$archive" \
  | "${compose[@]}" exec -T --user postgres restore-db \
      pg_restore --username=postgres --dbname=can_restore --no-owner --no-acl \
      --exit-on-error --single-transaction
"${compose[@]}" exec -T --user postgres restore-db \
  psql --username=postgres --dbname=can_restore --no-psqlrc --set=ON_ERROR_STOP=1 \
  --command='SELECT version_num FROM alembic_version;'
bash "$root/infra/scripts/replay-deletions.sh" "$CAN_RESTORE_PROJECT" "$CAN_RESTORE_ADMIN_PASSWORD_FILE" "$CAN_RESTORE_JOURNAL_DIR" migrate
bash "$root/infra/scripts/replay-deletions.sh" "$CAN_RESTORE_PROJECT" "$CAN_RESTORE_ADMIN_PASSWORD_FILE" "$CAN_RESTORE_JOURNAL_DIR" replay
bash "$root/infra/scripts/replay-deletions.sh" "$CAN_RESTORE_PROJECT" "$CAN_RESTORE_ADMIN_PASSWORD_FILE" "$CAN_RESTORE_JOURNAL_DIR" export-pending
CAN_DELETION_JOURNAL_DIR="$CAN_RESTORE_JOURNAL_DIR" python3 "$root/infra/deletion_journal.py" replicate
echo "Archive restored into isolated project $CAN_RESTORE_PROJECT. No API or worker started."
echo 'Next: run replay-deletions.sh finalize until complete, then verify-restore.sh. API remains isolated.'
# Deliberately keep the isolated volume for verification; never drop production data.
