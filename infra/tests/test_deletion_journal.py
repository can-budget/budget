"""Failure-injection checks for deletion acknowledgment and recovery completeness."""

import datetime as dt
import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "deletion_journal", Path(__file__).parents[1] / "deletion_journal.py"
)
assert SPEC and SPEC.loader
journal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journal)


class MemoryRemote:
    def __init__(self):
        self.files = {}
        self.calls = []
        self.fail_write = None
        self.fail_read = None

    def read(self, name):
        self.calls.append(("read", name))
        if name == self.fail_read or name not in self.files:
            raise journal.JournalError("Simulated remote read failure")
        return self.files[name]

    def put(self, name, data, *, immutable):
        self.calls.append(("put", name))
        if self.fail_write and self.fail_write in name:
            raise journal.JournalError("Simulated remote write failure")
        if immutable and name in self.files and self.files[name] != data:
            raise journal.JournalError("Simulated immutable conflict")
        self.files[name] = data

    def empty(self):
        return not self.files

    def names(self):
        return list(self.files)

    def remove(self, name):
        self.calls.append(("remove", name))
        del self.files[name]


@pytest.fixture
def setup(tmp_path):
    key = os.urandom(44)
    remote = MemoryRemote()
    journal.initialize(remote, key)
    local = tmp_path / "journal"
    (local / "records").mkdir(parents=True)
    return key, remote, local


def add_event(local, event_id=None):
    event_id = event_id or str(uuid.uuid4())
    ciphertext = "encrypted-test-record-" + str(uuid.uuid4())
    value = {
        "version": 1,
        "event_id": event_id,
        "ciphertext": ciphertext,
        "sha256": journal.digest(ciphertext.encode()),
    }
    body = journal.canonical(value)
    (local / "records" / f"{event_id}.json").write_bytes(body)
    return event_id, body


def complete_event(local, key, event_id, deleted_days_ago=40):
    deleted = dt.datetime.now(dt.UTC) - dt.timedelta(days=deleted_days_ago)
    data = journal.canonical(
        journal.sign(
            {
                "version": 1,
                "event_id": event_id,
                "data_deleted_at": deleted.isoformat(),
                "completed_at": deleted.isoformat(),
                "purge_tombstone_after": (deleted + dt.timedelta(days=30)).isoformat(),
            },
            key,
        )
    )
    journal.atomic_file(local / "completed" / f"{event_id}.json", data, immutable=True)
    return data


def backup_fixture(remote, key, anchor, started, name="can-test.dump.age"):
    remote.files[name] = b"encrypted archive fixture"
    remote.files[name + ".sha256"] = b"fixture"
    remote.files[name + ".journal-checkpoint.json"] = anchor
    remote.files[name + ".recovery.json"] = journal.canonical(
        journal.sign(
            {
                "version": 1,
                "archive_sha256": journal.digest(remote.files[name]),
                "snapshot_started_at": started,
                "journal_checkpoint_sha256": journal.digest(anchor),
            },
            key,
        )
    )


def test_no_ack_until_event_and_full_checkpoint_are_verified(setup):
    key, remote, local = setup
    event_id, body = add_event(local)
    status = journal.replicate(local, remote, key)
    ack = json.loads((local / "acks" / f"{event_id}.json").read_bytes())
    assert ack["sha256"] == journal.record(body, event_id)["sha256"]
    head = remote.files["LATEST.json"]
    assert ack["checkpoint_sha256"] == journal.digest(head)
    assert (local / "checkpoints" / f"{journal.digest(head)}.json").read_bytes() == head
    assert status["record_count"] == 1
    assert remote.calls.index(
        ("read", f"records/{event_id}.json")
    ) < remote.calls.index(("put", "LATEST.json"), 3)
    assert remote.calls[-1] == ("read", "LATEST.json")


@pytest.mark.parametrize("failure", ["records/", "checkpoints/", "LATEST.json"])
def test_partial_publication_does_not_acknowledge_or_update_health(setup, failure):
    key, remote, local = setup
    add_event(local)
    remote.fail_write = failure
    with pytest.raises(journal.JournalError):
        journal.replicate(local, remote, key)
    assert not (local / "acks").exists()
    assert not (local / "status.json").exists()


def test_readback_failure_never_acknowledges(setup):
    key, remote, local = setup
    event_id, _ = add_event(local)
    remote.fail_read = f"records/{event_id}.json"
    with pytest.raises(journal.JournalError):
        journal.replicate(local, remote, key)
    assert not (local / "acks").exists()


def test_restart_after_remote_commit_before_local_ack_is_idempotent(setup, monkeypatch):
    key, remote, local = setup
    event_id, _ = add_event(local)
    original = journal.atomic_file

    def crash(path, data, **kwargs):
        if path.parent.name == "acks":
            raise OSError("simulated process crash")
        original(path, data, **kwargs)

    monkeypatch.setattr(journal, "atomic_file", crash)
    with pytest.raises(OSError):
        journal.replicate(local, remote, key)
    remote_head = remote.files["LATEST.json"]
    monkeypatch.setattr(journal, "atomic_file", original)
    journal.replicate(local, remote, key)
    assert remote.files["LATEST.json"] == remote_head
    assert (local / "acks" / f"{event_id}.json").exists()


def test_missing_previously_committed_event_blocks_new_ack_and_fetch(setup, tmp_path):
    key, remote, local = setup
    first_id, _ = add_event(local)
    journal.replicate(local, remote, key)
    second_id, _ = add_event(local)
    del remote.files[f"records/{first_id}.json"]
    with pytest.raises(journal.JournalError):
        journal.replicate(local, remote, key)
    assert not (local / "acks" / f"{second_id}.json").exists()
    destination = tmp_path / "recovery"
    with pytest.raises(journal.JournalError):
        journal.fetch(remote, destination, key)
    assert not destination.exists()


def test_replication_never_prunes_remote_history_missing_locally(setup):
    key, remote, local = setup
    event_id, _ = add_event(local)
    journal.replicate(local, remote, key)
    (local / "records" / f"{event_id}.json").unlink()
    status = journal.replicate(local, remote, key)
    assert status["record_count"] == 1
    assert f"records/{event_id}.json" in remote.files


def test_latest_journal_includes_deletions_after_old_database_backup(setup, tmp_path):
    key, remote, local = setup
    old_backup_head = remote.files["LATEST.json"]
    first_id, _ = add_event(local)
    journal.replicate(local, remote, key)
    second_id, _ = add_event(local)
    journal.replicate(local, remote, key)
    bundle = tmp_path / "recovery"
    result = journal.fetch(remote, bundle, key)
    assert result["record_count"] == 2
    assert (bundle / "checkpoint.json").read_bytes() != old_backup_head
    assert {p.stem for p in (bundle / "records").glob("*.json")} == {
        first_id,
        second_id,
    }


def test_unauthenticated_inventory_removal_cannot_pass(setup, tmp_path):
    key, remote, local = setup
    add_event(local)
    journal.replicate(local, remote, key)
    damaged = json.loads(remote.files["LATEST.json"])
    damaged["records"] = []
    remote.files["LATEST.json"] = journal.canonical(damaged)
    with pytest.raises(journal.JournalError):
        journal.fetch(remote, tmp_path / "recovery", key)


def test_wrong_key_and_corrupt_ciphertext_fail_closed(setup, tmp_path):
    key, remote, local = setup
    event_id, _ = add_event(local)
    journal.replicate(local, remote, key)
    with pytest.raises(journal.JournalError):
        journal.fetch(remote, tmp_path / "wrong-key", os.urandom(44))
    damaged = json.loads(remote.files[f"records/{event_id}.json"])
    damaged["ciphertext"] += "corrupted"
    remote.files[f"records/{event_id}.json"] = journal.canonical(damaged)
    with pytest.raises(journal.JournalError):
        journal.fetch(remote, tmp_path / "corrupt", key)


def test_rollback_behind_acknowledged_checkpoint_stops_replication(setup):
    key, remote, local = setup
    prior = remote.files["LATEST.json"]
    add_event(local)
    journal.replicate(local, remote, key)
    remote.files["LATEST.json"] = prior
    with pytest.raises(journal.JournalError):
        journal.replicate(local, remote, key)


def test_genesis_is_explicit_and_only_allowed_on_empty_remote(setup):
    key, remote, _ = setup
    with pytest.raises(journal.JournalError):
        journal.initialize(remote, key)
    del remote.files["LATEST.json"]
    with pytest.raises(journal.JournalError):
        journal.initialize(remote, key)


def test_uninitialized_or_unavailable_remote_does_not_mean_empty_journal(tmp_path):
    with pytest.raises(journal.JournalError):
        journal.fetch(MemoryRemote(), tmp_path / "recovery", os.urandom(44))
    assert not (tmp_path / "recovery").exists()


def test_fetch_does_not_overwrite_existing_recovery_bundle(setup, tmp_path):
    key, remote, _ = setup
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "preserve"
    sentinel.write_text("unchanged")
    with pytest.raises(journal.JournalError):
        journal.fetch(remote, destination, key)
    assert sentinel.read_text() == "unchanged"


def test_atomic_immutable_file_cannot_be_overwritten(tmp_path):
    path = tmp_path / "immutable.json"
    journal.atomic_file(path, b"first", immutable=True)
    journal.atomic_file(path, b"first", immutable=True)
    with pytest.raises(journal.JournalError):
        journal.atomic_file(path, b"second", immutable=True)
    assert path.read_bytes() == b"first"


@pytest.mark.parametrize(
    "identifier", ["../escape", "UPPERCASE", "", "00000000-0000-0000-0000-00000000000Z"]
)
def test_untrusted_event_names_cannot_escape_record_directory(identifier):
    with pytest.raises(journal.JournalError):
        journal.event_name(identifier)


def test_prune_waits_for_both_minimum_age_and_verified_backup_floor(setup):
    key, remote, local = setup
    old, _ = add_event(local)
    young, _ = add_event(local)
    complete_event(local, key, old)
    complete_event(local, key, young, deleted_days_ago=2)
    journal.replicate(local, remote, key)
    backups = MemoryRemote()
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    backup_fixture(backups, key, remote.files["LATEST.json"], cutoff)
    result = journal.prune(local, remote, backups, key, cutoff)
    assert result["retired_events"] == 1
    assert f"records/{old}.json" not in remote.files
    assert f"completed/{old}.json" not in remote.files
    assert not (local / "records" / f"{old}.json").exists()
    assert f"records/{young}.json" in remote.files
    assert len([name for name in remote.files if name.startswith("checkpoints/")]) == 1
    assert journal.replicate(local, remote, key)["record_count"] == 1


def test_old_snapshot_cannot_be_restored_after_authenticated_floor_advanced(
    setup, tmp_path
):
    key, remote, local = setup
    anchor = remote.files["LATEST.json"]
    start = (dt.datetime.now(dt.UTC) - dt.timedelta(days=3)).isoformat()
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    journal.prune(local, remote, MemoryRemote(), key, cutoff)
    with pytest.raises(journal.JournalError, match="predates"):
        journal.fetch(remote, tmp_path / "restore", key, anchor, start)
    assert not (tmp_path / "restore").exists()


def test_long_dump_start_before_floor_blocks_prune_even_if_upload_finished_later(setup):
    key, remote, local = setup
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).isoformat()
    backups = MemoryRemote()
    backup_fixture(
        backups,
        key,
        remote.files["LATEST.json"],
        (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
    )
    before = dict(remote.files)
    with pytest.raises(journal.JournalError, match="predates"):
        journal.prune(local, remote, backups, key, cutoff)
    assert remote.files == before


@pytest.mark.parametrize(
    "missing", [".recovery.json", ".journal-checkpoint.json", ".sha256", ""]
)
def test_prune_refuses_partial_or_orphan_backup_inventory(setup, missing):
    key, remote, local = setup
    backups = MemoryRemote()
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    backup_fixture(backups, key, remote.files["LATEST.json"], cutoff)
    del backups.files["can-test.dump.age" + missing]
    with pytest.raises(journal.JournalError):
        journal.prune(local, remote, backups, key, cutoff)


def test_prune_failure_after_committed_floor_does_not_resurrect_retired_event(
    setup, monkeypatch
):
    key, remote, local = setup
    event_id, _ = add_event(local)
    complete_event(local, key, event_id)
    journal.replicate(local, remote, key)
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    original = remote.remove

    def failed(name):
        if name == f"completed/{event_id}.json":
            raise journal.JournalError("Simulated deletion failure")
        original(name)

    monkeypatch.setattr(remote, "remove", failed)
    with pytest.raises(journal.JournalError):
        journal.prune(local, remote, MemoryRemote(), key, cutoff)
    assert journal.replicate(local, remote, key)["record_count"] == 0
    monkeypatch.setattr(remote, "remove", original)
    assert (
        journal.prune(local, remote, MemoryRemote(), key, cutoff)["retired_events"] == 1
    )
    assert not (local / "completed" / f"{event_id}.json").exists()


def test_prune_recovers_after_remote_erasure_before_local_cleanup(setup):
    key, remote, local = setup
    event_id, body = add_event(local)
    completed = complete_event(local, key, event_id)
    journal.replicate(local, remote, key)
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    journal.prune(local, remote, MemoryRemote(), key, cutoff)
    # Reconstruct the disk state at the crash boundary after remote deletion.
    journal.atomic_file(local / "records" / f"{event_id}.json", body)
    journal.atomic_file(local / "completed" / f"{event_id}.json", completed)
    assert journal.replicate(local, remote, key)["record_count"] == 0
    assert (
        journal.prune(local, remote, MemoryRemote(), key, cutoff)["retired_events"] == 1
    )
    assert not (local / "records" / f"{event_id}.json").exists()


def test_authenticated_backup_envelope_detects_anchor_replacement(setup):
    key, remote, local = setup
    backup = MemoryRemote()
    anchor = remote.files["LATEST.json"]
    backup_fixture(backup, key, anchor, journal.timestamp())
    add_event(local)
    journal.replicate(local, remote, key)
    with pytest.raises(journal.JournalError, match="anchor"):
        journal.backup_envelope(
            backup.files["can-test.dump.age.recovery.json"],
            key,
            remote.files["LATEST.json"],
        )


def test_actual_backend_and_infra_journal_protocol_interoperate(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from can_api import deletion_journal as backend
    from can_api.config import settings

    key = Fernet.generate_key()
    local = tmp_path / "runtime"
    local.mkdir()
    monkeypatch.setenv("CAN_ENCRYPTION_KEY", key.decode())
    monkeypatch.setenv("CAN_ENCRYPTION_KEY_FILE", "")
    monkeypatch.setenv("CAN_DELETION_JOURNAL_DIR", str(local))
    settings.cache_clear()
    try:
        remote = MemoryRemote()
        journal.initialize(remote, key)
        empty_bundle = tmp_path / "empty-recovery"
        journal.fetch(remote, empty_bundle, key)
        assert backend.verified_inventory(empty_bundle) == []
        event_id = str(uuid.uuid4())
        payload = {
            "version": 1,
            "event_id": event_id,
            "user_id": str(uuid.uuid4()),
            "user_created_at": journal.timestamp(),
            "requested_at": journal.timestamp(),
            "policy": "retain_ledger_anonymize_v1",
        }
        job = {
            "id": event_id,
            "restore_tombstone_ciphertext": Fernet(key).encrypt(
                journal.canonical(payload)
            ),
        }
        assert backend.publish_and_acknowledged(job) is False
        journal.replicate(local, remote, key)
        assert backend.publish_and_acknowledged(job) is True
        bundle = tmp_path / "recovery"
        journal.fetch(remote, bundle, key)
        inventory = backend.verified_inventory(bundle)
        assert len(inventory) == 1 and inventory[0][1] == payload
        deleted = dt.datetime.now(dt.UTC) - dt.timedelta(days=40)
        backend.publish_completed(
            {
                **job,
                "data_deleted_at": deleted,
                "completed_at": deleted,
                "purge_tombstone_after": deleted + dt.timedelta(days=30),
            }
        )
        journal.replicate(local, remote, key)
        assert (
            journal.prune(local, remote, MemoryRemote(), key, journal.timestamp())[
                "retired_events"
            ]
            == 1
        )
        manifest, _ = backend.checkpoint(local / "checkpoint.json")
        assert manifest["records"] == []
    finally:
        settings.cache_clear()


def test_repeated_restore_extends_completion_retention_without_changing_event(setup):
    key, remote, local = setup
    event_id, body = add_event(local)
    completed = complete_event(local, key, event_id)
    journal.replicate(local, remote, key)
    path = local / "completed" / f"{event_id}.json"
    path.unlink()
    extended = complete_event(local, key, event_id, deleted_days_ago=2)
    journal.replicate(local, remote, key)
    assert remote.files[f"completed/{event_id}.json"] == extended
    assert remote.files[f"records/{event_id}.json"] == body
    assert (
        journal.prune(local, remote, MemoryRemote(), key, journal.timestamp())[
            "retired_events"
        ]
        == 0
    )
    path.write_bytes(completed)
    with pytest.raises(journal.JournalError, match="backwards"):
        journal.replicate(local, remote, key)
    assert remote.files[f"completed/{event_id}.json"] == extended


def test_retention_cannot_commit_on_rolled_back_remote_head(setup):
    key, remote, local = setup
    old = remote.files["LATEST.json"]
    add_event(local)
    journal.replicate(local, remote, key)
    remote.files["LATEST.json"] = old
    with pytest.raises(journal.JournalError, match="regressed"):
        journal.prune(local, remote, MemoryRemote(), key, journal.timestamp())


def test_uncommitted_remote_event_blocks_restore_until_producer_finishes_checkpoint(
    setup, tmp_path
):
    key, remote, local = setup
    event_id, body = add_event(local)
    remote.files[f"records/{event_id}.json"] = body
    with pytest.raises(journal.JournalError, match="uncommitted"):
        journal.fetch(remote, tmp_path / "restore", key)
    assert not (tmp_path / "restore").exists()
    journal.replicate(local, remote, key)
    assert journal.fetch(remote, tmp_path / "restore", key)["record_count"] == 1


def test_unknown_remote_event_blocks_replication_and_retention(setup):
    key, remote, local = setup
    event_id, body = add_event(local)
    remote.files[f"records/{event_id}.json"] = body
    (local / "records" / f"{event_id}.json").unlink()
    with pytest.raises(journal.JournalError, match="no matching local producer"):
        journal.replicate(local, remote, key)
    with pytest.raises(journal.JournalError, match="Uncommitted"):
        journal.prune(local, remote, MemoryRemote(), key, journal.timestamp())
    assert not (local / "acks").exists()
