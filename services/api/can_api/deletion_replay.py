"""Replay the independently verified deletion journal into an offline restored database.

Never point this CLI at a live service. Restore tooling must disable API/worker ingress first.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .db import engine, execute, one, rows
from .deletion_journal import canonical, event_record, verified_inventory, write_immutable
from .deletions import detach, erase_account, retained_tokens
from .errors import require
from .ledger import encode
from .security import encrypt


def replay(root: Path, *, check: bool = False) -> dict:
    # Validate EVERY encrypted event and the complete signed inventory before any mutation.
    inventory = verified_inventory(root)
    erased = 0
    pending = 0
    with engine().begin() as c:
        for record, tombstone in inventory:
            user = one(c, "SELECT * FROM users WHERE id=:id FOR UPDATE", id=tombstone["user_id"])
            if user:
                require(
                    user["created_at"] == datetime.fromisoformat(tombstone["user_created_at"]),
                    "deletion_incarnation_mismatch",
                    409,
                )
                if check:
                    pending += 1
                    continue
                job = one(c, "SELECT * FROM deletion_jobs WHERE id=:id", id=tombstone["event_id"])
                tokens, unavailable = retained_tokens(c, user["id"])
                requested = datetime.fromisoformat(tombstone["requested_at"])
                if not job:
                    execute(
                        c,
                        """INSERT INTO deletion_jobs(id,user_id,restore_tombstone_ciphertext,
                        encryption_key_id,status,requested_at,purge_tombstone_after,apple_tokens_ciphertext,
                        journal_acknowledged_at,apple_tokens_unavailable) VALUES (:id,:user,:tombstone,'configured','pending',:requested,:purge,:tokens,now(),:unavailable)""",
                        id=tombstone["event_id"],
                        user=user["id"],
                        tombstone=record["ciphertext"].encode(),
                        requested=requested,
                        purge=requested + timedelta(days=31),
                        tokens=encrypt(encode(tokens)),
                        unavailable=unavailable,
                    )
                else:
                    # Do not lose unrevoked credentials from a restored partially completed request.
                    from .security import decrypt

                    previous = (
                        json.loads(decrypt(job["apple_tokens_ciphertext"]))
                        if job["apple_tokens_ciphertext"]
                        else []
                    )
                    unique = {
                        (item["token_type"], item["token"]): item for item in previous + tokens
                    }
                    execute(
                        c,
                        """UPDATE deletion_jobs SET status='pending',apple_tokens_ciphertext=:tokens,
                        apple_revocation_state='pending',apple_tokens_unavailable=apple_tokens_unavailable OR :unavailable,lease_expires_at=NULL,lease_token=NULL,
                        journal_acknowledged_at=now(),next_attempt_at=now() WHERE id=:id""",
                        unavailable=unavailable,
                        tokens=encrypt(encode(list(unique.values()))),
                        id=job["id"],
                    )
                detach(c, user["id"])
                erased += int(erase_account(c, tombstone))
                execute(
                    c,
                    "UPDATE deletion_jobs SET data_deleted_at=now(),user_id=NULL,purge_tombstone_after=now()+interval '30 days' WHERE id=:id",
                    id=tombstone["event_id"],
                )
        unfinished = one(c, "SELECT count(*) AS total FROM deletion_jobs WHERE status <> 'done'")
        pending += unfinished["total"] if unfinished else 0
        if not check:
            # Tokens in backups must never regain authority, including surviving members' tokens.
            execute(c, "UPDATE sessions SET revoked_at=COALESCE(revoked_at,now())")
            execute(c, "UPDATE widget_tokens SET revoked_at=COALESCE(revoked_at,now())")
    return {"events": len(inventory), "accounts_erased": erased, "pending": pending}


def export_pending(root: Path) -> dict:
    """Export accepted jobs recovered from the backup but not yet replicated externally."""
    with engine().connect() as c:
        pending = rows(
            c, "SELECT id,restore_tombstone_ciphertext FROM deletion_jobs WHERE status <> 'done'"
        )
    verified_inventory(root, permitted_unlisted={str(job["id"]) + ".json" for job in pending})
    for job in pending:
        require(job["restore_tombstone_ciphertext"], "deletion_tombstone_missing", 503)
        record = event_record(job["id"], job["restore_tombstone_ciphertext"])
        write_immutable(root / "records" / (str(job["id"]) + ".json"), canonical(record))
    return {"exported": len(pending)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-dir", required=True, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read only; fail if targeted accounts or pending revocation jobs remain",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Explicitly contact Apple; process one bounded revocation batch in an isolated restore",
    )
    parser.add_argument(
        "--export-pending",
        action="store_true",
        help="Export recovered pending events to a writable isolated journal; replicate off-host before --finalize",
    )
    args = parser.parse_args()
    if sum((args.check, args.finalize, args.export_pending)) > 1:
        parser.error("--check, --finalize and --export-pending are mutually exclusive")
    try:
        if args.export_pending:
            result = export_pending(args.journal_dir)
        elif args.finalize:
            from . import deletions

            verified_inventory(args.journal_dir)
            deletions.settings().deletion_journal_dir = str(args.journal_dir)
            deletions.cycle(maintenance=False)
            result = replay(args.journal_dir, check=True)
        else:
            result = replay(args.journal_dir, check=args.check)
    except Exception:
        # Credential, ciphertext and DB parameters must never enter restore logs.
        parser.exit(
            2,
            "Deletion replay failed; inventory/key/schema must be verified before exposing this database.\n",
        )
    print(json.dumps(result))
    if (args.check or args.finalize) and result["pending"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
