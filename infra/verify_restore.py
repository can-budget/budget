"""Read-only recovery checks. Never prints identities, tokens or financial values."""

import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from can_api.budget import project


def main() -> None:
    password = Path(os.environ["CAN_RESTORE_PASSWORD_FILE"]).read_text().rstrip("\r\n")
    with psycopg.connect(
        host="restore-db",
        dbname="can_restore",
        user="postgres",
        password=password,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on",
    ) as conn:
        counts = {}
        for table in (
            "users",
            "cabinets",
            "memberships",
            "sprints",
            "entries",
            "sprint_revisions",
            "command_receipts",
            "deletion_jobs",
        ):
            row = conn.execute(
                psycopg.sql.SQL("SELECT count(*) AS n FROM {}").format(
                    psycopg.sql.Identifier(table)
                )
            ).fetchone()
            assert row is not None
            counts[table] = row["n"]
        invalid = conn.execute("""
            SELECT EXISTS (
                SELECT 1 FROM memberships WHERE left_at IS NULL
                GROUP BY cabinet_id HAVING count(*) > 10
            ) AS invalid
        """).fetchone()
        assert invalid is not None and not invalid["invalid"], (
            "Membership capacity violation"
        )
        projections = 0
        for sprint in conn.execute("SELECT * FROM sprints").fetchall():
            entries = conn.execute(
                """
                SELECT effective_day AS day,
                    coalesce(sum(amount_minor) FILTER (WHERE kind='topup'), 0) AS topups_minor,
                    coalesce(sum(amount_minor) FILTER (WHERE kind='expense'), 0) AS expenses_minor
                FROM entries WHERE sprint_id=%s AND deleted_at IS NULL GROUP BY effective_day
            """,
                (sprint["id"],),
            ).fetchall()
            revisions = conn.execute(
                """
                SELECT effective_day, sequence, bank_adjustment_minor, end_date_after AS end_date
                FROM sprint_revisions WHERE sprint_id=%s ORDER BY sequence
            """,
                (sprint["id"],),
            ).fetchall()
            choices = conn.execute(
                "SELECT day, mode FROM carry_decisions WHERE sprint_id=%s",
                (sprint["id"],),
            ).fetchall()
            # Replaying the final day exercises all accepted history, even for an active sprint.
            data = {
                "initial_minor": sprint["initial_minor"],
                "start_date": str(sprint["start_date"]),
                "end_date": str(sprint["initial_end_date"]),
                "as_of_date": str(sprint["end_date"]),
                "days": [
                    {
                        "day": str(row["day"]),
                        "topups_minor": int(row["topups_minor"]),
                        "expenses_minor": int(row["expenses_minor"]),
                    }
                    for row in entries
                ],
                "revisions": [
                    {
                        **row,
                        "effective_day": str(row["effective_day"]),
                        "end_date": str(row["end_date"]),
                    }
                    for row in revisions
                ],
                "choices": {str(row["day"]): row["mode"] for row in choices},
            }
            result = project(data)
            bank = (
                sprint["initial_minor"]
                + sum(
                    int(row["topups_minor"]) - int(row["expenses_minor"])
                    for row in entries
                )
                + sum(row["bank_adjustment_minor"] for row in revisions)
            )
            assert result["bank_minor"] == bank, "Ledger replay mismatch"
            assert result["today_remaining_minor"] == bank, (
                "Final-day invariant mismatch"
            )
            projections += 1
        tombstones = conn.execute("""
            SELECT count(*) AS n FROM deletion_jobs
            WHERE status <> 'done'
        """).fetchone()
        assert tombstones is not None
        print(
            json.dumps(
                {
                    "row_counts": counts,
                    "verified_projections": projections,
                    "deletion_replay_required": bool(tombstones["n"]),
                }
            )
        )
        if tombstones["n"]:
            raise SystemExit(
                "Recovery remains isolated until verified deletion replay and Apple revocation is completed."
            )


if __name__ == "__main__":
    try:
        main()
    except (
        psycopg.Error,
        OSError,
        ValueError,
        AssertionError,
        KeyError,
        TypeError,
    ) as exc:
        # psycopg exception details can contain private row data; emit only the error type.
        raise SystemExit(
            f"Recovery verification failed: {type(exc).__name__}"
        ) from None
