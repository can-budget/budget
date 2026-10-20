"""Run inside the existing API container over owner SSH; never expose as an HTTP route."""

import argparse
import json
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4


def validate_subject(value: str) -> str:
    prefix = "can-simulator:"
    if not value.startswith(prefix) or str(UUID(value[len(prefix) :])) != value[len(prefix) :]:
        raise ValueError("Only a synthetic simulator identity is allowed")
    return value


def issue(subject: str, origin: str) -> dict:
    validate_subject(subject)
    endpoint = urlsplit(origin)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("An HTTPS API origin is required")

    from can_api.db import engine, execute, one
    from can_api.ledger import profile
    from can_api.security import digest, issue_session, now, revoke_user

    expiry = now() + timedelta(hours=8)
    with engine().begin() as connection:
        user = one(
            connection,
            """INSERT INTO users(id,apple_subject,display_name) VALUES (:id,:subject,:name)
            ON CONFLICT(apple_subject) DO UPDATE SET apple_subject=excluded.apple_subject
            RETURNING id,deletion_requested_at""",
            id=uuid4(),
            subject=subject,
            name="Тестовый участник",
        )
        if user is None or user["deletion_requested_at"] is not None:
            raise ValueError("The simulator account is pending deletion")
        revoke_user(connection, user["id"])
        session = issue_session(connection, user["id"])
        record = one(
            connection,
            """UPDATE sessions SET absolute_expires_at=:expiry,
            access_expires_at=LEAST(access_expires_at,:expiry)
            WHERE user_id=:user AND access_token_hash=:hash RETURNING id,access_expires_at""",
            expiry=expiry,
            user=user["id"],
            hash=digest(session["access_token"]),
        )
        assert record is not None
        execute(
            connection,
            "UPDATE refresh_tokens SET expires_at=:expiry WHERE session_id=:session",
            expiry=expiry,
            session=record["id"],
        )
        session["access_expires_at"] = record["access_expires_at"].isoformat()
        session["refresh_expires_at"] = expiry.isoformat()
        result = {
            "api_origin": origin,
            "expires_at": expiry.isoformat(),
            "user": profile(connection, user["id"]),
            "session": session,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--api-origin", required=True)
    args = parser.parse_args()
    print(json.dumps(issue(args.subject, args.api_origin), default=str))


if __name__ == "__main__":
    main()
