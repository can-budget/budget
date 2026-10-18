"""Encrypted immutable deletion events, independently acknowledged outside database backups."""

import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from .config import settings
from .errors import require
from .security import decrypt

POLICY = "retain_ledger_anonymize_v1"
HEX = re.compile(r"^[0-9a-f]{64}$")


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fsync_dir(path.parent.parent)
    if path.exists():
        require(not path.is_symlink() and path.read_bytes() == data, "journal_event_conflict", 503)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(
                not path.is_symlink() and path.read_bytes() == data, "journal_event_conflict", 503
            )
        fsync_dir(path.parent)
    finally:
        os.unlink(temporary)


def event_record(event_id, ciphertext: bytes) -> dict:
    encoded = bytes(ciphertext).decode("ascii")
    return {
        "version": 1,
        "event_id": str(event_id),
        "ciphertext": encoded,
        "sha256": sha(encoded.encode()),
    }


def read_event(path: Path) -> tuple[dict, dict]:
    require(not path.is_symlink(), "journal_invalid", 503)
    raw = path.read_bytes()
    require(len(raw) <= 64 * 1024, "journal_invalid", 503)
    record = json.loads(raw)
    require(canonical(record) == raw, "journal_invalid", 503)
    require(
        set(record) == {"version", "event_id", "ciphertext", "sha256"} and record["version"] == 1,
        "journal_invalid",
        503,
    )
    event_id = str(UUID(record["event_id"]))
    require(
        path.name == event_id + ".json" and record["event_id"] == event_id, "journal_invalid", 503
    )
    require(
        hmac.compare_digest(sha(record["ciphertext"].encode()), record["sha256"]),
        "journal_invalid",
        503,
    )
    payload = json.loads(decrypt(record["ciphertext"].encode()))
    require(
        set(payload)
        == {"version", "event_id", "user_id", "user_created_at", "requested_at", "policy"},
        "journal_invalid",
        503,
    )
    require(
        payload["version"] == 1 and payload["event_id"] == event_id and payload["policy"] == POLICY,
        "journal_invalid",
        503,
    )
    require(str(UUID(payload["user_id"])) == payload["user_id"], "journal_invalid", 503)
    from datetime import datetime

    for field in ("user_created_at", "requested_at"):
        require(datetime.fromisoformat(payload[field]).tzinfo is not None, "journal_invalid", 503)
    return record, payload


def checkpoint(path: Path) -> tuple[dict, bytes]:
    require(not path.is_symlink(), "journal_checkpoint_invalid", 503)
    raw = path.read_bytes()
    require(len(raw) <= 16 * 1024 * 1024, "journal_checkpoint_invalid", 503)
    value = json.loads(raw)
    require(canonical(value) == raw, "journal_checkpoint_invalid", 503)
    require(
        set(value)
        == {
            "version",
            "sequence",
            "created_at",
            "previous_checkpoint",
            "records",
            "signature",
            "restore_not_before",
        },
        "journal_checkpoint_invalid",
        503,
    )
    signature = value.pop("signature")
    key = hmac.digest(
        settings().encryption_key.encode().strip(), b"can-deletion-journal-checkpoint-v1", "sha256"
    )
    expected = hmac.new(key, canonical(value), "sha256").hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, expected),
        "journal_checkpoint_invalid",
        503,
    )
    require(
        type(value["version"]) is int
        and value["version"] == 1
        and type(value["sequence"]) is int
        and value["sequence"] >= 0,
        "journal_checkpoint_invalid",
        503,
    )
    require(
        (value["sequence"] == 0 and value["previous_checkpoint"] is None)
        or (
            value["sequence"] > 0
            and isinstance(value["previous_checkpoint"], str)
            and bool(HEX.fullmatch(value["previous_checkpoint"]))
        ),
        "journal_checkpoint_invalid",
        503,
    )
    require(isinstance(value["records"], list), "journal_checkpoint_invalid", 503)
    require(
        value["sequence"] != 0
        or (
            value["records"] == []
            and value["previous_checkpoint"] is None
            and value["restore_not_before"] == "1970-01-01T00:00:00Z"
        ),
        "journal_checkpoint_invalid",
        503,
    )
    from datetime import datetime

    created = datetime.fromisoformat(value["created_at"])
    floor = datetime.fromisoformat(value["restore_not_before"])
    require(
        created.tzinfo is not None and floor.tzinfo is not None and floor <= created,
        "journal_checkpoint_invalid",
        503,
    )
    identifiers = set()
    for item in value["records"]:
        require(
            set(item) == {"event_id", "sha256"} and bool(HEX.fullmatch(item["sha256"])),
            "journal_checkpoint_invalid",
            503,
        )
        require(
            str(UUID(item["event_id"])) == item["event_id"] and item["event_id"] not in identifiers,
            "journal_checkpoint_invalid",
            503,
        )
        identifiers.add(item["event_id"])
    require(
        [item["event_id"] for item in value["records"]] == sorted(identifiers),
        "journal_checkpoint_invalid",
        503,
    )
    return value, raw


def publish_and_acknowledged(job: dict) -> bool:
    require(settings().deletion_journal_dir, "deletion_journal_not_configured", 503)
    root = Path(settings().deletion_journal_dir)
    record = event_record(job["id"], job["restore_tombstone_ciphertext"])
    data = canonical(record)
    write_immutable(root / "records" / (record["event_id"] + ".json"), data)
    ack_path = root / "acks" / (record["event_id"] + ".json")
    if not ack_path.exists():
        return False
    require(not ack_path.is_symlink(), "journal_ack_invalid", 503)
    ack = json.loads(ack_path.read_bytes())
    require(
        set(ack) == {"version", "event_id", "sha256", "checkpoint_sha256"},
        "journal_ack_invalid",
        503,
    )
    require(
        ack["version"] == 1
        and ack["event_id"] == record["event_id"]
        and ack["sha256"] == record["sha256"]
        and bool(HEX.fullmatch(ack["checkpoint_sha256"])),
        "journal_ack_invalid",
        503,
    )
    manifest, raw = checkpoint(root / "checkpoints" / (ack["checkpoint_sha256"] + ".json"))
    require(
        sha(raw) == ack["checkpoint_sha256"]
        and {"event_id": record["event_id"], "sha256": sha(data)} in manifest["records"],
        "journal_ack_invalid",
        503,
    )
    return True


def verified_inventory(
    root: Path, *, permitted_unlisted: set[str] | None = None
) -> list[tuple[dict, dict]]:
    manifest, _ = checkpoint(root / "checkpoint.json")
    expected = {item["event_id"] + ".json" for item in manifest["records"]}
    directory = root / "records"
    actual = (
        {p.name for p in directory.iterdir() if not p.name.startswith(".writing-")}
        if directory.exists()
        else set()
    )
    require(
        expected <= actual and actual <= (expected | (permitted_unlisted or set())),
        "journal_inventory_incomplete",
        503,
    )
    result = []
    for item in manifest["records"]:
        path = root / "records" / (item["event_id"] + ".json")
        require(sha(path.read_bytes()) == item["sha256"], "journal_inventory_incomplete", 503)
        result.append(read_event(path))
    return result


def publish_completed(job: dict) -> None:
    """A restore can extend retention; authenticated completion times never move backwards."""
    from datetime import datetime

    value = {
        "version": 1,
        "event_id": str(job["id"]),
        "data_deleted_at": job["data_deleted_at"].isoformat(),
        "completed_at": job["completed_at"].isoformat(),
        "purge_tombstone_after": job["purge_tombstone_after"].isoformat(),
    }
    key = hmac.digest(
        settings().encryption_key.encode().strip(), b"can-deletion-journal-checkpoint-v1", "sha256"
    )
    value["signature"] = hmac.new(key, canonical(value), "sha256").hexdigest()
    path = Path(settings().deletion_journal_dir) / "completed" / (str(job["id"]) + ".json")
    data = canonical(value)
    if not path.exists():
        write_immutable(path, data)
        return
    require(not path.is_symlink(), "journal_completion_invalid", 503)
    prior_raw = path.read_bytes()
    prior = json.loads(prior_raw)
    require(canonical(prior) == prior_raw, "journal_completion_invalid", 503)
    signature = prior.pop("signature")
    require(
        hmac.compare_digest(signature, hmac.new(key, canonical(prior), "sha256").hexdigest()),
        "journal_completion_invalid",
        503,
    )
    require(
        set(prior) == set(value) - {"signature"}
        and prior["event_id"] == value["event_id"]
        and prior["version"] == 1,
        "journal_completion_invalid",
        503,
    )
    for field in ("data_deleted_at", "completed_at", "purge_tombstone_after"):
        require(
            datetime.fromisoformat(value[field]) >= datetime.fromisoformat(prior[field]),
            "journal_completion_regression",
            503,
        )
    if path.read_bytes() == data:
        return
    # SQL row locking serializes completion publishers; replacement is durable and atomic.
    descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_health() -> None:
    """Detect an unavailable independent journal without exposing event or account identifiers."""
    from datetime import datetime

    from .security import now

    try:
        require(settings().deletion_journal_dir, "deletion_journal_not_configured", 503)
        root = Path(settings().deletion_journal_dir)
        head, raw = checkpoint(root / "checkpoint.json")
        status_path = root / "status.json"
        require(not status_path.is_symlink(), "deletion_journal_unhealthy", 503)
        status = json.loads(status_path.read_bytes())
        last_success = datetime.fromisoformat(status["last_success_at"])
        require(last_success.tzinfo is not None, "deletion_journal_unhealthy", 503)
        require(-60 <= (now() - last_success).total_seconds() <= 300, "deletion_journal_stale", 503)
        require(
            status.get("version") == 1
            and status.get("checkpoint_sha256") == sha(raw)
            and status.get("sequence") == head["sequence"]
            and status.get("record_count") == len(head["records"]),
            "deletion_journal_unhealthy",
            503,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        from .errors import Problem

        raise Problem("deletion_journal_unhealthy", 503, retryable=True) from exc
