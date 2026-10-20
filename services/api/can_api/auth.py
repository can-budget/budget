import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import Connection

from . import schemas
from .config import settings
from .db import execute, one, transaction
from .errors import Problem, require
from .ledger import encode, profile
from .passwords import verify_password
from .security import (
    apple_exchange,
    decrypt,
    digest,
    encrypt,
    identity,
    issue_session,
    now,
    revoke_user,
    token,
    verify_apple,
)

router = APIRouter()
DB = Annotated[Connection, Depends(transaction, scope="function")]
Bearer = Annotated[str | None, Header(alias="Authorization")]


@router.post("/v1/auth/password")
def password_login(body: schemas.PasswordLogin, c: DB):
    record = one(
        c,
        "SELECT user_id,password_hash FROM local_credentials WHERE login=:login",
        login=body.login,
    )
    valid = verify_password(
        body.password.get_secret_value(), record["password_hash"] if record else None
    )
    require(record and valid, "invalid_credentials", 401)
    assert record is not None
    user = one(
        c,
        "SELECT id,deletion_requested_at FROM users WHERE id=:id FOR UPDATE",
        id=record["user_id"],
    )
    # Serialize against password reset/deletion, then recheck the exact credential version.
    current = one(
        c, "SELECT password_hash FROM local_credentials WHERE user_id=:id", id=record["user_id"]
    )
    require(
        user
        and not user["deletion_requested_at"]
        and current
        and current["password_hash"] == record["password_hash"],
        "invalid_credentials",
        401,
    )
    proof, expires = token(), now() + timedelta(minutes=5)
    execute(
        c,
        "INSERT INTO reauth_tokens(token_hash,user_id,expires_at) VALUES (:hash,:user,:expires)",
        hash=digest(proof),
        user=record["user_id"],
        expires=expires,
    )
    return {
        "user": profile(c, record["user_id"]),
        "session": issue_session(c, record["user_id"]),
        "reauth_token": proof,
        "reauth_expires_at": expires,
    }


@router.post("/v1/auth/apple/challenge")
def challenge(c: DB):
    raw, challenge_id, expires = token(), uuid4(), now() + timedelta(minutes=5)
    execute(
        c,
        "INSERT INTO auth_challenges(id,nonce_hash,expires_at) VALUES (:id,:hash,:expires)",
        id=challenge_id,
        hash=digest(raw),
        expires=expires,
    )
    return {"challenge_id": challenge_id, "raw_nonce": raw, "expires_at": expires}


@router.post("/v1/auth/apple")
def login(body: schemas.AppleLogin, c: DB):
    challenge_row = one(
        c, "SELECT * FROM auth_challenges WHERE id=:id FOR UPDATE", id=body.challenge_id
    )
    require(
        challenge_row and not challenge_row["used_at"] and challenge_row["expires_at"] > now(),
        "challenge_expired",
        401,
    )
    assert challenge_row is not None
    claims = verify_apple(body.id_token, bytes(challenge_row["nonce_hash"]).hex())
    # Lock an existing incarnation before creating more Apple session credentials.
    existing = one(
        c,
        "SELECT id,deletion_requested_at FROM users WHERE apple_subject=:subject FOR UPDATE",
        subject=claims["sub"],
    )
    require(not existing or not existing["deletion_requested_at"], "account_deletion_pending", 403)
    tokens = apple_exchange(body.authorization_code)
    exchanged = verify_apple(tokens.get("id_token", ""), bytes(challenge_row["nonce_hash"]).hex())
    require(claims["sub"] == exchanged["sub"], "apple_identity_mismatch", 401)
    # ON CONFLICT closes the race between two new devices logging in simultaneously.
    user = one(
        c,
        """INSERT INTO users(id,apple_subject,display_name) VALUES (:id,:subject,:name)
        ON CONFLICT(apple_subject) DO UPDATE SET apple_subject=excluded.apple_subject
        RETURNING *""",
        id=uuid4(),
        subject=claims["sub"],
        name=body.display_name or "Участник",
    )
    assert user is not None
    require(not user["deletion_requested_at"], "account_deletion_pending", 403)
    if tokens.get("refresh_token"):
        execute(
            c,
            """INSERT INTO identity_credentials(user_id,apple_refresh_ciphertext,encryption_key_id)
            VALUES (:user,:cipher,'configured') ON CONFLICT(user_id) DO UPDATE
            SET apple_refresh_ciphertext=excluded.apple_refresh_ciphertext,updated_at=now()""",
            user=user["id"],
            cipher=encrypt(tokens["refresh_token"]),
        )
    else:
        require(
            one(c, "SELECT user_id FROM identity_credentials WHERE user_id=:id", id=user["id"]),
            "apple_refresh_unavailable",
            401,
        )
    # Each code exchange may create a distinct Apple session. Keep every token for revocation.
    for token_type in ("refresh_token", "access_token"):
        if tokens.get(token_type):
            execute(
                c,
                """INSERT INTO identity_tokens(id,user_id,token_hash,token_type,ciphertext)
                VALUES (:id,:user,:hash,:kind,:cipher) ON CONFLICT(user_id,token_hash) DO NOTHING""",
                id=uuid4(),
                user=user["id"],
                hash=digest(tokens[token_type]),
                kind=token_type,
                cipher=encrypt(tokens[token_type]),
            )
    execute(c, "UPDATE auth_challenges SET used_at=now() WHERE id=:id", id=body.challenge_id)
    reauth, expires = token(), now() + timedelta(minutes=5)
    execute(
        c,
        "INSERT INTO reauth_tokens(token_hash,user_id,expires_at) VALUES (:hash,:user,:expires)",
        hash=digest(reauth),
        user=user["id"],
        expires=expires,
    )
    return {
        "user": profile(c, user["id"]),
        "session": issue_session(c, user["id"]),
        "reauth_token": reauth,
        "reauth_expires_at": expires,
    }


@router.post("/v1/auth/refresh")
def refresh(body: schemas.RefreshRequest, c: DB):
    existing = one(
        c,
        """SELECT t.id,s.user_id FROM refresh_tokens t JOIN sessions s ON s.id=t.session_id
        WHERE t.token_hash=:hash""",
        hash=digest(body.refresh_token),
    )
    require(existing, "invalid_refresh", 401)
    assert existing is not None
    one(c, "SELECT id FROM users WHERE id=:id FOR UPDATE", id=existing["user_id"])
    record = one(
        c,
        """SELECT t.*,s.user_id,s.revoked_at,s.absolute_expires_at FROM refresh_tokens t
        JOIN sessions s ON s.id=t.session_id WHERE t.id=:id FOR UPDATE OF t,s""",
        id=existing["id"],
    )
    assert record is not None
    require(
        not record["revoked_at"]
        and record["absolute_expires_at"] > now()
        and record["expires_at"] > now(),
        "refresh_expired",
        401,
    )
    if record["consumed_at"]:
        if str(record["refresh_request_id"]) == str(body.refresh_request_id):
            require(record["response_expires_at"] > now(), "refresh_retry_expired", 401)
            return json.loads(decrypt(record["encrypted_retry_response"]))
        revoke_user(c, record["user_id"])
        # Return instead of raising so the security revocation commits.
        return JSONResponse(Problem("refresh_reuse", 401).body, status_code=401)
    access, new_refresh = token(), token()
    access_exp = now() + timedelta(minutes=settings().access_minutes)
    refresh_exp = min(
        now() + timedelta(days=settings().refresh_days), record["absolute_expires_at"]
    )
    result = {
        "access_token": access,
        "access_expires_at": access_exp.isoformat(),
        "refresh_token": new_refresh,
        "refresh_expires_at": refresh_exp.isoformat(),
    }
    execute(
        c,
        """UPDATE sessions SET access_token_hash=:hash,access_expires_at=:expires,last_used_at=now()
        WHERE id=:id""",
        hash=digest(access),
        expires=access_exp,
        id=record["session_id"],
    )
    execute(
        c,
        "INSERT INTO refresh_tokens(id,session_id,token_hash,expires_at) VALUES (:id,:session,:hash,:expires)",
        id=uuid4(),
        session=record["session_id"],
        hash=digest(new_refresh),
        expires=refresh_exp,
    )
    execute(
        c,
        """UPDATE refresh_tokens SET consumed_at=now(),refresh_request_id=:request,
        encrypted_retry_response=:response,response_expires_at=:expires WHERE id=:id""",
        request=body.refresh_request_id,
        response=encrypt(encode(result)),
        expires=now() + timedelta(minutes=2),
        id=record["id"],
    )
    return result


@router.post("/v1/auth/logout", status_code=204)
def logout(c: DB, authorization: Bearer = None):
    session = identity(c, authorization, lock=True)
    execute(c, "UPDATE sessions SET revoked_at=now() WHERE id=:id", id=session["id"])
    execute(
        c,
        """UPDATE widget_tokens SET revoked_at=now() WHERE device_id IN
        (SELECT id FROM devices WHERE session_id=:id)""",
        id=session["id"],
    )
    return Response(status_code=204)


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signedPayload: str | None = None
    payload: str | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self):
        if self.model_fields_set not in ({"payload"}, {"signedPayload"}) or (
            self.payload is None
        ) == (self.signedPayload is None):
            raise ValueError("exactly one signed Apple notification payload is required")
        return self


@router.post("/v1/auth/apple/notifications", status_code=204)
def notification(body: Notification, c: DB):
    claims = verify_apple(body.payload or body.signedPayload or "", notification=True)
    try:
        event = (
            json.loads(claims["events"]) if isinstance(claims["events"], str) else claims["events"]
        )
    except (KeyError, ValueError) as exc:
        raise Problem("invalid_notification", 422) from exc
    require(
        isinstance(event, dict) and isinstance(event.get("sub"), str), "invalid_notification", 422
    )
    require(
        isinstance(claims["jti"], str) and 0 < len(claims["jti"]) <= 512,
        "invalid_notification",
        422,
    )
    accepted = one(
        c,
        "INSERT INTO apple_notifications(event_id) VALUES (:id) ON CONFLICT DO NOTHING RETURNING event_id",
        id=claims["jti"],
    )
    if not accepted:
        return Response(status_code=204)
    if event.get("type") in ("consent-revoked", "account-delete", "account-deleted"):
        event_timestamp = event.get("event_time")
        require(
            type(event_timestamp) in (int, float) and event_timestamp > 0,
            "invalid_notification",
            422,
        )
        if event_timestamp > 100_000_000_000:
            event_timestamp /= 1000
        require(event_timestamp <= now().timestamp() + 300, "invalid_notification", 422)
        event_at = datetime.fromtimestamp(event_timestamp, UTC)
        user = one(
            c, "SELECT * FROM users WHERE apple_subject=:subject FOR UPDATE", subject=event["sub"]
        )
        # Delayed events for an old Apple authorization cannot delete a newly created account.
        if user and user["created_at"] <= event_at:
            if event["type"] in ("account-delete", "account-deleted"):
                from .deletions import queue_deletion

                queue_deletion(c, user)
            else:
                # Delivery may be delayed past a later authorization of the same account.
                execute(
                    c,
                    "UPDATE sessions SET revoked_at=COALESCE(revoked_at,now()) WHERE user_id=:id AND created_at<=:event",
                    id=user["id"],
                    event=event_at,
                )
                execute(
                    c,
                    """UPDATE widget_tokens SET revoked_at=COALESCE(revoked_at,now()) WHERE device_id IN
                    (SELECT d.id FROM devices d JOIN sessions s ON s.id=d.session_id WHERE d.user_id=:id AND s.revoked_at IS NOT NULL)""",
                    id=user["id"],
                )
                execute(
                    c,
                    "DELETE FROM identity_credentials WHERE user_id=:id AND updated_at<=:event",
                    id=user["id"],
                    event=event_at,
                )
                execute(
                    c,
                    "DELETE FROM identity_tokens WHERE user_id=:id AND created_at<=:event",
                    id=user["id"],
                    event=event_at,
                )
    return Response(status_code=204)


@router.get("/v1/me")
def me(c: DB, authorization: Bearer = None):
    return profile(c, identity(c, authorization)["user_id"])


@router.patch("/v1/me")
def update_profile(body: schemas.ProfilePatch, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)
    execute(
        c,
        "UPDATE users SET display_name=:name WHERE id=:id",
        name=body.display_name,
        id=user["user_id"],
    )
    # Member name changes invalidate the shared snapshot and widget update signal.
    from .ledger import increment, lock_cabinet

    membership = one(
        c,
        "SELECT cabinet_id FROM memberships WHERE user_id=:id AND left_at IS NULL",
        id=user["user_id"],
    )
    if membership:
        lock_cabinet(c, membership["cabinet_id"])
        increment(c, membership["cabinet_id"])
    return profile(c, user["user_id"])


@router.delete("/v1/me", status_code=202)
def delete_account(
    body: schemas.DeleteAccount,
    c: DB,
    authorization: Bearer = None,
    x_reauth_token: Annotated[str | None, Header()] = None,
):
    from .deletions import request_deletion

    return request_deletion(c, body, authorization, x_reauth_token)
