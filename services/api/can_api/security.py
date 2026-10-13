import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Connection

from .config import settings
from .db import execute, one
from .errors import Problem, require


def now() -> datetime:
    return datetime.now(UTC)


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def encrypt(value: str) -> bytes:
    key = settings().encryption_key
    require(key, "encryption_not_configured", 503)
    return Fernet(key.encode()).encrypt(value.encode())


def decrypt(value: bytes) -> str:
    require(settings().encryption_key, "encryption_not_configured", 503)
    try:
        return Fernet(settings().encryption_key.encode()).decrypt(bytes(value)).decode()
    except InvalidToken as exc:
        raise Problem("credential_unavailable", 503) from exc


def invitation_digest(code: str) -> bytes:
    require(settings().invitation_hmac_key, "invitation_key_not_configured", 503)
    return hmac.digest(settings().invitation_hmac_key.encode(), code.encode(), "sha256")


def payload_hash(value: dict) -> bytes:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")))


def rate_limit(c: Connection, key: str, limit: int, seconds: int = 60) -> None:
    row = one(
        c,
        """INSERT INTO request_limits(key,window_start,count) VALUES (:key,now(),1)
        ON CONFLICT(key) DO UPDATE SET
        count=CASE WHEN request_limits.window_start < now()-make_interval(secs=>:seconds)
          THEN 1 ELSE request_limits.count+1 END,
        window_start=CASE WHEN request_limits.window_start < now()-make_interval(secs=>:seconds)
          THEN now() ELSE request_limits.window_start END RETURNING count""",
        key=hashlib.sha256(key.encode()).hexdigest(),
        seconds=seconds,
    )
    require(row and row["count"] <= limit, "rate_limited", 429, retryable=True)


def identity(c: Connection, bearer: str | None, lock: bool = False) -> dict[str, Any]:
    require(bearer and bearer.startswith("Bearer "), "unauthorized", 401)
    access = str(bearer)[7:]
    row = one(
        c,
        """SELECT s.*,u.display_name,u.deletion_requested_at FROM sessions s
        JOIN users u ON u.id=s.user_id WHERE access_token_hash=:hash
        AND s.revoked_at IS NULL AND s.access_expires_at>now() AND s.absolute_expires_at>now()""",
        hash=digest(access),
    )
    require(row and row["deletion_requested_at"] is None, "unauthorized", 401)
    assert row is not None
    if lock:
        one(c, "SELECT id FROM users WHERE id=:id FOR UPDATE", id=row["user_id"])
        # A revoke may have committed while this request waited for the user lock.
        valid = one(c, "SELECT id FROM sessions WHERE id=:id AND revoked_at IS NULL", id=row["id"])
        require(valid, "unauthorized", 401)
    execute(c, "UPDATE sessions SET last_used_at=now() WHERE id=:id", id=row["id"])
    return row


_key_cache: dict[str, Any] = {"expires": 0, "keys": []}


def verify_apple(
    encoded: str, nonce: str | None = None, *, notification: bool = False
) -> dict[str, Any]:
    cfg = settings()
    require(cfg.apple_client_id, "apple_not_configured", 503)
    try:
        header = jwt.get_unverified_header(encoded)
        require(header.get("alg") == "RS256", "invalid_apple_token", 401)
        if _key_cache["expires"] < time.time() or not any(
            key.get("kid") == header.get("kid") for key in _key_cache["keys"]
        ):
            response = httpx.get("https://appleid.apple.com/auth/keys", timeout=10)
            response.raise_for_status()
            _key_cache.update(keys=response.json()["keys"], expires=time.time() + 3600)
        matching = [key for key in _key_cache["keys"] if key.get("kid") == header.get("kid")]
        require(len(matching) == 1, "invalid_apple_token", 401)
        claims = jwt.decode(
            encoded,
            jwt.PyJWK.from_dict(matching[0]).key,
            algorithms=["RS256"],
            audience=cfg.apple_client_id,
            issuer="https://appleid.apple.com",
            options={
                "require": ["iat", "iss", "aud", "jti"]
                if notification
                else ["exp", "iat", "iss", "aud", "sub"]
            },
        )
        if notification:
            require(abs(time.time() - claims["iat"]) <= 86400, "expired_notification", 401)
        if nonce is not None:
            require(hmac.compare_digest(str(claims.get("nonce", "")), nonce), "invalid_nonce", 401)
        return claims
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise Problem("invalid_apple_token", 401) from exc
    except httpx.HTTPError as exc:
        raise Problem("apple_unavailable", 503, retryable=True) from exc


def apple_client_secret() -> str:
    cfg = settings()
    require(
        cfg.apple_team_id and cfg.apple_key_id and cfg.apple_private_key,
        "apple_signing_not_configured",
        503,
    )
    timestamp = int(time.time())
    return jwt.encode(
        {
            "iss": cfg.apple_team_id,
            "iat": timestamp,
            "exp": timestamp + 300,
            "aud": "https://appleid.apple.com",
            "sub": cfg.apple_client_id,
        },
        cfg.apple_private_key,
        algorithm="ES256",
        headers={"kid": cfg.apple_key_id},
    )


def apple_exchange(code: str) -> dict[str, Any]:
    try:
        response = httpx.post(
            "https://appleid.apple.com/auth/token",
            data={
                "client_id": settings().apple_client_id,
                "client_secret": apple_client_secret(),
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        if response.status_code >= 500:
            raise Problem("apple_unavailable", 503, retryable=True)
        require(response.status_code == 200, "apple_code_rejected", 401)
        return response.json()
    except httpx.HTTPError as exc:
        raise Problem("apple_unavailable", 503, retryable=True) from exc


def issue_session(c: Connection, user_id: Any) -> dict[str, Any]:
    sid, family = uuid4(), uuid4()
    access, refresh = token(), token()
    current = now()
    access_exp = current + timedelta(minutes=settings().access_minutes)
    refresh_exp = current + timedelta(days=settings().refresh_days)
    execute(
        c,
        """INSERT INTO sessions(id,user_id,family_id,access_token_hash,access_expires_at,absolute_expires_at)
        VALUES (:id,:user,:family,:hash,:expires,:absolute)""",
        id=sid,
        user=user_id,
        family=family,
        hash=digest(access),
        expires=access_exp,
        absolute=current + timedelta(days=settings().session_days),
    )
    execute(
        c,
        "INSERT INTO refresh_tokens(id,session_id,token_hash,expires_at) VALUES (:id,:session,:hash,:expires)",
        id=uuid4(),
        session=sid,
        hash=digest(refresh),
        expires=refresh_exp,
    )
    return {
        "access_token": access,
        "access_expires_at": access_exp.isoformat(),
        "refresh_token": refresh,
        "refresh_expires_at": refresh_exp.isoformat(),
    }


def revoke_user(c: Connection, user_id: Any) -> None:
    execute(
        c,
        "UPDATE sessions SET revoked_at=now() WHERE user_id=:id AND revoked_at IS NULL",
        id=user_id,
    )
    execute(
        c,
        """UPDATE widget_tokens SET revoked_at=now() WHERE device_id IN
        (SELECT id FROM devices WHERE user_id=:id) AND revoked_at IS NULL""",
        id=user_id,
    )
