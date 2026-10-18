"""Exercise the host preparation shell with fake privileged commands only."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/prepare-host.sh"
FAKES = r"""
log() { printf '%s\n' "$*" >> "$CASE_DIR/calls"; }
getent() {
    case "$*" in
        'group docker') printf '%s\n' 'docker:x:999:' ;;
        'group can-runtime'|'group 10001')
            [[ -f "$CASE_DIR/runtime" ]] || return 2
            printf '%s\n' 'can-runtime:x:10001:' ;;
        'group can-operator')
            [[ -f "$CASE_DIR/group" ]] || return 2
            printf '%s\n' 'can-operator:x:1000:' ;;
        'passwd can-operator')
            [[ -f "$CASE_DIR/user" ]] || return 2
            printf 'can-operator:x:1000:1000::/home/can-operator:%s\n' "${CASE_OPERATOR_SHELL:-/bin/bash}" ;;
        *) return 2 ;;
    esac
}
groupadd() {
    log groupadd "$@"
    if [[ "$*" == '--gid 10001 can-runtime' ]]; then
        touch "$CASE_DIR/runtime"
    else
        touch "$CASE_DIR/group"
    fi
}
useradd() { log useradd "$@"; touch "$CASE_DIR/user"; }
usermod() { log usermod "$@"; }
dpkg-query() {
    [[ -f "$CASE_DIR/packages" ]] || return 1
    printf '%s' 'install ok installed'
}
apt-get() {
    log apt-get "$@"
    [[ ! -f "$CASE_DIR/fail-apt" ]] || return 100
    [[ " $* " != *' install '* ]] || touch "$CASE_DIR/packages"
}
age() { :; }
rclone() { :; }
install() { log install "$@"; }
"""


def run_script(tmp_path, snippet, *, fakes=FAKES):
    return subprocess.run(
        ["bash", "-c", 'source "$1"\n' + fakes + "\n" + snippet, "test", str(SCRIPT)],
        env={"PATH": os.environ["PATH"], "CASE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path):
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def test_first_preparation_then_retry_are_additive_and_do_not_create_runtime_files(tmp_path):
    result = run_script(
        tmp_path,
        """
        preflight() { check_accounts; }
        main /reviewed/receive-release.py
        main /reviewed/receive-release.py
        """,
    )
    assert result.returncode == 0, result.stderr
    commands = calls(tmp_path)
    assert sum(line.startswith("apt-get ") and " update" in line for line in commands) == 1
    assert sum("install --yes --no-install-recommends age rclone" in line for line in commands) == 1
    assert commands.count("groupadd --gid 10001 can-runtime") == 1
    assert sum(line.startswith("useradd ") for line in commands) == 1
    assert commands.count("usermod --append --groups docker,can-runtime can-operator") == 2
    assert commands.index("apt-get -o DPkg::Lock::Timeout=60 update") < commands.index(
        "groupadd --gid 10001 can-runtime"
    )
    assert (
        "install -d -o can-operator -g can-runtime -m 2770 -- /var/lib/can-deletions/records"
        in commands
    )
    assert "install -d -o can-operator -g can-operator -m 0700 -- /opt/can/state" in commands
    assert (
        "install -o root -g root -m 0755 -- /reviewed/receive-release.py /usr/local/lib/can/receive-release.py"
        in commands
    )
    assert not any(
        forbidden in line
        for line in commands
        for forbidden in (
            "runtime.conf",
            "authorized_keys",
            "systemctl",
            "docker ",
            "--password",
            "initialize",
            "migrate",
            "chown -R",
        )
    )


def test_apt_failure_stops_before_account_or_directory_changes(tmp_path):
    (tmp_path / "fail-apt").touch()
    result = run_script(
        tmp_path, "preflight() { check_accounts; }; main /reviewed/receive-release.py"
    )
    assert result.returncode == 100
    assert calls(tmp_path) == ["apt-get -o DPkg::Lock::Timeout=60 update"]


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
def test_existing_operator_shell_is_reused_without_account_or_password_changes(tmp_path, shell):
    for fixture in ("user", "group", "packages"):
        (tmp_path / fixture).touch()
    result = run_script(
        tmp_path,
        f"CASE_OPERATOR_SHELL={shell}; preflight() {{ check_accounts; }}; main /reviewed/receive-release.py",
    )
    assert result.returncode == 0, result.stderr
    commands = calls(tmp_path)
    assert "groupadd --gid 10001 can-runtime" in commands
    assert "usermod --append --groups docker,can-runtime can-operator" in commands
    assert not any(line.startswith("useradd ") for line in commands)
    assert not any(
        "--shell" in line or "--password" in line or "passwd " in line for line in commands
    )


@pytest.mark.parametrize(
    "query,response,reason",
    [
        ("group can-runtime", "can-runtime:x:555:", "different GID"),
        ("group 10001", "another-service:x:10001:", "another group"),
        (
            "passwd can-operator",
            "can-operator:x:0:1000::/home/can-operator:/bin/bash",
            "owner review",
        ),
        ("passwd can-operator", "can-operator:x:1000:1000::/other/home:/bin/bash", "owner review"),
        (
            "passwd can-operator",
            "can-operator:x:1000:555::/home/can-operator:/bin/bash",
            "primary group",
        ),
    ],
)
def test_conflicting_accounts_fail_before_any_package_or_permission_changes(
    tmp_path, query, response, reason
):
    snippet = f"""
    getent() {{
        case "$*" in
            '{query}') printf '%s\\n' '{response}' ;;
            'group docker') printf '%s\\n' 'docker:x:999:' ;;
            *) return 2 ;;
        esac
    }}
    preflight() {{ check_accounts; }}
    main /reviewed/receive-release.py
    """
    result = run_script(tmp_path, snippet)
    assert result.returncode != 0
    assert reason in result.stderr
    assert calls(tmp_path) == []


def test_existing_packages_do_not_trigger_apt_update_or_upgrade(tmp_path):
    (tmp_path / "packages").touch()
    result = run_script(tmp_path, "ensure_packages")
    assert result.returncode == 0
    assert calls(tmp_path) == []


def test_only_missing_declared_package_is_installed(tmp_path):
    result = run_script(
        tmp_path,
        """
        dpkg-query() {
            [[ "${*: -1}" == age ]] || return 1
            printf '%s' 'install ok installed'
        }
        ensure_packages
        """,
    )
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [
        "apt-get -o DPkg::Lock::Timeout=60 update",
        "apt-get -o DPkg::Lock::Timeout=60 install --yes --no-install-recommends rclone",
    ]


def test_symlink_managed_directory_is_rejected_without_mutation(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    result = run_script(tmp_path, 'check_path "$CASE_DIR/link/nested"')
    assert result.returncode != 0
    assert "symlinks" in result.stderr
    assert not (tmp_path / "real/nested").exists()
    assert calls(tmp_path) == []


def test_non_directory_managed_path_is_rejected(tmp_path):
    (tmp_path / "occupied").write_text("unrelated fixture")
    result = run_script(tmp_path, 'check_path "$CASE_DIR/occupied"')
    assert result.returncode != 0
    assert "another object" in result.stderr
    assert (tmp_path / "occupied").read_text() == "unrelated fixture"


def test_receiver_source_symlink_is_rejected(tmp_path):
    (tmp_path / "receiver.py").write_text("pass\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "receiver.py")
    result = run_script(tmp_path, 'check_source "$CASE_DIR/link.py"')
    assert result.returncode != 0
    assert "regular receiver source" in result.stderr
    assert calls(tmp_path) == []
