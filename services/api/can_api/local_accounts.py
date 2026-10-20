"""Owner-operated local account creation/reset. No public registration endpoint."""

import argparse
import getpass
import json
import sys
from uuid import UUID, uuid4

from sqlalchemy import Connection

from .db import engine, execute, one
from .errors import Problem, require
from .passwords import hash_password, normalize_login
from .security import revoke_user


def create_account(
    c: Connection, login: str, password: str, display_name: str, user_id: UUID | None = None
) -> dict:
    login = normalize_login(login)
    display_name = display_name.strip()
    require(1 <= len(display_name) <= 40, "invalid_display_name", 422)
    encoded = hash_password(password)
    require(
        not one(c, "SELECT user_id FROM local_credentials WHERE login=:login", login=login),
        "login_unavailable",
        409,
    )
    if user_id is None:
        user_id = uuid4()
        execute(
            c,
            "INSERT INTO users(id,display_name) VALUES (:id,:name)",
            id=user_id,
            name=display_name,
        )
    else:
        user = one(
            c, "SELECT id,deletion_requested_at FROM users WHERE id=:id FOR UPDATE", id=user_id
        )
        require(user and not user["deletion_requested_at"], "account_unavailable", 409)
        require(
            not one(c, "SELECT user_id FROM local_credentials WHERE user_id=:id", id=user_id),
            "credentials_already_exist",
            409,
        )
    execute(
        c,
        "INSERT INTO local_credentials(user_id,login,password_hash) VALUES (:user,:login,:hash)",
        user=user_id,
        login=login,
        hash=encoded,
    )
    return {"user_id": str(user_id), "login": login}


def reset_password(c: Connection, login: str, password: str) -> dict:
    login, encoded = normalize_login(login), hash_password(password)
    record = one(c, "SELECT user_id FROM local_credentials WHERE login=:login", login=login)
    require(record, "account_unavailable", 404)
    assert record is not None
    user = one(
        c,
        "SELECT id,deletion_requested_at FROM users WHERE id=:id FOR UPDATE",
        id=record["user_id"],
    )
    require(user and not user["deletion_requested_at"], "account_unavailable", 409)
    execute(
        c,
        "UPDATE local_credentials SET password_hash=:hash,updated_at=now() WHERE user_id=:id",
        hash=encoded,
        id=record["user_id"],
    )
    revoke_user(c, record["user_id"])
    execute(c, "DELETE FROM reauth_tokens WHERE user_id=:id", id=record["user_id"])
    return {"user_id": str(record["user_id"]), "login": login}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "reset"])
    parser.add_argument("--login", required=True)
    parser.add_argument("--display-name", default="Участник")
    parser.add_argument(
        "--user-id",
        type=UUID,
        help="Explicitly attach to an existing account, preserving its budget",
    )
    parser.add_argument(
        "--password-stdin", action="store_true", help="Read one password line from private stdin"
    )
    args = parser.parse_args()
    try:
        if args.command == "reset" and args.user_id is not None:
            raise ValueError("--user-id is only supported when creating credentials")
        password = (
            sys.stdin.readline().rstrip("\r\n")
            if args.password_stdin
            else getpass.getpass("Password: ")
        )
        with engine().begin() as c:
            result = (
                create_account(c, args.login, password, args.display_name, args.user_id)
                if args.command == "create"
                else reset_password(c, args.login, password)
            )
        print(json.dumps(result))
        return 0
    except (Problem, ValueError) as error:
        print(error.body["code"] if isinstance(error, Problem) else str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
