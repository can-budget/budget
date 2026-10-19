"""Account erasure preserves financial facts and removes identity from every retained copy."""

import hmac
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import Connection

from .config import settings
from .db import engine, execute, one, rows
from .deletion_journal import POLICY, checkpoint, publish_and_acknowledged, publish_completed
from .errors import Problem, require
from .ledger import encode, increment, lock_cabinet
from .security import apple_client_secret, decrypt, digest, encrypt, identity, now, revoke_user


def accepted_replay(c: Connection, command_id, proof: str | None, confirmed: bool) -> dict | None:
    job = one(c, "SELECT * FROM deletion_jobs WHERE command_id=:id", id=command_id)
    if not job:
        return None
    require(
        confirmed
        and proof
        and job["reauth_proof_hash"]
        and hmac.compare_digest(bytes(job["reauth_proof_hash"]), digest(proof)),
        "deletion_replay_proof_required",
        401,
    )
    return job["accepted_json"]


def retained_tokens(c: Connection, user_id) -> tuple[list[dict], bool]:
    tokens = rows(
        c, "SELECT token_type,ciphertext FROM identity_tokens WHERE user_id=:id", id=user_id
    )
    # A legacy credential may predate the token history migration.
    legacy = one(
        c, "SELECT apple_refresh_ciphertext FROM identity_credentials WHERE user_id=:id", id=user_id
    )
    if legacy:
        tokens.append(
            {"token_type": "refresh_token", "ciphertext": legacy["apple_refresh_ciphertext"]}
        )
    result, seen = [], set()
    unavailable = False
    for record in tokens:
        try:
            secret = decrypt(record["ciphertext"])
        except Problem as exc:
            if exc.body["code"] != "credential_unavailable":
                raise
            unavailable = True
            continue
        key = (record["token_type"], secret)
        if key not in seen:
            result.append({"token_type": key[0], "token": secret})
            seen.add(key)
    return result, unavailable


def detach(c: Connection, user_id) -> None:
    """User lock is held; lock cabinets in UUID order, preserving all money and dates."""
    cabinets = rows(
        c,
        "SELECT DISTINCT cabinet_id FROM memberships WHERE user_id=:id ORDER BY cabinet_id",
        id=user_id,
    )
    for membership in cabinets:
        cabinet_id = membership["cabinet_id"]
        lock_cabinet(c, cabinet_id)
        execute(
            c,
            "UPDATE memberships SET left_at=COALESCE(left_at,now()) WHERE user_id=:user AND cabinet_id=:cabinet",
            user=user_id,
            cabinet=cabinet_id,
        )
        execute(
            c,
            "UPDATE invitations SET revoked_at=COALESCE(revoked_at,now()),code_ciphertext=NULL WHERE author_id=:user AND cabinet_id=:cabinet",
            user=user_id,
            cabinet=cabinet_id,
        )
        active = one(
            c,
            "SELECT id FROM memberships WHERE cabinet_id=:id AND left_at IS NULL LIMIT 1",
            id=cabinet_id,
        )
        if not active:
            execute(
                c,
                "UPDATE cabinets SET archived_at=COALESCE(archived_at,now()) WHERE id=:id",
                id=cabinet_id,
            )
            execute(
                c,
                "UPDATE invitations SET revoked_at=COALESCE(revoked_at,now()),code_ciphertext=NULL WHERE cabinet_id=:id",
                id=cabinet_id,
            )
        increment(c, cabinet_id)
    revoke_user(c, user_id)


def request_deletion(c: Connection, body, authorization: str | None, proof: str | None) -> dict:
    replay = accepted_replay(c, body.command_id, proof, body.confirm_shared_history_changes)
    if replay is not None:
        return replay
    require(body.confirm_shared_history_changes and proof, "deletion_confirmation_required", 422)
    require(
        settings().encryption_key
        and (settings().recovery_mode == "single_server" or settings().deletion_journal_dir),
        "deletion_journal_not_configured",
        503,
        retryable=True,
    )
    try:
        session = identity(c, authorization, lock=True)
    except Problem:
        replay = accepted_replay(c, body.command_id, proof, True)
        if replay is not None:
            return replay
        raise
    # A concurrent identical request can have committed while identity waited for its lock.
    replay = accepted_replay(c, body.command_id, proof, True)
    if replay is not None:
        return replay
    user = one(c, "SELECT * FROM users WHERE id=:id", id=session["user_id"])
    assert user is not None
    require(not user["deletion_requested_at"], "account_deletion_pending", 409)
    reauth = one(
        c,
        "SELECT * FROM reauth_tokens WHERE token_hash=:hash AND user_id=:user AND used_at IS NULL AND expires_at>now() FOR UPDATE",
        hash=digest(proof or ""),
        user=user["id"],
    )
    require(reauth, "reauth_required", 401)
    execute(
        c, "UPDATE reauth_tokens SET used_at=now() WHERE token_hash=:hash", hash=digest(proof or "")
    )
    return queue_deletion(c, user, body.command_id, digest(proof or ""))


def queue_deletion(c: Connection, user: dict, command_id=None, proof_hash=None) -> dict:
    """Trusted entry points are explicit reauthentication or a verified Apple deletion event."""
    require(
        settings().encryption_key
        and (settings().recovery_mode == "single_server" or settings().deletion_journal_dir),
        "deletion_journal_not_configured",
        503,
        retryable=True,
    )
    existing = one(c, "SELECT accepted_json FROM deletion_jobs WHERE user_id=:id", id=user["id"])
    if existing:
        return existing["accepted_json"] or {}
    job_id, requested = uuid4(), now()
    tombstone = {
        "version": 1,
        "event_id": str(job_id),
        "user_id": str(user["id"]),
        "user_created_at": user["created_at"].isoformat(),
        "requested_at": requested.isoformat(),
        "policy": POLICY,
    }
    response = {
        "job_id": str(job_id),
        "status": "accepted",
        "primary_deletion_deadline": (requested + timedelta(hours=24)).isoformat(),
        "backup_retention_days": 30,
    }
    tokens, unavailable = retained_tokens(c, user["id"])
    execute(
        c,
        """INSERT INTO deletion_jobs(id,user_id,restore_tombstone_ciphertext,encryption_key_id,status,
        requested_at,purge_tombstone_after,command_id,reauth_proof_hash,accepted_json,apple_tokens_ciphertext,apple_tokens_unavailable)
        VALUES (:id,:user,:tombstone,'configured','pending',:requested,:purge,:command,:proof,CAST(:accepted AS jsonb),:tokens,:unavailable)""",
        id=job_id,
        user=user["id"],
        tombstone=encrypt(encode(tombstone)),
        requested=requested,
        purge=requested + timedelta(days=31),
        command=command_id,
        proof=proof_hash,
        accepted=encode(response),
        tokens=encrypt(encode(tokens)),
        unavailable=unavailable,
    )
    execute(
        c,
        "UPDATE users SET deletion_requested_at=:requested WHERE id=:id",
        requested=requested,
        id=user["id"],
    )
    detach(c, user["id"])
    return response


def scrub_json(value, user_id: str, identifiers: set[str]):
    """Null only identity links; financial IDs, amounts, dates and other people's names survive."""
    if isinstance(value, list):
        return [
            scrub_json(item, user_id, identifiers)
            for item in value
            if not (
                isinstance(item, dict) and item.get("user_id") == user_id and "display_name" in item
            )
        ]
    if isinstance(value, dict):
        hidden_names = set()
        for id_key, name_key in (
            ("author_id", "author_name"),
            ("last_editor_id", "last_editor_name"),
            ("actor_id", "actor_name"),
            ("creator_id", "creator_name"),
        ):
            if value.get(id_key) == user_id:
                hidden_names.add(name_key)
        if value.get("user_id") == user_id or value.get("id") == user_id:
            hidden_names.update(("display_name", "name", "apple_subject", "email"))
        return {
            key: None if key in hidden_names else scrub_json(item, user_id, identifiers)
            for key, item in value.items()
            if key not in identifiers
        }
    if isinstance(value, str) and value in identifiers:
        return None
    return value


def erase_account(c: Connection, tombstone: dict) -> bool:
    """Caller has verified the independently durable tombstone; exact incarnation only."""
    user = one(c, "SELECT * FROM users WHERE id=:id FOR UPDATE", id=tombstone["user_id"])
    if not user:
        return False
    require(
        user["created_at"] == datetime.fromisoformat(tombstone["user_created_at"]),
        "deletion_incarnation_mismatch",
        409,
    )
    user_id = str(user["id"])
    membership_ids = {
        str(row["id"])
        for row in rows(c, "SELECT id FROM memberships WHERE user_id=:id", id=user_id)
    }
    command_ids = {
        str(row["command_id"])
        for row in rows(c, "SELECT command_id FROM command_receipts WHERE user_id=:id", id=user_id)
    }
    identifiers = {user_id, user["apple_subject"]} | membership_ids | command_ids
    # All affected cabinets are locked before touching shared audit/results.
    cabinet_ids = rows(
        c,
        """SELECT id FROM cabinets WHERE id IN (
        SELECT cabinet_id FROM memberships WHERE user_id=:id UNION
        SELECT cabinet_id FROM entries WHERE author_id=:id OR last_editor_id=:id UNION
        SELECT cabinet_id FROM sprint_revisions WHERE actor_id=:id) ORDER BY id""",
        id=user_id,
    )
    for cabinet in cabinet_ids:
        lock_cabinet(c, cabinet["id"])
    # Metadata JSON can contain stale before/after identity even when FK links were changed.
    for table, columns, key in (
        ("entry_revisions", ("before_state", "after_state"), "id"),
        ("command_receipts", ("request_json", "result_json", "error_json"), "command_id"),
    ):
        records = rows(c, f"SELECT * FROM {table}")
        for record in records:
            if table == "command_receipts" and str(record["user_id"]) == user_id:
                continue
            changed = {}
            for column in columns:
                cleaned = scrub_json(record[column], user_id, identifiers)
                if cleaned != record[column]:
                    changed[column] = encode(cleaned)
            if changed:
                setters = ",".join(f"{column}=CAST(:{column} AS jsonb)" for column in changed)
                suffix = " AND user_id=:owner" if table == "command_receipts" else ""
                execute(
                    c,
                    f"UPDATE {table} SET {setters} WHERE {key}=:id" + suffix,
                    id=record[key],
                    owner=record.get("user_id"),
                    **changed,
                )
    # Command UUIDs are unlinkable financial event IDs once the owner's receipts are removed.
    # Recovery/membership links specifically identify an old private outbox and are erased.
    execute(c, "UPDATE entries SET recovery_of_command_id=NULL WHERE author_id=:id", id=user_id)
    execute(c, "DELETE FROM users WHERE id=:id", id=user_id)
    for cabinet in cabinet_ids:
        increment(c, cabinet["id"])
    return True


def revoke_apple_token(record: dict) -> None:
    try:
        response = httpx.post(
            "https://appleid.apple.com/auth/revoke",
            data={
                "client_id": settings().apple_client_id,
                "client_secret": apple_client_secret(),
                "token": record["token"],
                "token_type_hint": record["token_type"],
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise Problem("apple_revoke_unavailable", 503, retryable=True) from exc
    # Apple returns an empty 200 both for a successful revoke and an already invalid token.
    require(
        response.status_code == 200,
        "apple_revoke_rejected" if response.status_code < 500 else "apple_revoke_unavailable",
        503,
        retryable=True,
    )


def maintain_retention() -> None:
    """Expire completed identity ciphertext under the selected recovery policy."""
    if settings().recovery_mode == "single_server":
        # This owner-selected mode has no backups or external recovery journal.
        with engine().begin() as c:
            execute(
                c,
                """UPDATE deletion_jobs SET restore_tombstone_ciphertext=NULL,
                encryption_key_id=NULL,apple_tokens_ciphertext=NULL
                WHERE status='done' AND purge_tombstone_after<=now()""",
            )
        return
    if not settings().deletion_journal_dir:
        return
    path = Path(settings().deletion_journal_dir) / "checkpoint.json"
    manifest = checkpoint(path)[0] if path.exists() else None
    retained = {item["event_id"] for item in manifest["records"]} if manifest else set()
    floor = datetime.fromisoformat(manifest["restore_not_before"]) if manifest else None
    with engine().begin() as c:
        jobs = rows(
            c,
            "SELECT * FROM deletion_jobs WHERE status='done' AND restore_tombstone_ciphertext IS NOT NULL FOR UPDATE SKIP LOCKED",
        )
        for job in jobs:
            if (
                floor is not None
                and str(job["id"]) not in retained
                and job["data_deleted_at"]
                and floor >= job["data_deleted_at"]
                and job["purge_tombstone_after"] <= now()
            ):
                execute(
                    c,
                    "UPDATE deletion_jobs SET restore_tombstone_ciphertext=NULL,encryption_key_id=NULL,apple_tokens_ciphertext=NULL WHERE id=:id",
                    id=job["id"],
                )
            else:
                publish_completed(job)


def cycle(*, maintenance: bool = True) -> bool:
    if maintenance:
        maintain_retention()
    lease = uuid4()
    with engine().begin() as c:
        job = one(
            c,
            """SELECT * FROM deletion_jobs WHERE (status='pending' OR (status='processing' AND lease_expires_at<now()))
            AND next_attempt_at<=now() ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1""",
        )
        if not job:
            return False
        execute(
            c,
            "UPDATE deletion_jobs SET status='processing',lease_token=:lease,lease_expires_at=now()+interval '5 minutes',attempts=attempts+1 WHERE id=:id",
            lease=lease,
            id=job["id"],
        )
    error = None
    try:
        off_host = settings().recovery_mode == "off_host"
        if off_host:
            require(
                publish_and_acknowledged(job), "deletion_journal_ack_pending", 503, retryable=True
            )
        tombstone = json.loads(decrypt(job["restore_tombstone_ciphertext"]))
        with engine().begin() as c:
            current = one(
                c,
                "SELECT * FROM deletion_jobs WHERE id=:id AND lease_token=:lease FOR UPDATE",
                id=job["id"],
                lease=lease,
            )
            if not current:
                return True
            if not current["data_deleted_at"]:
                erase_account(c, tombstone)
                execute(
                    c,
                    "UPDATE deletion_jobs SET data_deleted_at=now(),journal_acknowledged_at=:ack,user_id=NULL,purge_tombstone_after=now()+interval '30 days' WHERE id=:id",
                    id=job["id"],
                    ack=now() if off_host else None,
                )
        tokens = (
            json.loads(decrypt(job["apple_tokens_ciphertext"]))
            if job["apple_tokens_ciphertext"]
            else []
        )
        unavailable = job["apple_tokens_unavailable"] or (
            not tokens and job["apple_revocation_state"] == "pending"
        )
        # Persist progress after every token, so restarts retry only unconfirmed revocations.
        processed = 0
        while tokens and processed < 4:
            revoke_apple_token(tokens[0])
            tokens.pop(0)
            processed += 1
            with engine().begin() as c:
                updated = one(
                    c,
                    """UPDATE deletion_jobs SET apple_tokens_ciphertext=:tokens,
                    apple_revocation_state=:state,lease_expires_at=now()+interval '5 minutes'
                    WHERE id=:id AND lease_token=:lease RETURNING id""",
                    tokens=encrypt(encode(tokens)) if tokens else None,
                    state="pending" if tokens else "done",
                    id=job["id"],
                    lease=lease,
                )
                if not updated:
                    return True
                execute(
                    c,
                    """INSERT INTO worker_health(id,last_seen_at) VALUES (1,now())
                    ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                )
            from .worker import heartbeat

            heartbeat()
        if tokens:
            with engine().begin() as c:
                execute(
                    c,
                    "UPDATE deletion_jobs SET status='pending',next_attempt_at=now(),lease_expires_at=NULL WHERE id=:id AND lease_token=:lease",
                    id=job["id"],
                    lease=lease,
                )
            return True
        with engine().begin() as c:
            execute(
                c,
                """UPDATE deletion_jobs SET status='done',completed_at=now(),last_error_code=:error,
                apple_tokens_ciphertext=NULL,apple_revocation_state=:state,lease_expires_at=NULL
                WHERE id=:id AND lease_token=:lease""",
                error="apple_token_unavailable_manual_revocation" if unavailable else None,
                state="not_available" if unavailable else "done",
                id=job["id"],
                lease=lease,
            )
        return True
    except Problem as exc:
        error = str(exc.body["code"])
    except (OSError, ValueError, TypeError, KeyError):
        error = "deletion_journal_unavailable"
    with engine().begin() as c:
        execute(
            c,
            """UPDATE deletion_jobs SET status='pending',next_attempt_at=:next,
            lease_expires_at=NULL,last_error_code=:error WHERE id=:id AND lease_token=:lease""",
            next=now() + timedelta(seconds=min(3600, 2 ** min(job["attempts"] + 1, 11))),
            error=error,
            id=job["id"],
            lease=lease,
        )
    return True
