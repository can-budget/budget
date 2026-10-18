#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: bash infra/scripts/verify-restore.sh can-restore-UNIQUE RESTORE_PASSWORD_FILE' >&2
  exit 64
fi
project=$1
password_file=$2
: "${CAN_RELEASE:?Set the image tag matching the restored database release}"
[[ "$project" =~ ^can-restore-[a-z0-9][a-z0-9-]{2,40}$ ]] || exit 64
[[ "$password_file" == /* && -r "$password_file" ]] || exit 66
: "${CAN_RESTORE_JOURNAL_DIR:?Set the verified recovery journal directory}"
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
bash "$root/infra/scripts/replay-deletions.sh" "$project" "$password_file" "$CAN_RESTORE_JOURNAL_DIR" check
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --network "${project}_isolated" \
  --mount "type=bind,source=$password_file,target=/run/secrets/restore_password,readonly" \
  --mount "type=bind,source=$root/infra/verify_restore.py,target=/verify_restore.py,readonly" \
  --env CAN_RESTORE_PASSWORD_FILE=/run/secrets/restore_password \
  "can-api:$CAN_RELEASE" python /verify_restore.py
