#!/usr/bin/env python3
"""Restricted SSH entry point installed outside the release tree by the owner."""

import fcntl
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

IMAGE = r"ghcr\.io/can-budget/budget-api@sha256:[0-9a-f]{64}"
COMMAND = re.compile(rf"release ([0-9a-f]{{40}}) ({IMAGE}) ([1-9][0-9]{{0,14}})")
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 1000
REQUIRED = {
    "infra/compose.yaml",
    "infra/caddy/Caddyfile",
    "infra/postgres/init.sql",
    "infra/deletion_journal.py",
    "infra/scripts/backup.sh",
    "infra/scripts/deploy-release.sh",
    "infra/scripts/deploy-release.py",
}


def unpack(payload: bytes, target: Path) -> None:
    """Extract regular infra files manually; tar links and special files are refused."""
    if len(payload) > MAX_BYTES:
        raise ValueError("Release archive exceeds the size limit")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES:
            raise ValueError("Too many release archive entries")
        seen: set[str] = set()
        total = 0
        for entry in members:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            path = PurePosixPath(name)
            if (
                not name
                or str(path) != name
                or path.is_absolute()
                or ".." in path.parts
                or path.parts[0] != "infra"
                or name in seen
                or not (entry.isdir() or entry.isfile())
                or entry.size < 0
            ):
                raise ValueError("Unsafe release archive entry")
            total += entry.size
            if total > MAX_BYTES:
                raise ValueError("Expanded release archive exceeds the size limit")
            seen.add(name)
        if not REQUIRED.issubset({entry.name for entry in members if entry.isfile()}):
            raise ValueError("Release archive is incomplete")
        for entry in members:
            destination = target / entry.name
            if entry.isdir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                destination.chmod(0o755)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with archive.extractfile(entry) as source, destination.open("xb") as out:
                    shutil.copyfileobj(source, out)
                # Non-secret source bind mounts must remain readable by container
                # users (notably postgres); the release root itself stays private.
                destination.chmod(0o644)
        for directory in target.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("Unexpected object in existing release")
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sync_tree(root: Path) -> None:
    """Make source files durable before any durable success marker can name them."""
    directories = [root]
    for path in root.rglob("*"):
        if path.is_dir():
            directories.append(path)
        else:
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def receive(command: str, stream, root: Path) -> int:
    match = COMMAND.fullmatch(command)
    if match is None:
        raise ValueError("Only the release SHA IMAGE RUN_NUMBER command is permitted")
    sha, image, sequence = match.groups()
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("Owner must provision the deployment root")
    releases = root / "releases"
    if not releases.is_dir() or releases.is_symlink():
        raise ValueError("Owner must provision the releases directory")
    with (root / ".receive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = stream.read(MAX_BYTES + 1)
        with tempfile.TemporaryDirectory(prefix=".incoming-", dir=releases) as temporary:
            staging = Path(temporary) / "release"
            staging.mkdir(mode=0o700)
            unpack(payload, staging)
            destination = releases / sha
            if destination.is_symlink():
                raise ValueError("Release destination must not be a symlink")
            if destination.exists():
                if inventory(destination) != inventory(staging):
                    raise ValueError("An existing source revision has different release files")
                sync_tree(destination)
            else:
                sync_tree(staging)
                staging.rename(destination)
                descriptor = os.open(releases, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        # This code is reviewed main-branch code. Docker access is privileged;
        # the SSH restriction narrows the interface, not the authority of that code.
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": os.environ["HOME"],
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            ["bash", str(destination / "infra/scripts/deploy-release.sh"), sha, image, sequence],
            env=environment,
            check=False,
        ).returncode


def main() -> int:
    os.umask(0o077)
    try:
        return receive(
            os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdin.buffer, Path("/opt/can")
        )
    except (ValueError, OSError, tarfile.TarError):
        # Do not echo the command, archive names or owner configuration to CI logs.
        print("Release receiver refused the request; inspect trusted host state.", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
