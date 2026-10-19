#!/usr/bin/env bash
set -euo pipefail
umask 077

# GitHub runner only. Owner secrets arrive through step environment, never argv.
: "${CAN_DEPLOY_HOST:?Set production deployment host}"
: "${CAN_DEPLOY_USER:?Set production deployment user}"
: "${CAN_DEPLOY_SSH_KEY:?Set the dedicated production SSH secret}"
: "${CAN_DEPLOY_KNOWN_HOSTS:?Set verified pinned server host keys}"
: "${CAN_IMAGE:?Set the published immutable image digest}"
: "${GITHUB_SHA:?}"
: "${GITHUB_RUN_NUMBER:?}"
port="${CAN_DEPLOY_PORT:-22}"
[[ "$CAN_DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || exit 64
[[ "$CAN_DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 64
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port > 0 && 10#$port < 65536)) || exit 64
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "$GITHUB_RUN_NUMBER" =~ ^[1-9][0-9]{0,14}$ ]] || exit 64
[[ "$CAN_IMAGE" =~ ^ghcr\.io/can-budget/budget-api@sha256:[0-9a-f]{64}$ ]] || exit 64
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]] || exit 65

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
printf '%s\n' "$CAN_DEPLOY_SSH_KEY" >"$temporary/key"
printf '%s\n' "$CAN_DEPLOY_KNOWN_HOSTS" >"$temporary/known_hosts"
unset CAN_DEPLOY_SSH_KEY CAN_DEPLOY_KNOWN_HOSTS

# The credential preamble travels only inside the pinned SSH channel. It is not
# part of the source archive or the SSH command, and the receiver deletes it.
# Legacy/manual callers may omit registry auth when the host has its own access.
{
  if [[ -n "${CAN_REGISTRY_TOKEN:-}" ]]; then
    python3 - <<'PY'
import json
import os
import sys

sys.stdout.write("CAN-REGISTRY-AUTH-V1\n")
sys.stdout.write(json.dumps({"username": os.environ["CAN_REGISTRY_USER"],
                           "token": os.environ["CAN_REGISTRY_TOKEN"]}) + "\n")
PY
  fi
  git archive --format=tar "$GITHUB_SHA" infra
} | ssh -F /dev/null -T \
  -p "$port" -i "$temporary/key" \
  -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$temporary/known_hosts" \
  -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=20 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  "$CAN_DEPLOY_USER@$CAN_DEPLOY_HOST" \
  "release $GITHUB_SHA $CAN_IMAGE $GITHUB_RUN_NUMBER"
