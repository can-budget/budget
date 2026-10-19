"""Update only the existing backend after a verified off-host backup.

The fixed SSH receiver validates and installs the reviewed infra bundle first.
This entry does not bootstrap a server, change PostgreSQL/Caddy, or roll back SQL.
Runtime configuration is a root-owned, non-secret KEY=value file; no shell eval.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import ipaddress
import json
import os
import pwd
import re
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"ghcr\.io/can-budget/budget-api@sha256:[0-9a-f]{64}\Z")
COUNTER = re.compile(r"[1-9][0-9]{0,19}\Z")
DOMAIN = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
MIGRATION_CONTAINER = "can-production-release-migrate"
RUNTIME_KEYS = {
    "DOCKER_CONFIG",
    "PYTHONDONTWRITEBYTECODE",
    "CAN_DEPLOY_ROOT",
    "CAN_REPO_DIR",
    "CAN_RELEASE",
    "CAN_IMAGE",
    "CAN_DOMAIN",
    "CAN_RECOVERY_MODE",
    "CAN_ACME_EMAIL",
    "CAN_SECRETS_DIR",
    "CAN_APPLE_CLIENT_ID",
    "CAN_APPLE_TEAM_ID",
    "CAN_APPLE_KEY_ID",
    "CAN_APPLE_PRIVATE_KEY_FILE",
    "CAN_APNS_KEY_ID",
    "CAN_APNS_PRIVATE_KEY_FILE",
    "CAN_APNS_WIDGET_TOPIC",
    "CAN_BACKUP_DIR",
    "CAN_BACKUP_RECIPIENTS_FILE",
    "CAN_RCLONE_CONFIG_FILE",
    "CAN_BACKUP_REMOTE",
    "CAN_DELETION_JOURNAL_DIR",
    "CAN_DELETION_JOURNAL_REMOTE",
    "CAN_ENCRYPTION_KEY_FILE",
}


class DeployError(Exception):
    """Operator-safe error; never include subprocess output or credential contents."""


def validate_identity(sha: str, image: str, sequence: str) -> dict[str, Any]:
    if not SHA.fullmatch(sha) or not IMAGE.fullmatch(image) or not COUNTER.fullmatch(sequence):
        raise DeployError("Invalid source SHA, immutable image, or workflow run number")
    return {"source_sha": sha, "image": image, "run_number": int(sequence)}


def trusted_path(path: Path, *, directory: bool = False, owner: int | None = None) -> None:
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise DeployError("Expected an absolute trusted path without a symlink")
    details = path.stat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(details.st_mode) or details.st_mode & 0o022:
        raise DeployError("Deployment inputs must be regular, non-writable by group/others")
    if details.st_uid not in ({0, os.getuid()} if owner is None else {owner}):
        raise DeployError("Deployment input ownership is not trusted")


def load_runtime(path: Path, *, owner: int = 0) -> dict[str, str]:
    trusted_path(path, owner=owner)
    if path.stat().st_size > 32768:
        raise DeployError("Runtime configuration is too large")
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = shlex.split(line, comments=True)
        if len(parts) != 1 or "=" not in parts[0]:
            raise DeployError("Runtime configuration requires one literal KEY=value per line")
        key, value = parts[0].split("=", 1)
        if key not in RUNTIME_KEYS or key in result or any(c in value for c in "\r\n\x00$`"):
            raise DeployError("Unsupported runtime setting or shell expansion")
        result[key] = value
    if result.get("PYTHONDONTWRITEBYTECODE", "1") != "1":
        raise DeployError("PYTHONDONTWRITEBYTECODE must remain enabled")
    return result


def environment(runtime: dict[str, str], release: Path, identity: dict[str, Any]) -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        **runtime,
        "CAN_REPO_DIR": str(release),
        "CAN_RELEASE": identity["source_sha"],
        "CAN_IMAGE": identity["image"],
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    trusted_path(path)
    value = json.loads(path.read_text())
    identity = validate_identity(value["source_sha"], value["image"], str(value["run_number"]))
    if value["run_number"] != identity["run_number"]:
        raise DeployError("Invalid deployment state")
    return value


def reserve_sequence(path: Path, identity: dict[str, Any], baseline_sha: str) -> None:
    previous = read_state(path)
    if previous:
        if identity["run_number"] < previous["run_number"]:
            raise DeployError("A newer workflow run has already attempted deployment")
        if identity["run_number"] == previous["run_number"] and any(
            identity[key] != previous[key] for key in identity
        ):
            raise DeployError("A workflow run number cannot identify different release contents")
    atomic_json(path, {**identity, "previous_source_sha": baseline_sha})


class Commands:
    def run(self, args: list[str], env: dict[str, str], stage: str, timeout: int = 30) -> str:
        print(f"Deployment: {stage}", flush=True)
        try:
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise DeployError(f"{stage} unavailable or timed out") from None
        if result.returncode:
            raise DeployError(
                f"{stage} failed (exit {result.returncode}); inspect privately on the host"
            )
        return result.stdout


def compose(release: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        "can-production",
        "--env-file",
        "/dev/null",
        "-f",
        str(release / "infra/compose.yaml"),
    ]


def normalize_release_paths(value: Any, release: Path) -> Any:
    if isinstance(value, dict):
        return {key: normalize_release_paths(item, release) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_release_paths(item, release) for item in value]
    if isinstance(value, str) and value.startswith(str(release) + "/"):
        return "<release>/" + value[len(str(release)) + 1 :]
    return value


def check_fixed_infrastructure(before: dict, after: dict, current: Path, candidate: Path) -> None:
    for name in ("db", "caddy"):
        if normalize_release_paths(before["services"][name], current) != normalize_release_paths(
            after["services"][name], candidate
        ):
            raise DeployError(
                "Database or Caddy configuration changed; a separate owner maintenance is required"
            )
    for section in ("networks", "volumes", "secrets"):
        if before.get(section) != after.get(section):
            raise DeployError(
                "Persistent infrastructure changed; a separate owner maintenance is required"
            )
    for relative in ("infra/caddy/Caddyfile", "infra/postgres/init.sql"):
        if (current / relative).read_bytes() != (candidate / relative).read_bytes():
            raise DeployError(
                "Database bootstrap or Caddy source changed; owner maintenance is required"
            )
    for name in ("api", "worker", "migrate"):
        if after["services"][name]["image"] != after["services"]["api"]["image"]:
            raise DeployError("API, worker and migration must use one immutable image")


def inspect_service(
    commands: Commands,
    release: Path,
    env: dict[str, str],
    service: str,
    *,
    image_id: str | None = None,
    running_required: bool = True,
) -> dict:
    container = commands.run(
        compose(release) + ["ps", "--all", "--quiet", service], env, f"locate {service}"
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise DeployError("Expected exactly one existing production container; bootstrap is manual")
    data = json.loads(
        commands.run(
            ["docker", "inspect", "--format", "{{json .}}", container], env, f"inspect {service}"
        )
    )
    if running_required and (
        not data["State"].get("Running")
        or data["State"].get("Health", {}).get("Status") != "healthy"
    ):
        raise DeployError(f"Production {service} is not healthy")
    if image_id is not None and data.get("Image") != image_id:
        raise DeployError(f"Production {service} did not start the reviewed image")
    return data


def validate_endpoint_host(host: str) -> None:
    if host.count(":") > 1 or host.startswith("["):
        raise DeployError("CAN_DOMAIN does not support IPv6 yet; use a DNS hostname or public IPv4")
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        if DOMAIN.fullmatch(host) and len(host) <= 253:
            return
        raise DeployError(
            "CAN_DOMAIN must be a DNS hostname or canonical public IPv4 address "
            "without URL, port, path or credentials"
        ) from None
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise DeployError("CAN_DOMAIN IPv4 address must be public and unicast")


def prerequisites(runtime: dict[str, str]) -> None:
    mode = runtime.get("CAN_RECOVERY_MODE", "off_host")
    if mode not in ("off_host", "single_server"):
        raise DeployError("Invalid recovery mode")
    required = (
        ("CAN_DOMAIN",)
        if mode == "single_server"
        else ("CAN_BACKUP_REMOTE", "CAN_DELETION_JOURNAL_REMOTE", "CAN_DOMAIN")
    )
    for key in required:
        if not runtime.get(key):
            raise DeployError("Runtime configuration lacks required production settings")
    validate_endpoint_host(runtime["CAN_DOMAIN"])
    if runtime.get("DOCKER_CONFIG"):
        trusted_path(Path(runtime["DOCKER_CONFIG"]), directory=True)
    directories = ("CAN_SECRETS_DIR", "CAN_DELETION_JOURNAL_DIR")
    if mode == "off_host":
        directories += ("CAN_BACKUP_DIR",)
    for key in directories:
        path = Path(runtime.get(key, ""))
        if not path.is_absolute() or not path.is_dir():
            raise DeployError("Owner must provision persistent runtime directories first")
    files = ("CAN_ENCRYPTION_KEY_FILE",)
    if mode == "off_host":
        files += ("CAN_BACKUP_RECIPIENTS_FILE", "CAN_RCLONE_CONFIG_FILE")
    for key in files:
        path = Path(runtime.get(key, ""))
        if not path.is_absolute() or not path.is_file() or not path.stat().st_size:
            raise DeployError("Owner-managed backup and encryption file paths are required")


def no_existing_migration(commands: Commands, env: dict[str, str]) -> None:
    existing = commands.run(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=can-production",
            "--filter",
            "label=com.docker.compose.service=migrate",
        ],
        env,
        "check for unfinished migration",
    )
    if existing.strip():
        raise DeployError(
            "A prior migration container remains; inspect and resolve it before retrying"
        )


def image_identity(commands: Commands, env: dict[str, str], sha: str, image: str) -> str:
    metadata = json.loads(
        commands.run(
            ["docker", "image", "inspect", "--format", "{{json .}}", image],
            env,
            "verify image revision",
        )
    )
    if metadata.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != sha:
        raise DeployError("Image revision label differs from the reviewed source SHA")
    if metadata.get("Os") != "linux" or metadata.get("Architecture") != "amd64":
        raise DeployError("The reviewed backend image must target Linux amd64")
    image_id = metadata.get("Id", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise DeployError("Pulled image has no valid immutable image ID")
    return image_id


def public_readiness(commands: Commands, env: dict[str, str]) -> None:
    response = commands.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "10",
            "--retry",
            "6",
            "--retry-delay",
            "5",
            "--retry-max-time",
            "65",
            "--proto",
            "=https",
            "https://" + env["CAN_DOMAIN"] + "/health/ready",
        ],
        env,
        "verify public HTTPS readiness",
        100,
    )
    if json.loads(response).get("status") != "ok":
        raise DeployError("Public HTTPS did not confirm API readiness")


def promote_link(root: Path, sha: str) -> None:
    with tempfile.TemporaryDirectory(prefix=".promote-", dir=root) as directory:
        temporary = Path(directory) / "current"
        temporary.symlink_to(root / "releases" / sha, target_is_directory=True)
        os.replace(temporary, root / "current")
        fsync_dir(root)


def deploy(
    sha: str,
    image: str,
    sequence: str,
    *,
    candidate: Path,
    root: Path,
    runtime: dict[str, str],
    commands: Commands | None = None,
) -> dict[str, Any]:
    identity = validate_identity(sha, image, sequence)
    commands = commands or Commands()
    trusted_path(root, directory=True)
    trusted_path(root / "releases", directory=True)
    if candidate != root / "releases" / sha:
        raise DeployError("Candidate must be the reviewed source SHA directory under releases")
    trusted_path(candidate, directory=True)
    for relative in (
        "infra/compose.yaml",
        "infra/scripts/backup.sh",
        "infra/deletion_journal.py",
        "infra/caddy/Caddyfile",
        "infra/postgres/init.sql",
    ):
        trusted_path(candidate / relative)
    prerequisites(runtime)
    state_dir = root / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    trusted_path(state_dir, directory=True)
    lock_path = state_dir / "deploy.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeployError("Another deployment holds the production lock") from None
        current_link = root / "current"
        if not current_link.is_symlink():
            raise DeployError("Manual bootstrap must establish the current release symlink")
        current = current_link.resolve(strict=True)
        if current.parent != root / "releases" or not SHA.fullmatch(current.name):
            raise DeployError("Current release must be an owner-established SHA directory")
        trusted_path(current, directory=True)
        for relative in (
            "infra/compose.yaml",
            "infra/scripts/backup.sh",
            "infra/deletion_journal.py",
            "infra/caddy/Caddyfile",
            "infra/postgres/init.sql",
        ):
            trusted_path(current / relative)
        previous = read_state(state_dir / "last-success.json")
        high_water = read_state(state_dir / "high-water.json")
        if previous and previous["source_sha"] != current.name:
            raise DeployError(
                "Current symlink and successful-release receipt disagree; owner reconciliation is required"
            )
        if not previous and high_water and high_water.get("previous_source_sha") != current.name:
            raise DeployError(
                "Bootstrap pointer and attempted-release baseline disagree; owner reconciliation is required"
            )
        previous_identity = previous or {
            "source_sha": current.name,
            "image": runtime.get("CAN_IMAGE") or "can-api:" + current.name,
            "run_number": 0,
        }
        old_env = environment(runtime, current, previous_identity)
        new_env = environment(runtime, candidate, identity)
        reserve_sequence(state_dir / "high-water.json", identity, current.name)
        if previous and all(previous[key] == identity[key] for key in identity):
            image_id = image_identity(commands, new_env, sha, image)
            for service in ("api", "worker"):
                inspect_service(commands, current, new_env, service, image_id=image_id)
            public_readiness(commands, new_env)
            return previous
        platform = commands.run(
            ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"],
            new_env,
            "verify Docker platform",
        ).strip()
        if platform != "linux/amd64":
            raise DeployError("Continuous deployment requires a Linux amd64 Docker host")
        no_existing_migration(commands, new_env)
        before = json.loads(
            commands.run(
                compose(current) + ["config", "--format", "json"],
                old_env,
                "validate current Compose",
            )
        )
        after = json.loads(
            commands.run(
                compose(candidate) + ["config", "--format", "json"],
                new_env,
                "validate candidate Compose",
            )
        )
        if after["services"]["api"]["image"] != image:
            raise DeployError("Candidate Compose does not use the requested immutable image")
        check_fixed_infrastructure(before, after, current, candidate)
        for service in ("db", "caddy"):
            inspect_service(commands, current, old_env, service)
        for service in ("api", "worker"):
            inspect_service(commands, current, old_env, service, running_required=False)
        commands.run(["docker", "pull", image], new_env, "pull reviewed image", 900)
        image_id = image_identity(commands, new_env, sha, image)
        commands.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pull",
                "never",
                image,
                "python",
                "-c",
                "import can_api.app, can_api.worker",
            ],
            new_env,
            "smoke candidate runtime",
            60,
        )
        # Current tooling and the existing DB are used; the candidate has not changed live services.
        if runtime.get("CAN_RECOVERY_MODE", "off_host") == "off_host":
            commands.run(
                ["bash", str(current / "infra/scripts/backup.sh")],
                old_env,
                "verified encrypted off-host backup",
                7200,
            )
        touched = False
        migration_started = False
        try:
            touched = True
            commands.run(
                compose(current) + ["stop", "--timeout", "45", "api", "worker"],
                old_env,
                "stop backend writes",
                120,
            )
            no_existing_migration(commands, new_env)
            migration_started = True
            commands.run(
                compose(candidate)
                + [
                    "--profile",
                    "maintenance",
                    "run",
                    "--name",
                    MIGRATION_CONTAINER,
                    "--rm",
                    "--no-deps",
                    "--pull",
                    "never",
                    "migrate",
                ],
                new_env,
                "forward schema migration",
                900,
            )
            no_existing_migration(commands, new_env)
            migration_started = False
            commands.run(
                compose(candidate)
                + [
                    "up",
                    "--no-deps",
                    "--no-build",
                    "--pull",
                    "never",
                    "--force-recreate",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    "api",
                    "worker",
                ],
                new_env,
                "start and wait for backend health",
                240,
            )
            for service in ("api", "worker"):
                inspect_service(commands, candidate, new_env, service, image_id=image_id)
            public_readiness(commands, new_env)
            completed = {
                **identity,
                "recovery_mode": runtime.get("CAN_RECOVERY_MODE", "off_host"),
                "completed_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            promote_link(root, sha)
            atomic_json(state_dir / "last-success.json", completed)
            return completed
        except (DeployError, OSError, ValueError, KeyError, TypeError):
            if migration_started:
                with contextlib.suppress(DeployError):
                    commands.run(
                        ["docker", "stop", "--time", "45", MIGRATION_CONTAINER],
                        new_env,
                        "contain unfinished migration",
                        60,
                    )
            if touched:
                with contextlib.suppress(DeployError):
                    commands.run(
                        compose(candidate) + ["stop", "--timeout", "45", "api", "worker"],
                        new_env,
                        "contain failed deployment",
                        120,
                    )
            raise DeployError(
                "Deployment failed after stopping writes. No SQL/image rollback attempted; keep ingress closed and perform reviewed forward recovery."
            ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sha")
    parser.add_argument("image")
    parser.add_argument("run_number")
    args = parser.parse_args()
    try:
        validate_identity(args.source_sha, args.image, args.run_number)
        runtime = load_runtime(Path("/etc/can/runtime.conf"))
        registry = os.environ.get("CAN_RELEASE_DOCKER_CONFIG")
        if registry:
            registry_path = Path(registry)
            trusted_path(registry_path, directory=True, owner=os.getuid())
            trusted_path(registry_path / "config.json", owner=os.getuid())
            runtime["DOCKER_CONFIG"] = registry
        root = Path(runtime.get("CAN_DEPLOY_ROOT", "/opt/can"))
        result = deploy(
            args.source_sha,
            args.image,
            args.run_number,
            candidate=Path(__file__).resolve().parents[2],
            root=root,
            runtime=runtime,
        )
    except (DeployError, OSError, ValueError, KeyError, TypeError) as error:
        message = (
            str(error)
            if isinstance(error, DeployError)
            else "Deployment prerequisites or state are invalid; inspect privately on the host"
        )
        parser.exit(1, message + "\n")
    print(json.dumps({"status": "healthy", **result}, sort_keys=True))


if __name__ == "__main__":
    main()
