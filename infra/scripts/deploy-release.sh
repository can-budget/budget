#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 3 ]]; then
  echo 'Usage: deploy-release.sh SOURCE_SHA IMMUTABLE_IMAGE WORKFLOW_RUN_NUMBER' >&2
  exit 64
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 "$root/deploy-release.py" "$@"
