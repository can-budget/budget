from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from can_api import auth, config, deletions
from can_api.app import app
from can_api.db import execute, one
from can_api.local_accounts import create_account, reset_password
from can_api.security import digest

pytestmark = pytest.mark.postgres
PASSWORD = "local test password with 24 chars"


def provision(engine, login="owner", existing=None):
    with engine.begin() as c:
        return create_account(c, login, PASSWORD, "Owner", existing)


def login(client, name="owner", password=PASSWORD):
    return client.post("/v1/auth/password", json={"login": name, "password": password})


def test_private_password_login_refresh_and_reauthentication_without_apple(postgres):
    account = provision(postgres)
    client = TestClient(app)
    response = login(client, " OWNER ")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["user"]["id"] == account["user_id"]
    headers = {"Authorization": "Bearer " + result["session"]["access_token"]}
    assert client.get("/v1/me", headers=headers).json()["id"] == account["user_id"]
    rotated = client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": result["session"]["refresh_token"],
            "refresh_request_id": str(uuid4()),
        },
    )
    assert rotated.status_code == 200
    with postgres.begin() as c:
        assert (
            one(c, "SELECT apple_subject FROM users WHERE id=:id", id=account["user_id"])[
                "apple_subject"
            ]
            is None
        )
        proof = one(
            c,
            "SELECT user_id FROM reauth_tokens WHERE token_hash=:hash",
            hash=digest(result["reauth_token"]),
        )
        assert str(proof["user_id"]) == account["user_id"]
    # Accounts cannot be registered or overwritten through HTTP.
    assert (
        client.post("/v1/auth/register", json={"login": "other", "password": PASSWORD}).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/auth/password", json={"login": "other", "password": PASSWORD, "display_name": "X"}
        ).status_code
        == 422
    )


def test_invalid_and_unknown_login_do_not_issue_sessions_or_disclose_credentials(postgres):
    provision(postgres)
    client = TestClient(app)
    wrong, unknown = login(client, password="wrong"), login(client, name="missing")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert PASSWORD not in wrong.text
    invalid = client.post(
        "/v1/auth/password", json={"login": "owner", "password": "private-" * 100}
    )
    assert invalid.status_code == 422 and "private-" not in invalid.text
    with postgres.begin() as c:
        assert one(c, "SELECT count(*) AS n FROM sessions")["n"] == 0


def test_failed_password_attempts_are_rate_limited_even_after_transaction_rollback(postgres):
    provision(postgres)
    client = TestClient(app)
    for _ in range(10):
        assert login(client, password="wrong").status_code == 401
    blocked = login(client)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    with postgres.begin() as c:
        execute(c, "UPDATE request_limits SET window_start=now()-interval '6 minutes'")
    assert login(client).status_code == 200


def test_password_reset_revokes_old_sessions_refresh_and_reauthentication(postgres):
    account = provision(postgres)
    client = TestClient(app)
    original = login(client).json()
    with postgres.begin() as c:
        reset_password(c, "owner", PASSWORD + " new")
        assert (
            one(
                c,
                "SELECT count(*) AS n FROM reauth_tokens WHERE user_id=:id",
                id=account["user_id"],
            )["n"]
            == 0
        )
    assert (
        client.get(
            "/v1/me", headers={"Authorization": "Bearer " + original["session"]["access_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/auth/refresh",
            json={
                "refresh_token": original["session"]["refresh_token"],
                "refresh_request_id": str(uuid4()),
            },
        ).status_code
        == 401
    )
    assert login(client).status_code == 401
    assert login(client, password=PASSWORD + " new").json()["user"]["id"] == account["user_id"]


def test_password_reset_between_verification_and_user_lock_cannot_create_session(
    postgres, monkeypatch
):
    provision(postgres)
    verify = auth.verify_password

    def changed(password, stored):
        valid = verify(password, stored)
        with postgres.begin() as c:
            reset_password(c, "owner", PASSWORD + " rotated")
        return valid

    monkeypatch.setattr(auth, "verify_password", changed)
    assert login(TestClient(app)).status_code == 401


def test_credentials_can_attach_to_existing_account_without_replacing_its_data(postgres):
    user = uuid4()
    with postgres.begin() as c:
        execute(
            c,
            "INSERT INTO users(id,apple_subject,display_name) VALUES (:id,'old-subject','Original')",
            id=user,
        )
    provision(postgres, existing=user)
    result = login(TestClient(app)).json()
    assert result["user"]["id"] == str(user) and result["user"]["display_name"] == "Original"


def test_local_account_deletion_needs_own_fresh_proof_and_erases_password(postgres, monkeypatch):
    cfg = config.settings()
    cfg.recovery_mode = "single_server"
    account = provision(postgres)
    provision(postgres, login="member")
    client = TestClient(app)
    owner, member = login(client).json(), login(client, "member").json()
    headers = {
        "Authorization": "Bearer " + owner["session"]["access_token"],
        "X-Reauth-Token": member["reauth_token"],
    }
    body = {"command_id": str(uuid4()), "confirm_shared_history_changes": True}
    assert client.request("DELETE", "/v1/me", headers=headers, json=body).status_code == 401
    headers["X-Reauth-Token"] = owner["reauth_token"]
    assert client.request("DELETE", "/v1/me", headers=headers, json=body).status_code == 202
    assert login(client).status_code == 401

    def no_apple_call(record):
        pytest.fail("A password-only account must not contact Apple for deletion")

    monkeypatch.setattr(deletions, "revoke_apple_token", no_apple_call)
    assert deletions.cycle(maintenance=False)
    with postgres.begin() as c:
        assert (
            one(c, "SELECT user_id FROM local_credentials WHERE user_id=:id", id=account["user_id"])
            is None
        )
        job = one(
            c,
            "SELECT status,apple_revocation_state,last_error_code FROM deletion_jobs WHERE command_id=:id",
            id=body["command_id"],
        )
        assert job == {"status": "done", "apple_revocation_state": "done", "last_error_code": None}
    assert login(client, "member").status_code == 200
