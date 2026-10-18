"""Replicate encrypted deletion events and fetch a complete authenticated recovery journal.

Only file paths and remote identifiers are accepted from the environment. The
replicator never decrypts account tombstones or prints remote command output.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

DOMAIN = b"can-deletion-journal-checkpoint-v1"
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")


class JournalError(Exception):
    """Safe error text contains no credentials or decrypted account data."""


class Remote(Protocol):
    def read(self, name: str) -> bytes: ...
    def put(self, name: str, data: bytes, *, immutable: bool) -> None: ...
    def empty(self) -> bool: ...
    def names(self) -> list[str]: ...
    def remove(self, name: str) -> None: ...


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def event_name(value: Any) -> str:
    if not isinstance(value, str):
        raise JournalError("Invalid journal event identifier")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise JournalError("Invalid journal event identifier") from None
    return value


def checked_hash(value: Any) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise JournalError("Invalid journal digest")
    return value


def timestamp() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def instant(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise JournalError("Journal timestamp must include a timezone")
    return result


def authenticated(data: bytes, key: bytes, fields: set[str]) -> dict[str, Any]:
    value = json.loads(data)
    if set(value) != fields | {"signature"} or canonical(value) != data:
        raise JournalError("Invalid authenticated journal metadata")
    unsigned = {name: item for name, item in value.items() if name != "signature"}
    if not hmac.compare_digest(
        checked_hash(value["signature"]), sign(unsigned, key)["signature"]
    ):
        raise JournalError("Journal metadata authentication failed")
    if type(value["version"]) is not int or value["version"] != 1:
        raise JournalError("Unsupported journal metadata")
    return value


def completion(data: bytes, key: bytes, event_id: str) -> dict[str, Any]:
    value = authenticated(
        data,
        key,
        {
            "version",
            "event_id",
            "data_deleted_at",
            "completed_at",
            "purge_tombstone_after",
        },
    )
    if event_name(value["event_id"]) != event_id:
        raise JournalError("Deletion completion identifier mismatch")
    deleted = instant(value["data_deleted_at"])
    if instant(value["completed_at"]) < deleted or instant(
        value["purge_tombstone_after"]
    ) < deleted + dt.timedelta(days=30):
        raise JournalError("Deletion retention must cover at least 30 days")
    return value


def retired(value: dict[str, Any], head: dict[str, Any]) -> bool:
    return instant(value["data_deleted_at"]) <= instant(
        head["restore_not_before"]
    ) and instant(value["purge_tombstone_after"]) <= dt.datetime.now(dt.UTC)


def sign(value: dict[str, Any], encryption_key: bytes) -> dict[str, Any]:
    derived = hmac.digest(encryption_key.strip(), DOMAIN, "sha256")
    return {
        **value,
        "signature": hmac.new(derived, canonical(value), "sha256").hexdigest(),
    }


def record(data: bytes, expected_id: str) -> dict[str, Any]:
    if len(data) > MAX_RECORD_BYTES:
        raise JournalError("Journal record exceeds the size limit")
    try:
        value = json.loads(data)
        if (
            set(value) != {"version", "event_id", "ciphertext", "sha256"}
            or value["version"] != 1
        ):
            raise JournalError("Unsupported journal record")
        if event_name(value["event_id"]) != expected_id:
            raise JournalError("Journal event identifier mismatch")
        if not isinstance(value["ciphertext"], str) or not value["ciphertext"]:
            raise JournalError("Invalid encrypted journal record")
        if not hmac.compare_digest(
            checked_hash(value["sha256"]), digest(value["ciphertext"].encode())
        ):
            raise JournalError("Journal ciphertext checksum mismatch")
        if canonical(value) != data:
            raise JournalError("Journal record is not canonical")
        return value
    except (ValueError, TypeError, KeyError):
        raise JournalError("Malformed journal record") from None


def checkpoint(data: bytes, encryption_key: bytes) -> dict[str, Any]:
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise JournalError("Journal checkpoint exceeds the size limit")
    try:
        value = json.loads(data)
        fields = {
            "version",
            "sequence",
            "created_at",
            "previous_checkpoint",
            "records",
            "signature",
            "restore_not_before",
        }
        if set(value) != fields or value["version"] != 1:
            raise JournalError("Unsupported journal checkpoint")
        signature = checked_hash(value["signature"])
        unsigned = {key: item for key, item in value.items() if key != "signature"}
        if not hmac.compare_digest(
            signature, sign(unsigned, encryption_key)["signature"]
        ):
            raise JournalError("Journal checkpoint authentication failed")
        if canonical(value) != data:
            raise JournalError("Journal checkpoint is not canonical")
        if type(value["sequence"]) is not int or value["sequence"] < 0:
            raise JournalError("Invalid checkpoint sequence")
        if value["sequence"] == 0:
            if (
                value["previous_checkpoint"] is not None
                or value["records"]
                or value["restore_not_before"] != "1970-01-01T00:00:00Z"
            ):
                raise JournalError("Invalid journal genesis checkpoint")
        else:
            checked_hash(value["previous_checkpoint"])
        when = dt.datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise JournalError("Journal timestamp must include a timezone")
        if instant(value["restore_not_before"]) > when:
            raise JournalError("Journal retention floor cannot be in the future")
        if not isinstance(value["records"], list):
            raise JournalError("Invalid checkpoint inventory")
        ids = []
        for entry in value["records"]:
            if set(entry) != {"event_id", "sha256"}:
                raise JournalError("Invalid checkpoint inventory entry")
            ids.append(event_name(entry["event_id"]))
            checked_hash(entry["sha256"])
        if ids != sorted(set(ids)):
            raise JournalError("Checkpoint inventory is not unique and sorted")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise JournalError("Malformed journal checkpoint") from None


def atomic_file(path: Path, data: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise JournalError(
                        "Refusing to replace immutable journal data"
                    ) from None
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class RcloneRemote:
    def __init__(self, remote: str, config: Path):
        if not re.fullmatch(r"[A-Za-z0-9_-]+:[^\r\n:]+", remote):
            raise JournalError(
                "Journal remote must be an explicit configured off-host remote:path"
            )
        if not config.is_absolute() or not config.is_file():
            raise JournalError(
                "An absolute owner-managed rclone configuration path is required"
            )
        self.remote = remote.rstrip("/")
        self.config = config

    def command(self, *args: str) -> bytes:
        try:
            result = subprocess.run(
                ["rclone", "--config", str(self.config), *args],
                check=False,
                capture_output=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise JournalError(
                "Journal remote command is unavailable or timed out"
            ) from None
        if result.returncode:
            raise JournalError(
                f"Journal remote command failed with exit code {result.returncode}"
            )
        return result.stdout

    def read(self, name: str) -> bytes:
        return self.command("cat", f"{self.remote}/{name}")

    def put(self, name: str, data: bytes, *, immutable: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="can-journal-") as directory:
            path = Path(directory) / "encrypted-journal.json"
            path.write_bytes(data)
            args = ["copyto", str(path), f"{self.remote}/{name}"]
            if immutable:
                args.append("--immutable")
            else:
                args.append("--ignore-times")
            self.command(*args)

    def empty(self) -> bool:
        # --recursive includes retained checkpoints even if LATEST was lost.
        return not self.command("lsf", self.remote, "--recursive").strip()

    def names(self) -> list[str]:
        return (
            self.command("lsf", self.remote, "--recursive", "--files-only")
            .decode()
            .splitlines()
        )

    def remove(self, name: str) -> None:
        self.command("deletefile", f"{self.remote}/{name}")


def remote_record_ids(remote: Remote) -> set[str]:
    result = set()
    for name in remote.names():
        if not name.startswith("records/"):
            continue
        if name.count("/") != 1 or not name.endswith(".json"):
            raise JournalError(
                "An unexpected remote record prevents a complete journal"
            )
        result.add(event_name(Path(name).stem))
    return result


def load_remote(
    remote: Remote, key: bytes, *, allow_unlisted: bool = False
) -> tuple[bytes, dict[str, Any], dict[str, bytes]]:
    raw = remote.read("LATEST.json")
    head = checkpoint(raw, key)
    # A committed head must also exist as its immutable content-addressed object.
    if remote.read(f"checkpoints/{digest(raw)}.json") != raw:
        raise JournalError("Published checkpoint does not match its immutable object")
    records = {}
    for item in head["records"]:
        body = remote.read(f"records/{item['event_id']}.json")
        record(body, item["event_id"])
        if digest(body) != item["sha256"]:
            raise JournalError("Journal inventory checksum mismatch")
        records[item["event_id"]] = body
    if not allow_unlisted and remote_record_ids(remote) != set(records):
        raise JournalError("Off-host journal has uncommitted or incomplete records")
    return raw, head, records


def publish(remote: Remote, raw: bytes) -> None:
    name = f"checkpoints/{digest(raw)}.json"
    remote.put(name, raw, immutable=True)
    if remote.read(name) != raw:
        raise JournalError("Off-host checkpoint verification failed")
    remote.put("LATEST.json", raw, immutable=False)
    if remote.read("LATEST.json") != raw:
        raise JournalError("Off-host journal publication verification failed")


def initialize(remote: Remote, key: bytes) -> None:
    if not remote.empty():
        raise JournalError("Refusing to initialize a nonempty journal remote")
    raw = canonical(
        sign(
            {
                "version": 1,
                "sequence": 0,
                "created_at": timestamp(),
                "previous_checkpoint": None,
                "records": [],
                "restore_not_before": "1970-01-01T00:00:00Z",
            },
            key,
        )
    )
    publish(remote, raw)


def reject_regression(local: Path, raw: bytes, head: dict[str, Any]) -> None:
    status_path = local / "status.json"
    if not status_path.exists():
        return
    status = json.loads(status_path.read_bytes())
    if head["sequence"] < status["sequence"] or (
        head["sequence"] == status["sequence"]
        and digest(raw) != status["checkpoint_sha256"]
    ):
        raise JournalError(
            "Remote journal regressed or diverged from an acknowledged checkpoint"
        )


def replicate(local: Path, remote: Remote, key: bytes) -> dict[str, Any]:
    if not local.is_dir():
        raise JournalError("Provision the private journal directory before replication")
    with (local / ".replication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior_raw, prior, records = load_remote(remote, key, allow_unlisted=True)
        unlisted = remote_record_ids(remote) - set(records)
        reject_regression(local, prior_raw, prior)
        status_path = local / "status.json"
        local_records = {}
        retired_records = set()
        for path in sorted((local / "records").glob("*.json")):
            event_id = event_name(path.stem)
            data = path.read_bytes()
            parsed = record(data, event_id)
            completed = local / "completed" / f"{event_id}.json"
            if (
                event_id not in records
                and completed.exists()
                and retired(completion(completed.read_bytes(), key, event_id), prior)
            ):
                # A crash after publishing a retention floor must not resurrect a purged record.
                retired_records.add(event_id)
                continue
            if event_id in records and records[event_id] != data:
                raise JournalError("Local and off-host immutable events disagree")
            if event_id not in records:
                remote.put(f"records/{event_id}.json", data, immutable=True)
                if remote.read(f"records/{event_id}.json") != data:
                    raise JournalError("Off-host event verification failed")
            records[event_id] = data
            local_records[event_id] = parsed
        remote_names = set(remote.names())
        for event_id in unlisted - set(local_records) - retired_records:
            name = f"completed/{event_id}.json"
            if name not in remote_names or not retired(
                completion(remote.read(name), key, event_id), prior
            ):
                raise JournalError(
                    "An uncommitted remote record has no matching local producer"
                )
        for path in sorted((local / "completed").glob("*.json")):
            event_id = event_name(path.stem)
            data = path.read_bytes()
            value = completion(data, key, event_id)
            if event_id not in records and retired(value, prior):
                continue
            name = f"completed/{event_id}.json"
            immutable = name not in remote_names
            if not immutable:
                previous = completion(remote.read(name), key, event_id)
                fields = ("data_deleted_at", "completed_at", "purge_tombstone_after")
                if any(
                    instant(value[field]) < instant(previous[field]) for field in fields
                ):
                    raise JournalError(
                        "Deletion completion retention cannot move backwards"
                    )
            # Re-erasing a restored account extends its real retention period.
            # Only completion times may advance; encrypted events stay immutable.
            remote.put(name, data, immutable=immutable)
            if remote.read(f"completed/{event_id}.json") != data:
                raise JournalError("Off-host completion verification failed")
        inventory = [
            {"event_id": event_id, "sha256": digest(data)}
            for event_id, data in sorted(records.items())
        ]
        # An unchanged inventory needs a fresh local health heartbeat, not unbounded remote checkpoints.
        if inventory == prior["records"]:
            raw = prior_raw
            head = prior
        else:
            head = sign(
                {
                    "version": 1,
                    "sequence": prior["sequence"] + 1,
                    "created_at": timestamp(),
                    "previous_checkpoint": digest(prior_raw),
                    "records": inventory,
                    "restore_not_before": prior["restore_not_before"],
                },
                key,
            )
            raw = canonical(head)
            publish(remote, raw)
        checkpoint_hash = digest(raw)
        atomic_file(
            local / "checkpoints" / f"{checkpoint_hash}.json", raw, immutable=True
        )
        atomic_file(local / "checkpoint.json", raw)
        for event_id, parsed in local_records.items():
            ack = canonical(
                {
                    "version": 1,
                    "event_id": event_id,
                    "sha256": parsed["sha256"],
                    "checkpoint_sha256": checkpoint_hash,
                }
            )
            # An ACK is written only after the entire off-host checkpoint is verified.
            atomic_file(local / "acks" / f"{event_id}.json", ack)
        status = {
            "version": 1,
            "last_success_at": timestamp(),
            "checkpoint_sha256": checkpoint_hash,
            "sequence": head["sequence"],
            "record_count": len(records),
        }
        atomic_file(status_path, canonical(status))
        return status


def fetch(
    remote: Remote,
    destination: Path,
    key: bytes,
    minimum_checkpoint: bytes | None = None,
    snapshot_started_at: str | None = None,
) -> dict[str, Any]:
    if destination.exists():
        raise JournalError("Recovery journal destination must not already exist")
    raw, head, records = load_remote(remote, key)
    if snapshot_started_at is not None and instant(snapshot_started_at) < instant(
        head["restore_not_before"]
    ):
        raise JournalError(
            "This database backup predates the authenticated journal retention floor"
        )
    if minimum_checkpoint is not None:
        minimum = checkpoint(minimum_checkpoint, key)
        inventory = {item["event_id"]: item["sha256"] for item in head["records"]}
        if (
            head["sequence"] < minimum["sequence"]
            or (head["sequence"] == minimum["sequence"] and raw != minimum_checkpoint)
            or instant(head["restore_not_before"])
            < instant(minimum["restore_not_before"])
        ) or (
            instant(head["restore_not_before"])
            == instant(minimum["restore_not_before"])
            and any(
                inventory.get(item["event_id"]) != item["sha256"]
                for item in minimum["records"]
            )
        ):
            raise JournalError(
                "Journal is older than, or incomplete relative to, the database backup"
            )
    # All remote objects are verified before any local bundle can be considered complete.
    destination.mkdir(parents=True, mode=0o750)
    (destination / "records").mkdir(mode=0o750)
    # The owner provisions a setgid recovery parent with runtime GID 10001.
    # Pending deletion exports may add records before the off-host replica runs.
    destination.chmod(0o2770)
    (destination / "records").chmod(0o2770)
    for event_id, data in records.items():
        atomic_file(destination / "records" / f"{event_id}.json", data, immutable=True)
        parsed = record(data, event_id)
        atomic_file(
            destination / "acks" / f"{event_id}.json",
            canonical(
                {
                    "version": 1,
                    "event_id": event_id,
                    "sha256": parsed["sha256"],
                    "checkpoint_sha256": digest(raw),
                }
            ),
            immutable=True,
        )
    atomic_file(
        destination / "checkpoints" / f"{digest(raw)}.json", raw, immutable=True
    )
    atomic_file(destination / "checkpoint.json", raw, immutable=True)
    return {
        "checkpoint_sha256": digest(raw),
        "sequence": head["sequence"],
        "record_count": len(records),
    }


def archive_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def backup_envelope(data: bytes, key: bytes, anchor: bytes) -> dict[str, Any]:
    value = authenticated(
        data,
        key,
        {
            "version",
            "archive_sha256",
            "snapshot_started_at",
            "journal_checkpoint_sha256",
        },
    )
    checked_hash(value["archive_sha256"])
    if checked_hash(value["journal_checkpoint_sha256"]) != digest(anchor):
        raise JournalError("Backup envelope does not authenticate its journal anchor")
    checkpoint(anchor, key)
    if instant(value["snapshot_started_at"]) > dt.datetime.now(dt.UTC):
        raise JournalError("Backup snapshot cannot start in the future")
    return value


def seal_backup(archive: Path, anchor: Path, started: str, key: bytes) -> None:
    checkpoint(anchor.read_bytes(), key)
    if instant(started) > dt.datetime.now(dt.UTC):
        raise JournalError("Backup snapshot cannot start in the future")
    envelope = sign(
        {
            "version": 1,
            "archive_sha256": archive_digest(archive),
            "snapshot_started_at": started,
            "journal_checkpoint_sha256": digest(anchor.read_bytes()),
        },
        key,
    )
    atomic_file(
        Path(str(archive) + ".recovery.json"), canonical(envelope), immutable=True
    )


def retained_backups(remote: Remote, key: bytes, cutoff: dt.datetime) -> int:
    names = set(remote.names())
    archives = {name for name in names if name.endswith(".dump.age")}
    permitted = {
        name + suffix
        for name in archives
        for suffix in ("", ".sha256", ".journal-checkpoint.json", ".recovery.json")
    }
    if any(".dump.age" in name and name not in permitted for name in names):
        raise JournalError("An orphan or partial backup prevents retention maintenance")
    for archive in archives:
        anchor_name = archive + ".journal-checkpoint.json"
        envelope_name = archive + ".recovery.json"
        if (
            anchor_name not in names
            or envelope_name not in names
            or archive + ".sha256" not in names
        ):
            raise JournalError(
                "A retained backup lacks authenticated recovery metadata"
            )
        anchor = remote.read(anchor_name)
        value = backup_envelope(remote.read(envelope_name), key, anchor)
        if instant(value["snapshot_started_at"]) < cutoff:
            raise JournalError(
                "A retained backup predates the requested retention floor"
            )
    return len(archives)


def prune(
    local: Path, remote: Remote, backups: Remote, key: bytes, cutoff: str
) -> dict[str, Any]:
    boundary = instant(cutoff)
    if boundary > dt.datetime.now(dt.UTC):
        raise JournalError("Retention floor cannot be in the future")
    # Share the publisher lock: a concurrent replica must not restore retired events.
    with (local / ".replication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup_count = retained_backups(backups, key, boundary)
        raw, head, records = load_remote(remote, key, allow_unlisted=True)
        reject_regression(local, raw, head)
        if boundary < instant(head["restore_not_before"]):
            raise JournalError("Retention floor cannot move backwards")
        names = set(remote.names())
        eligible = set()
        completion_names = {name for name in names if name.startswith("completed/")}
        completion_names.update(
            "completed/" + path.name for path in (local / "completed").glob("*.json")
        )
        for name in sorted(completion_names):
            if not name.startswith("completed/") or not name.endswith(".json"):
                continue
            event_id = event_name(Path(name).stem)
            data = remote.read(name) if name in names else (local / name).read_bytes()
            value = completion(data, key, event_id)
            if instant(value["data_deleted_at"]) <= boundary and instant(
                value["purge_tombstone_after"]
            ) <= dt.datetime.now(dt.UTC):
                eligible.add(event_id)
        if remote_record_ids(remote) - set(records) - eligible:
            raise JournalError(
                "Uncommitted remote records prevent retention maintenance"
            )
        kept = {
            event_id: data
            for event_id, data in records.items()
            if event_id not in eligible
        }
        inventory = [
            {"event_id": event_id, "sha256": digest(data)}
            for event_id, data in sorted(kept.items())
        ]
        if inventory != head["records"] or boundary != instant(
            head["restore_not_before"]
        ):
            head = sign(
                {
                    "version": 1,
                    "sequence": head["sequence"] + 1,
                    "created_at": timestamp(),
                    "previous_checkpoint": digest(raw),
                    "restore_not_before": cutoff,
                    "records": inventory,
                },
                key,
            )
            raw = canonical(head)
            publish(remote, raw)
        # The authenticated floor is durable before any encrypted tombstone is erased.
        head_hash = digest(raw)
        atomic_file(local / "checkpoints" / f"{head_hash}.json", raw, immutable=True)
        atomic_file(local / "checkpoint.json", raw)
        for event_id, data in kept.items():
            if (local / "records" / f"{event_id}.json").exists():
                parsed = record(data, event_id)
                atomic_file(
                    local / "acks" / f"{event_id}.json",
                    canonical(
                        {
                            "version": 1,
                            "event_id": event_id,
                            "sha256": parsed["sha256"],
                            "checkpoint_sha256": head_hash,
                        }
                    ),
                )
        atomic_file(
            local / "status.json",
            canonical(
                {
                    "version": 1,
                    "last_success_at": timestamp(),
                    "checkpoint_sha256": head_hash,
                    "sequence": head["sequence"],
                    "record_count": len(kept),
                }
            ),
        )
        deleted = 0
        for event_id in sorted(eligible):
            for prefix in ("records", "completed"):
                name = f"{prefix}/{event_id}.json"
                if name in names:
                    remote.remove(name)
            # A failed remote deletion leaves local evidence for an idempotent retry.
            for prefix in ("records", "completed", "acks"):
                with contextlib.suppress(FileNotFoundError):
                    (local / prefix / f"{event_id}.json").unlink()
            deleted += 1
        # Old content-addressed inventories contain identifiers of retired events.
        for name in sorted(names):
            if (
                name.startswith("checkpoints/")
                and name.endswith(".json")
                and name != f"checkpoints/{head_hash}.json"
            ):
                checked_hash(Path(name).stem)
                remote.remove(name)
        for path in (local / "checkpoints").glob("*.json"):
            if path.stem != head_hash:
                path.unlink()
        return {
            "retired_events": deleted,
            "retained_backups": backup_count,
            "restore_not_before": cutoff,
            "checkpoint_sha256": head_hash,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("initialize", "replicate", "fetch", "seal-backup", "prune"),
    )
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--minimum-checkpoint", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--snapshot-started-at")
    parser.add_argument("--backup-cutoff")
    args = parser.parse_args()
    key_path = Path(os.environ["CAN_ENCRYPTION_KEY_FILE"])
    if not key_path.is_absolute():
        raise JournalError("An absolute encryption-key file path is required")
    key = key_path.read_bytes().strip()
    if len(key) < 32:
        raise JournalError("Journal authentication key is invalid")
    if args.operation == "seal-backup":
        if (
            args.archive is None
            or args.minimum_checkpoint is None
            or args.snapshot_started_at is None
        ):
            raise JournalError(
                "Archive, journal anchor and snapshot start time are required"
            )
        seal_backup(
            args.archive, args.minimum_checkpoint, args.snapshot_started_at, key
        )
        print("Database backup recovery envelope authenticated")
        return
    remote = RcloneRemote(
        os.environ["CAN_DELETION_JOURNAL_REMOTE"],
        Path(os.environ["CAN_RCLONE_CONFIG_FILE"]),
    )
    if args.operation == "initialize":
        initialize(remote, key)
        print("Authenticated off-host deletion journal initialized")
    elif args.operation in ("replicate", "prune"):
        local = Path(os.environ["CAN_DELETION_JOURNAL_DIR"])
        if not local.is_absolute():
            raise JournalError("An absolute journal directory is required")
        if args.operation == "prune":
            if args.backup_cutoff is None:
                raise JournalError(
                    "An explicit verified backup retention cutoff is required"
                )
            backups = RcloneRemote(
                os.environ["CAN_BACKUP_REMOTE"],
                Path(os.environ["CAN_RCLONE_CONFIG_FILE"]),
            )
            backup_directory = Path(os.environ["CAN_BACKUP_DIR"])
            if not backup_directory.is_absolute() or not backup_directory.is_dir():
                raise JournalError(
                    "Retention must run beside the configured backup producer"
                )
            with (backup_directory / ".backup.lock").open("a") as backup_lock:
                fcntl.flock(backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(
                    json.dumps(prune(local, remote, backups, key, args.backup_cutoff))
                )
        else:
            print(json.dumps(replicate(local, remote, key)))
    else:
        if args.destination is None or not args.destination.is_absolute():
            raise JournalError("An absolute fresh recovery destination is required")
        if args.minimum_checkpoint is None or not args.minimum_checkpoint.is_absolute():
            raise JournalError(
                "The authenticated journal checkpoint accompanying the backup is required"
            )
        if args.archive is None or not args.archive.is_absolute():
            raise JournalError("An absolute database archive path is required")
        anchor = args.minimum_checkpoint.read_bytes()
        envelope = backup_envelope(
            Path(str(args.archive) + ".recovery.json").read_bytes(), key, anchor
        )
        if envelope["archive_sha256"] != archive_digest(args.archive):
            raise JournalError(
                "Database archive differs from its authenticated recovery envelope"
            )
        print(
            json.dumps(
                fetch(
                    remote,
                    args.destination,
                    key,
                    anchor,
                    envelope["snapshot_started_at"],
                )
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (JournalError, OSError, KeyError, ValueError, TypeError):
        # External tools and filesystem errors can embed private host details.
        raise SystemExit(
            "Deletion journal operation failed; no new deletion acknowledgment is implied."
        ) from None
