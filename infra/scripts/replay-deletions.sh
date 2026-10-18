#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 4 ]]; then
  echo 'Usage: bash infra/scripts/replay-deletions.sh can-restore-UNIQUE RESTORE_PASSWORD_FILE JOURNAL_DIR migrate|replay|export-pending|finalize|check' >&2
  exit 64
fi
project=$1
password_file=$2
journal=$3
operation=$4
: "${CAN_RELEASE:?Set the reviewed recovery image tag}"
: "${CAN_ENCRYPTION_KEY_FILE:?Set the owner recovery encryption-key file path}"
[[ "$project" =~ ^can-restore-[a-z0-9][a-z0-9-]{2,40}$ ]] || exit 64
[[ "$password_file" == /* && -s "$password_file" && "$journal" == /* && -d "$journal" ]] || exit 66
[[ "$CAN_ENCRYPTION_KEY_FILE" == /* && -s "$CAN_ENCRYPTION_KEY_FILE" ]] || exit 66
case "$operation" in migrate|replay|export-pending|finalize|check) ;; *) exit 64 ;; esac
mount_mode=readonly
[[ "$operation" != export-pending ]] || mount_mode=readonly=false
run=(docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true
  --network "${project}_isolated" --tmpfs /tmp:rw,noexec,nosuid,size=16m
  --mount "type=bind,source=$password_file,target=/run/secrets/restore_password,readonly"
  --mount "type=bind,source=$CAN_ENCRYPTION_KEY_FILE,target=/run/secrets/encryption_key,readonly"
  --mount "type=bind,source=$journal,target=/recovery-journal,$mount_mode"
  --env CAN_DATABASE_HOST=restore-db --env CAN_DATABASE_NAME=can_restore
  --env CAN_DATABASE_USER=postgres --env CAN_DATABASE_PASSWORD_FILE=/run/secrets/restore_password
  --env CAN_ENCRYPTION_KEY_FILE=/run/secrets/encryption_key
  --env CAN_DELETION_JOURNAL_DIR=/recovery-journal)
if [[ "$operation" == finalize ]]; then
  : "${CAN_APPLE_CLIENT_ID:?Set the owner Sign in with Apple client ID}"
  : "${CAN_APPLE_TEAM_ID:?Set the owner Apple team ID}"
  : "${CAN_APPLE_KEY_ID:?Set the owner Apple signing key ID}"
  : "${CAN_APPLE_PRIVATE_KEY_FILE:?Set the owner Apple signing key file path}"
  [[ "$CAN_APPLE_PRIVATE_KEY_FILE" == /* && -s "$CAN_APPLE_PRIVATE_KEY_FILE" ]] || exit 66
  # This temporary egress network has no ingress ports. Only explicit Apple finalization uses it.
  network="${project}_apple-revocation"
  if docker network inspect "$network" >/dev/null 2>&1; then
    echo 'Refusing an existing recovery egress network.' >&2
    exit 73
  fi
  docker network create --label "can.restore.project=$project" "$network" >/dev/null
  trap 'docker network rm "$network" >/dev/null' EXIT
  run+=(--network "$network"
    --mount "type=bind,source=$CAN_APPLE_PRIVATE_KEY_FILE,target=/run/secrets/apple_key,readonly"
    --env CAN_APPLE_CLIENT_ID --env CAN_APPLE_TEAM_ID --env CAN_APPLE_KEY_ID
    --env CAN_APPLE_PRIVATE_KEY_FILE=/run/secrets/apple_key)
fi
if [[ "$operation" == migrate ]]; then
  "${run[@]}" "can-api:$CAN_RELEASE" alembic -c services/api/alembic.ini upgrade head
elif [[ "$operation" == replay ]]; then
  "${run[@]}" "can-api:$CAN_RELEASE" python -m can_api.deletion_replay --journal-dir /recovery-journal
else
  "${run[@]}" "can-api:$CAN_RELEASE" python -m can_api.deletion_replay --journal-dir /recovery-journal "--$operation"
fi
