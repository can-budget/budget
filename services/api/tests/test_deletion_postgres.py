"""Real PostgreSQL privacy/lost-response/concurrency/restore tests, explicitly gated by URL."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_deletions import signed_checkpoint
from test_postgres import add_member, purchase, seed_user, setup_budget

from can_api import deletion_journal as journal
from can_api import deletion_replay, deletions
from can_api.db import execute, one, rows
from can_api.errors import Problem
from can_api.ledger import encode
from can_api.security import digest, encrypt, now

pytestmark = pytest.mark.postgres


def deletion_request(client, headers, postgres, tmp_path, *, token_count=2):
    cfg = deletions.settings()
    cfg.deletion_journal_dir = str(tmp_path)
    user = client.get("/v1/me", headers=headers).json()["id"]
    proof = "test-deletion-proof-" + str(uuid4())
    with postgres.begin() as c:
        execute(
            c,
            "INSERT INTO reauth_tokens(token_hash,user_id,expires_at) VALUES (:hash,:user,:expires)",
            hash=digest(proof),
            user=user,
            expires=now() + timedelta(minutes=5),
        )
        for _ in range(token_count):
            token = "test-apple-refresh-" + str(uuid4())
            execute(
                c,
                "INSERT INTO identity_tokens(id,user_id,token_hash,token_type,ciphertext) VALUES (:id,:user,:hash,'refresh_token',:cipher)",
                id=uuid4(),
                user=user,
                hash=digest(token),
                cipher=encrypt(token),
            )
    body = {"command_id": str(uuid4()), "confirm_shared_history_changes": True}
    response = client.request(
        "DELETE", "/v1/me", headers=headers | {"X-Reauth-Token": proof}, json=body
    )
    assert response.status_code == 202, response.text
    return user, proof, body, response.json()


def acknowledge(postgres, root):
    with postgres.connect() as c:
        jobs = rows(c, "SELECT * FROM deletion_jobs")
    records = [journal.event_record(job["id"], job["restore_tombstone_ciphertext"]) for job in jobs]
    signed_checkpoint(root, deletions.settings(), records)
    with postgres.begin() as c:
        execute(c, "UPDATE deletion_jobs SET next_attempt_at=now()")


def test_deletion_reauth_immediate_revocation_and_lost_response(postgres, tmp_path, monkeypatch):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    command = purchase(sprint, membership)
    assert (
        client.post(f"/v1/cabinets/{cabinet}/commands", headers=headers, json=command).status_code
        == 200
    )
    user, proof, body, accepted = deletion_request(client, headers, postgres, tmp_path)
    assert client.get("/v1/me", headers=headers).status_code == 401
    assert client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).status_code == 401
    with postgres.begin() as c:
        execute(c, "UPDATE reauth_tokens SET expires_at=now()-interval '1 hour'")
        assert one(c, "SELECT archived_at FROM cabinets WHERE id=:id", id=cabinet)["archived_at"]
    # Exact capability may replay after access and original proof expiry; no identity bypass for new requests.
    assert (
        client.request("DELETE", "/v1/me", headers={"X-Reauth-Token": proof}, json=body).json()
        == accepted
    )
    assert (
        client.request(
            "DELETE", "/v1/me", headers={"X-Reauth-Token": "wrong"}, json=body
        ).status_code
        == 401
    )
    assert (
        client.request(
            "DELETE",
            "/v1/me",
            headers={"X-Reauth-Token": proof},
            json=body | {"command_id": str(uuid4())},
        ).status_code
        == 401
    )
    assert (
        client.request(
            "DELETE",
            "/v1/me",
            headers={"X-Reauth-Token": proof},
            json=body | {"confirm_shared_history_changes": False},
        ).status_code
        != 202
    )
    calls = []
    monkeypatch.setattr(deletions, "revoke_apple_token", lambda record: calls.append(record))
    assert deletions.cycle()  # Publishing without an independent ACK must not erase the user.
    with postgres.connect() as c:
        assert one(c, "SELECT id FROM users WHERE id=:id", id=user)
    assert not calls
    acknowledge(postgres, tmp_path)
    assert deletions.cycle()
    with postgres.connect() as c:
        assert one(c, "SELECT id FROM users WHERE id=:id", id=user) is None
        assert one(c, "SELECT count(*) AS n FROM entries")["n"] == 1
        assert one(c, "SELECT status FROM deletion_jobs")["status"] == "done"
    assert len(calls) == 2
    assert (
        client.request("DELETE", "/v1/me", headers={"X-Reauth-Token": proof}, json=body).json()
        == accepted
    )


def test_anonymizes_all_history_without_changing_shared_finance_or_survivor_receipts(
    postgres, tmp_path, monkeypatch
):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    other = add_member(client, postgres, headers, cabinet)
    survivor = client.get("/v1/me", headers=other).json()["id"]
    user = client.get("/v1/me", headers=headers).json()["id"]
    path = f"/v1/cabinets/{cabinet}/commands"
    original = purchase(sprint, membership, 111_111)
    assert client.post(path, headers=headers, json=original).status_code == 200
    edit = {
        "command_id": str(uuid4()),
        "type": "correct_entry",
        "payload": {
            "sprint_id": sprint,
            "entry_id": original["payload"]["entry_id"],
            "kind": "expense",
            "amount_minor": 222_222,
            "expected_entry_revision": 1,
            "allow_completed": False,
        },
    }
    assert client.post(path, headers=other, json=edit).status_code == 200
    # Persist a realistic cached conflict with before/after identity and another member's audit name.
    conflict = {**edit, "command_id": str(uuid4())}
    assert client.post(path, headers=other, json=conflict).status_code == 409
    with postgres.begin() as c:
        error = {
            "code": "stale_entry_revision",
            "retryable": False,
            "message": "Conflict",
            "details": {
                "entry": {
                    "author_id": user,
                    "author_name": "Deleted Name",
                    "last_editor_id": survivor,
                    "last_editor_name": "Survivor",
                    "membership_id": membership,
                    "amount_minor": 222_222,
                }
            },
        }
        execute(
            c,
            "UPDATE command_receipts SET error_json=CAST(:error AS jsonb) WHERE command_id=:id",
            error=encode(error),
            id=conflict["command_id"],
        )
    before = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=other).json()["projection"]
    deletion_request(client, headers, postgres, tmp_path)
    acknowledge(postgres, tmp_path)
    monkeypatch.setattr(deletions, "revoke_apple_token", lambda _: None)
    assert deletions.cycle()
    after = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=other).json()
    assert after["projection"] == before
    assert len(after["cabinet"]["members"]) == 1
    with postgres.connect() as c:
        entry = one(c, "SELECT * FROM entries")
        assert entry["author_id"] is None and entry["membership_id"] is None
        assert str(entry["last_editor_id"]) == survivor and entry["amount_minor"] == 222_222
        assert entry["occurred_at"].isoformat() == original["payload"]["occurred_at"]
        for row in rows(c, "SELECT before_state,after_state FROM entry_revisions"):
            assert user not in encode(row) and membership not in encode(row)
        retained = one(
            c, "SELECT * FROM command_receipts WHERE command_id=:id", id=conflict["command_id"]
        )
        assert retained["error_json"]["details"]["entry"]["author_name"] is None
        assert retained["error_json"]["details"]["entry"]["last_editor_name"] == "Survivor"
        assert user not in encode(retained) and membership not in encode(retained)
        assert one(c, "SELECT id FROM users WHERE id=:id", id=survivor)
    assert client.post(path, headers=other, json=edit).status_code == 200


def test_apple_outage_does_not_retain_account_and_stale_lease_cannot_finish(
    postgres, tmp_path, monkeypatch
):
    client, headers, _, _, _ = setup_budget(postgres)
    user, _, _, accepted = deletion_request(client, headers, postgres, tmp_path)
    acknowledge(postgres, tmp_path)

    def unavailable(_):
        raise Problem("apple_revoke_unavailable", 503, retryable=True)

    monkeypatch.setattr(deletions, "revoke_apple_token", unavailable)
    assert deletions.cycle()
    with postgres.begin() as c:
        assert one(c, "SELECT id FROM users WHERE id=:id", id=user) is None
        job = one(c, "SELECT * FROM deletion_jobs")
        assert job["status"] == "pending" and job["apple_tokens_ciphertext"]
        execute(c, "UPDATE deletion_jobs SET next_attempt_at=now()")

    def lease_stolen(_):
        with postgres.begin() as c:
            execute(
                c,
                "UPDATE deletion_jobs SET lease_token=:lease,status='processing',lease_expires_at=now()+interval '5 minutes'",
                lease=uuid4(),
            )

    monkeypatch.setattr(deletions, "revoke_apple_token", lease_stolen)
    assert deletions.cycle()
    with postgres.connect() as c:
        assert (
            one(c, "SELECT status FROM deletion_jobs WHERE id=:id", id=accepted["job_id"])["status"]
            == "processing"
        )
    assert not deletions.cycle()


def test_independent_tombstone_replays_old_database_and_never_deletes_recreated_account(
    postgres, tmp_path, monkeypatch
):
    client, headers, _, _, _ = setup_budget(postgres)
    user = client.get("/v1/me", headers=headers).json()["id"]
    with postgres.connect() as c:
        old = one(c, "SELECT * FROM users WHERE id=:id", id=user)
    event = {
        "version": 1,
        "event_id": str(uuid4()),
        "user_id": user,
        "user_created_at": old["created_at"].isoformat(),
        "requested_at": now().isoformat(),
        "policy": journal.POLICY,
    }
    deletions.settings().deletion_journal_dir = str(tmp_path)
    record = journal.event_record(event["event_id"], encrypt(encode(event)))
    signed_checkpoint(tmp_path, deletions.settings(), [record])
    # The tombstone exists only externally: this DB simulates a backup predating DELETE acceptance.
    result = deletion_replay.replay(Path(tmp_path))
    assert result["accounts_erased"] == 1
    with postgres.begin() as c:
        new_id = uuid4()
        execute(
            c,
            "INSERT INTO users(id,apple_subject,display_name) VALUES (:id,:subject,'New incarnation')",
            id=new_id,
            subject=old["apple_subject"],
        )
    assert deletion_replay.replay(Path(tmp_path))["accounts_erased"] == 0
    with postgres.connect() as c:
        assert one(c, "SELECT id FROM users WHERE id=:id", id=new_id)
        assert one(c, "SELECT id FROM users WHERE id=:id", id=user) is None
    monkeypatch.setattr(deletions, "revoke_apple_token", lambda _: None)
    assert deletions.cycle()
    assert deletion_replay.replay(Path(tmp_path), check=True)["pending"] == 0


def test_missing_confirmation_or_expired_reauth_never_queues_deletion(postgres, tmp_path):
    client, headers, _, _, _ = setup_budget(postgres)
    deletions.settings().deletion_journal_dir = str(tmp_path)
    user = client.get("/v1/me", headers=headers).json()["id"]
    proof = "test-expired-proof"
    with postgres.begin() as c:
        execute(
            c,
            "INSERT INTO reauth_tokens(token_hash,user_id,expires_at) VALUES (:hash,:user,now()-interval '1 second')",
            hash=digest(proof),
            user=user,
        )
    response = client.request(
        "DELETE",
        "/v1/me",
        headers=headers | {"X-Reauth-Token": proof},
        json={"command_id": str(uuid4()), "confirm_shared_history_changes": True},
    )
    assert response.status_code == 401
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS n FROM deletion_jobs")["n"] == 0
    assert client.get("/v1/me", headers=headers).status_code == 200


def test_retention_clears_only_after_age_and_signed_backup_floor_but_keeps_retry(
    postgres, tmp_path, monkeypatch
):
    import hmac

    client, headers, _, _, _ = setup_budget(postgres)
    _, proof, body, accepted = deletion_request(client, headers, postgres, tmp_path)
    acknowledge(postgres, tmp_path)
    monkeypatch.setattr(deletions, "revoke_apple_token", lambda _: None)
    assert deletions.cycle()
    deletions.maintain_retention()
    completed = tmp_path / "completed" / (accepted["job_id"] + ".json")
    assert completed.exists()
    future = now() + timedelta(days=31)
    monkeypatch.setattr(deletions, "now", lambda: future)
    # Age alone is insufficient while an independently restorable backup still needs this event.
    deletions.maintain_retention()
    with postgres.connect() as c:
        assert one(c, "SELECT restore_tombstone_ciphertext FROM deletion_jobs")[
            "restore_tombstone_ciphertext"
        ]
    checkpoint = {
        "version": 1,
        "sequence": 2,
        "created_at": future.isoformat(),
        "previous_checkpoint": journal.sha((tmp_path / "checkpoint.json").read_bytes()),
        "restore_not_before": future.isoformat(),
        "records": [],
    }
    key = hmac.digest(
        deletions.settings().encryption_key.encode(),
        b"can-deletion-journal-checkpoint-v1",
        "sha256",
    )
    checkpoint["signature"] = hmac.new(key, journal.canonical(checkpoint), "sha256").hexdigest()
    (tmp_path / "checkpoint.json").write_bytes(journal.canonical(checkpoint))
    deletions.maintain_retention()
    with postgres.connect() as c:
        job = one(c, "SELECT * FROM deletion_jobs")
        assert job["restore_tombstone_ciphertext"] is None
        assert job["apple_tokens_ciphertext"] is None and job["user_id"] is None
    assert (
        client.request("DELETE", "/v1/me", headers={"X-Reauth-Token": proof}, json=body).json()
        == accepted
    )


def test_signed_apple_account_deleted_schedules_only_existing_old_incarnation(
    postgres, tmp_path, monkeypatch
):
    from can_api import auth

    client, headers, _, _, _ = setup_budget(postgres)
    deletions.settings().deletion_journal_dir = str(tmp_path)
    user = client.get("/v1/me", headers=headers).json()["id"]
    with postgres.connect() as c:
        account = one(c, "SELECT * FROM users WHERE id=:id", id=user)
    claims = {
        "jti": str(uuid4()),
        "iat": now().timestamp(),
        "events": {
            "type": "account-deleted",
            "sub": account["apple_subject"],
            "event_time": (account["created_at"] - timedelta(days=1)).timestamp(),
        },
    }
    monkeypatch.setattr(auth, "verify_apple", lambda *a, **k: claims)
    assert (
        client.post(
            "/v1/auth/apple/notifications", json={"payload": "verified-test-signature"}
        ).status_code
        == 204
    )
    assert client.get("/v1/me", headers=headers).status_code == 200
    claims["jti"] = str(uuid4())
    claims["events"]["event_time"] = now().timestamp() * 1000
    assert (
        client.post(
            "/v1/auth/apple/notifications", json={"payload": "verified-test-signature"}
        ).status_code
        == 204
    )
    assert client.get("/v1/me", headers=headers).status_code == 401
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS n FROM deletion_jobs")["n"] == 1
    assert (
        client.post(
            "/v1/auth/apple/notifications", json={"payload": "verified-test-signature"}
        ).status_code
        == 204
    )


def test_delayed_consent_revocation_preserves_new_same_user_authorization(postgres, monkeypatch):
    from can_api import auth
    from can_api.security import issue_session

    old_headers, user, _ = seed_user(postgres)
    event_at = now()
    with postgres.begin() as c:
        fresh = issue_session(c, user)
        # Explicitly place sessions on opposite sides of the signed event, no wall-clock sleeps.
        execute(
            c,
            "UPDATE sessions SET created_at=:created WHERE access_token_hash=:hash",
            created=event_at - timedelta(seconds=1),
            hash=digest(old_headers["Authorization"][7:]),
        )
        execute(
            c,
            "UPDATE sessions SET created_at=:created WHERE access_token_hash=:hash",
            created=event_at + timedelta(seconds=1),
            hash=digest(fresh["access_token"]),
        )
        for label, created in (
            ("old", event_at - timedelta(seconds=1)),
            ("new", event_at + timedelta(seconds=1)),
        ):
            execute(
                c,
                "INSERT INTO identity_tokens(id,user_id,token_hash,token_type,ciphertext,created_at) VALUES (:id,:user,:hash,'refresh_token',:cipher,:created)",
                id=uuid4(),
                user=user,
                hash=digest(label),
                cipher=encrypt(label),
                created=created,
            )
    claims = {
        "jti": str(uuid4()),
        "iat": now().timestamp(),
        "events": {"type": "consent-revoked", "sub": str(user), "event_time": event_at.timestamp()},
    }
    monkeypatch.setattr(auth, "verify_apple", lambda *a, **k: claims)
    from fastapi.testclient import TestClient

    from can_api.app import app

    client = TestClient(app)
    assert (
        client.post(
            "/v1/auth/apple/notifications", json={"payload": "verified-notification"}
        ).status_code
        == 204
    )
    assert client.get("/v1/me", headers=old_headers).status_code == 401
    assert (
        client.get(
            "/v1/me", headers={"Authorization": "Bearer " + fresh["access_token"]}
        ).status_code
        == 200
    )
    with postgres.connect() as c:
        tokens = rows(c, "SELECT token_hash FROM identity_tokens WHERE user_id=:id", id=user)
        assert len(tokens) == 1 and bytes(tokens[0]["token_hash"]) == digest("new")
