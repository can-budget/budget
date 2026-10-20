import hashlib
import hmac
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from can_api import deletion_journal as journal
from can_api import deletions, security
from can_api.config import Settings
from can_api.errors import Problem


@pytest.fixture
def deletion_config(monkeypatch, tmp_path):
    config = Settings(
        encryption_key=Fernet.generate_key().decode(), deletion_journal_dir=str(tmp_path)
    )
    for module in (journal, deletions, security):
        monkeypatch.setattr(module, "settings", lambda: config)
    return config


def signed_checkpoint(root: Path, cfg, records: list[dict]) -> str:
    entries = []
    for record in records:
        data = journal.canonical(record)
        journal.write_immutable(root / "records" / (record["event_id"] + ".json"), data)
        entries.append({"event_id": record["event_id"], "sha256": journal.sha(data)})
    value = {
        "version": 1,
        "sequence": 1,
        "created_at": security.now().isoformat(),
        "previous_checkpoint": "0" * 64,
        "restore_not_before": "1970-01-01T00:00:00Z",
        "records": sorted(entries, key=lambda item: item["event_id"]),
    }
    key = hmac.digest(cfg.encryption_key.encode(), b"can-deletion-journal-checkpoint-v1", "sha256")
    value["signature"] = hmac.new(key, journal.canonical(value), "sha256").hexdigest()
    raw = journal.canonical(value)
    digest = journal.sha(raw)
    journal.write_immutable(root / "checkpoints" / (digest + ".json"), raw)
    (root / "checkpoint.json").write_bytes(raw)
    for record in records:
        ack = {
            "version": 1,
            "event_id": record["event_id"],
            "sha256": record["sha256"],
            "checkpoint_sha256": digest,
        }
        journal.write_immutable(
            root / "acks" / (record["event_id"] + ".json"), journal.canonical(ack)
        )
    return digest


def sample_event():
    payload = {
        "version": 1,
        "event_id": str(uuid4()),
        "user_id": str(uuid4()),
        "user_created_at": security.now().isoformat(),
        "requested_at": security.now().isoformat(),
        "policy": journal.POLICY,
    }
    ciphertext = security.encrypt(json.dumps(payload))
    return payload, {"id": payload["event_id"], "restore_tombstone_ciphertext": ciphertext}


def test_journal_requires_independent_signed_ack_and_is_immutable(deletion_config):
    payload, job = sample_event()
    assert not journal.publish_and_acknowledged(job)
    root = Path(deletion_config.deletion_journal_dir)
    record = journal.event_record(job["id"], job["restore_tombstone_ciphertext"])
    assert (
        payload["user_id"] not in (root / "records" / (payload["event_id"] + ".json")).read_text()
    )
    signed_checkpoint(root, deletion_config, [record])
    assert journal.publish_and_acknowledged(job)
    assert journal.verified_inventory(root)[0][1] == payload
    job["restore_tombstone_ciphertext"] = security.encrypt("changed")
    with pytest.raises(Problem, match="journal_event_conflict"):
        journal.publish_and_acknowledged(job)


def test_missing_tampered_or_forged_inventory_never_replays(deletion_config):
    _, job = sample_event()
    root = Path(deletion_config.deletion_journal_dir)
    record = journal.event_record(job["id"], job["restore_tombstone_ciphertext"])
    signed_checkpoint(root, deletion_config, [record])
    file = root / "records" / (str(job["id"]) + ".json")
    original = file.read_bytes()
    file.write_bytes(original + b" ")
    with pytest.raises(Problem):
        journal.verified_inventory(root)
    file.write_bytes(original)
    checkpoint = root / "checkpoint.json"
    original_checkpoint = checkpoint.read_bytes()
    value = json.loads(original_checkpoint)
    value["records"] = []
    checkpoint.write_bytes(journal.canonical(value))
    with pytest.raises(Problem):
        journal.verified_inventory(root)
    checkpoint.write_bytes(original_checkpoint)
    file.unlink()
    with pytest.raises(Problem):
        journal.verified_inventory(root)


def test_scrub_nested_audit_and_receipt_preserves_other_people_and_money():
    user, member, other = str(uuid4()), str(uuid4()), str(uuid4())
    before = {
        "author_id": user,
        "author_name": "Old historical name",
        "last_editor_id": other,
        "last_editor_name": "Survivor",
        "membership_id": member,
        "amount_minor": 12345,
        "occurred_at": "2026-01-04T12:13:14Z",
        "revision": 9,
    }
    value = {
        "before_state": before,
        "after_state": {
            **before,
            "author_id": other,
            "author_name": "Survivor",
            "last_editor_id": user,
            "last_editor_name": "New name",
        },
        "members": [
            {"user_id": user, "display_name": "New name"},
            {"user_id": other, "display_name": "New name"},
        ],
    }
    cleaned = deletions.scrub_json(value, user, {user, member})
    assert cleaned["before_state"]["author_id"] is None
    assert cleaned["before_state"]["author_name"] is None
    assert cleaned["after_state"]["last_editor_name"] is None
    assert cleaned["before_state"]["last_editor_name"] == "Survivor"
    assert cleaned["members"] == [{"user_id": other, "display_name": "New name"}]
    for field in ("amount_minor", "occurred_at", "revision"):
        assert cleaned["before_state"][field] == before[field]
    assert user not in json.dumps(cleaned) and member not in json.dumps(cleaned)
    assert value["before_state"] == before


def test_only_exact_original_deletion_proof_can_replay(monkeypatch):
    proof = "test-proof-" + str(uuid4())
    result = {"job_id": str(uuid4()), "status": "accepted"}
    monkeypatch.setattr(
        deletions,
        "one",
        lambda *a, **k: {"reauth_proof_hash": security.digest(proof), "accepted_json": result},
    )
    assert deletions.accepted_replay(None, uuid4(), proof, True) == result
    for candidate, confirmed in ((None, True), ("wrong-proof", True), (proof, False)):
        with pytest.raises(Problem) as caught:
            deletions.accepted_replay(None, uuid4(), candidate, confirmed)
        assert caught.value.status == 401


@pytest.mark.parametrize("status", [200, 400, 429, 500])
def test_apple_revoke_empty_200_only_is_success(deletion_config, monkeypatch, status):
    sent = []
    monkeypatch.setattr(deletions, "apple_client_secret", lambda: "test-client-secret")

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(status)

    monkeypatch.setattr(deletions.httpx, "post", post)
    record = {"token_type": "refresh_token", "token": "test-refresh"}
    if status == 200:
        deletions.revoke_apple_token(record)
    else:
        with pytest.raises(Problem) as caught:
            deletions.revoke_apple_token(record)
        assert caught.value.body["retryable"]
    assert sent[0][0] == "https://appleid.apple.com/auth/revoke"
    assert sent[0][1]["data"]["token_type_hint"] == "refresh_token"


def test_checkpoint_key_domain_separation():
    key = b"test-key"
    derived = hmac.digest(key, b"can-deletion-journal-checkpoint-v1", "sha256")
    assert derived != hashlib.sha256(key).digest()


def test_apple_notification_accepts_exactly_one_documented_payload():
    from pydantic import ValidationError

    from can_api.auth import Notification

    assert Notification(payload="signed-jwt").payload == "signed-jwt"
    assert Notification(signedPayload="legacy-jwt").signedPayload == "legacy-jwt"
    for body in (
        {},
        {"payload": "a", "signedPayload": "b"},
        {"payload": "a", "unexpected": True},
        {"payload": "a", "signedPayload": None},
        {"signedPayload": "a", "payload": None},
        {"payload": None},
        {"signedPayload": None},
    ):
        with pytest.raises(ValidationError):
            Notification.model_validate(body)


def test_unrecoverable_historical_apple_token_does_not_block_local_deletion(
    deletion_config, monkeypatch
):
    valid = security.encrypt("test-valid-current-token")
    lost = Fernet(Fernet.generate_key()).encrypt(b"old-key-unavailable")
    monkeypatch.setattr(
        deletions,
        "rows",
        lambda *a, **k: [
            {"token_type": "refresh_token", "ciphertext": lost},
            {"token_type": "refresh_token", "ciphertext": valid},
        ],
    )
    monkeypatch.setattr(deletions, "one", lambda *a, **k: None)
    tokens, unavailable = deletions.retained_tokens(None, uuid4())
    assert tokens == [{"token_type": "refresh_token", "token": "test-valid-current-token"}]
    assert unavailable


def test_replayed_erasure_can_extend_but_never_shorten_retention(deletion_config):
    from datetime import timedelta

    current = security.now()
    job = {
        "id": uuid4(),
        "data_deleted_at": current,
        "completed_at": current,
        "purge_tombstone_after": current + timedelta(days=30),
    }
    journal.publish_completed(job)
    journal.publish_completed(job)
    extended = {
        key: value + timedelta(days=2) if key != "id" else value for key, value in job.items()
    }
    journal.publish_completed(extended)
    with pytest.raises(Problem, match="journal_completion_regression"):
        journal.publish_completed(job)


@pytest.mark.parametrize(
    "change",
    [
        {"previous_checkpoint": None},
        {"created_at": "2026-01-01T00:00:00"},
        {"restore_not_before": "2099-01-01T00:00:00Z"},
        {
            "created_at": "2026-01-01T00:00:00Z",
            "restore_not_before": "2026-01-01T00:00:00.000001Z",
        },
        {"sequence": True},
        {
            "records": [
                {"event_id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "sha256": "0" * 64},
                {"event_id": "00000000-0000-0000-0000-000000000000", "sha256": "0" * 64},
            ]
        },
    ],
)
def test_signed_but_malformed_checkpoint_is_rejected(deletion_config, change):
    root = Path(deletion_config.deletion_journal_dir)
    _, job = sample_event()
    signed_checkpoint(
        root,
        deletion_config,
        [journal.event_record(job["id"], job["restore_tombstone_ciphertext"])],
    )
    path = root / "checkpoint.json"
    value = json.loads(path.read_bytes())
    value.pop("signature")
    value.update(change)
    key = hmac.digest(
        deletion_config.encryption_key.encode(), b"can-deletion-journal-checkpoint-v1", "sha256"
    )
    value["signature"] = hmac.new(key, journal.canonical(value), "sha256").hexdigest()
    path.write_bytes(journal.canonical(value))
    with pytest.raises(Problem):
        journal.checkpoint(path)


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_delete_confirmation_is_strict_true(confirmation):
    from pydantic import ValidationError

    from can_api.schemas import DeleteAccount

    with pytest.raises(ValidationError):
        DeleteAccount.model_validate(
            {"command_id": str(uuid4()), "confirm_shared_history_changes": confirmation}
        )


def test_journal_health_requires_recent_matching_independent_checkpoint(deletion_config):
    from datetime import timedelta

    root = Path(deletion_config.deletion_journal_dir)
    _, job = sample_event()
    digest = signed_checkpoint(
        root,
        deletion_config,
        [journal.event_record(job["id"], job["restore_tombstone_ciphertext"])],
    )
    status = {
        "version": 1,
        "last_success_at": security.now().isoformat(),
        "checkpoint_sha256": digest,
        "sequence": 1,
        "record_count": 1,
    }
    (root / "status.json").write_bytes(journal.canonical(status))
    journal.check_health()
    status["last_success_at"] = (security.now() - timedelta(minutes=6)).isoformat()
    (root / "status.json").write_bytes(journal.canonical(status))
    with pytest.raises(Problem, match="deletion_journal_stale"):
        journal.check_health()
    status["last_success_at"] = security.now().isoformat()
    status["checkpoint_sha256"] = "0" * 64
    (root / "status.json").write_bytes(journal.canonical(status))
    with pytest.raises(Problem, match="deletion_journal_unhealthy"):
        journal.check_health()


@pytest.mark.parametrize("overdue", [False, True])
@pytest.mark.parametrize("mode", ["off_host", "single_server"])
def test_readiness_checks_deletion_deadline_and_journal(monkeypatch, overdue, mode):
    from contextlib import nullcontext

    from can_api import app

    class Database:
        def connect(self):
            return nullcontext(None)

    monkeypatch.setattr(app, "engine", lambda: Database())
    monkeypatch.setattr(app, "settings", lambda: Settings(recovery_mode=mode))

    def query(_connection, sql):
        if "alembic_version" in sql:
            return {"version_num": "0004"}
        assert "interval '24 hours'" in sql
        return {"id": uuid4()} if overdue else None

    monkeypatch.setattr(app, "one", query)
    checked = []
    monkeypatch.setattr(journal, "check_health", lambda: checked.append(True))
    if overdue:
        with pytest.raises(Problem, match="deletion_overdue"):
            app.ready()
        assert not checked
    else:
        response = app.ready()
        assert response["status"] == "ok"
        assert response["recovery_mode"] == mode
        assert checked == ([True] if mode == "off_host" else [])


@pytest.mark.parametrize("mode", ["", "disabled", "single-server"])
def test_unknown_recovery_modes_are_rejected(mode):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(recovery_mode=mode)
