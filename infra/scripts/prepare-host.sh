#!/usr/bin/env bash
# Explicit first-host preparation. Does not configure secrets or start services.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 0077

fail() {
    printf 'Host preparation refused: %s\n' "$1" >&2
    return 1
}

check_accounts() {
    local runtime by_gid operator primary docker_group operator_shell
    runtime="$(getent group can-runtime || true)"
    by_gid="$(getent group 10001 || true)"
    [[ -z "$runtime" || "$(cut -d: -f3 <<<"$runtime")" == 10001 ]] ||
        fail 'can-runtime exists with a different GID.'
    [[ -z "$by_gid" || "${by_gid%%:*}" == can-runtime ]] ||
        fail 'GID 10001 already belongs to another group.'
    docker_group="$(getent group docker || true)"
    [[ -n "$docker_group" ]] || fail 'The existing Docker installation must provide its docker group.'
    operator="$(getent passwd can-operator || true)"
    if [[ -n "$operator" ]]; then
        operator_shell="$(cut -d: -f7 <<<"$operator")"
        [[ "$(cut -d: -f3 <<<"$operator")" != 0 &&
            "$(cut -d: -f6 <<<"$operator")" == /home/can-operator &&
            ( "$operator_shell" == /bin/bash || "$operator_shell" == /bin/sh ) ]] ||
            fail 'Existing can-operator UID, home or shell needs owner review.'
        primary="$(getent group can-operator || true)"
        [[ -n "$primary" && "$(cut -d: -f3 <<<"$primary")" == "$(cut -d: -f4 <<<"$operator")" ]] ||
            fail 'Existing can-operator must have its own primary group.'
    fi
}

check_path() {
    local path="$1" parent="$1"
    while [[ "$parent" != / ]]; do
        [[ ! -L "$parent" ]] || fail 'Managed host paths must not contain symlinks.'
        if [[ -e "$parent" ]]; then
            [[ -d "$parent" ]] || fail 'A managed directory path is occupied by another object.'
        fi
        parent="${parent%/*}"
        [[ -n "$parent" ]] || parent=/
    done
    if [[ -e "$path" ]]; then
        local owner operator_uid
        owner="$(stat -c %u -- "$path")"
        operator_uid="$(id -u can-operator 2>/dev/null || true)"
        [[ "$owner" == 0 || ( -n "$operator_uid" && "$owner" == "$operator_uid" ) ]] ||
            fail 'An existing managed directory belongs to another account.'
    fi
}

check_source() {
    local path="$1" metadata owner mode parent
    [[ "$path" == /* && -f "$path" && ! -L "$path" ]] || fail 'Supply an absolute regular receiver source file.'
    [[ "$(readlink -f -- "$path")" == "$path" ]] || fail 'Receiver source path must not contain symlinks.'
    for parent in "$path" "${path%/*}"; do
        metadata="$(stat -c '%u %a' -- "$parent")"
        read -r owner mode <<<"$metadata"
        [[ "$owner" == 0 && $((8#$mode & 0022)) == 0 ]] ||
            fail 'Receiver source and its directory must be root-owned without group/other writes.'
    done
    /usr/bin/python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$path"
}

preflight() {
    [[ "$EUID" == 0 ]] || fail 'Run this reviewed script as root.'
    [[ -r /etc/os-release ]] || fail 'Host operating system cannot be identified.'
    # This file is maintained by the host operating system, never supplied by CI.
    local ID VERSION_ID
    source /etc/os-release
    [[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || fail 'This preparation is reviewed for Ubuntu 24.04 only.'
    [[ "$(dpkg --print-architecture)" == amd64 ]] || fail 'The release image requires amd64.'
    local command compose_version
    for command in apt-get dpkg-query docker python3 curl flock sha256sum getent groupadd useradd usermod install; do
        command -v "$command" >/dev/null || fail 'A required host command is missing; review the existing installation.'
    done
    /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail 'Python 3.11 or later is required.'
    [[ "$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')" == linux/amd64 ]] || fail 'Existing Docker Engine must be available on Linux amd64.'
    compose_version="$(docker compose version --short)"
    [[ "$compose_version" =~ ^v?([0-9]+)\. && "${BASH_REMATCH[1]}" -ge 2 ]] || fail 'Docker Compose v2 or later must already be installed.'
    check_source "$1"
    check_accounts
    local directory
    for directory in /home/can-operator /home/can-operator/.ssh /opt/can /opt/can/releases /opt/can/state \
        /etc/can /etc/can/secrets /etc/can/registry /var/lib/can-backup /var/lib/can-deletions \
        /var/lib/can-deletions/records /var/lib/can-deletions/completed /var/lib/can-deletions/acks \
        /var/lib/can-deletions/checkpoints /usr/local/lib/can; do
        check_path "$directory"
    done
    local receiver=/usr/local/lib/can/receive-release.py
    if [[ -e "$receiver" || -L "$receiver" ]]; then
        [[ -f "$receiver" && ! -L "$receiver" && "$(stat -c %u -- "$receiver")" == 0 ]] ||
            fail 'Installed receiver must be a root-owned regular file.'
    fi
}

ensure_packages() {
    local package status
    local missing=()
    for package in age rclone; do
        status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
        [[ "$status" == 'install ok installed' ]] || missing+=("$package")
    done
    if (( ${#missing[@]} )); then
        DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 update
        DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install --yes --no-install-recommends "${missing[@]}"
    fi
    command -v age >/dev/null && command -v rclone >/dev/null || fail 'Backup executables are still unavailable.'
}

ensure_accounts() {
    getent group can-runtime >/dev/null || groupadd --gid 10001 can-runtime
    getent group can-operator >/dev/null || groupadd can-operator
    if ! getent passwd can-operator >/dev/null; then
        useradd --create-home --home-dir /home/can-operator --shell /bin/bash --gid can-operator --no-user-group can-operator
    fi
    usermod --append --groups docker,can-runtime can-operator
}

prepare_directories() {
    local directory
    for directory in /home/can-operator /home/can-operator/.ssh /opt/can /opt/can/releases /opt/can/state /var/lib/can-backup; do
        install -d -o can-operator -g can-operator -m 0700 -- "$directory"
    done
    install -d -o root -g can-operator -m 0750 -- /etc/can
    for directory in /etc/can/secrets /etc/can/registry; do
        install -d -o can-operator -g can-operator -m 0700 -- "$directory"
    done
    for directory in /var/lib/can-deletions /var/lib/can-deletions/records /var/lib/can-deletions/completed \
        /var/lib/can-deletions/acks /var/lib/can-deletions/checkpoints; do
        install -d -o can-operator -g can-runtime -m 2770 -- "$directory"
    done
    install -d -o root -g root -m 0755 -- /usr/local/lib/can
}

main() {
    [[ $# == 1 ]] || fail 'Usage: prepare-host.sh /absolute/reviewed/receive-release.py'
    preflight "$1"
    ensure_packages
    ensure_accounts
    prepare_directories
    install -o root -g root -m 0755 -- "$1" /usr/local/lib/can/receive-release.py
    printf '%s\n' 'Host prerequisites prepared. Production bootstrap and credential provisioning remain separate.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
