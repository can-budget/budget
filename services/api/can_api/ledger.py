import base64
import json
import secrets
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection

from .budget import MAX_EXPOSURE, project
from .config import settings
from .db import execute, one, rows
from .errors import Problem, require
from .schemas import Command
from .security import decrypt, encrypt, invitation_digest, now, payload_hash

ZONE = ZoneInfo("Asia/Shanghai")


def business_date() -> date:
    return now().astimezone(ZONE).date()


def encode(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def member(c: Connection, cabinet_id: Any, user_id: Any) -> dict[str, Any]:
    membership = one(
        c,
        """SELECT m.* FROM memberships m JOIN cabinets b ON b.id=m.cabinet_id
        WHERE m.cabinet_id=:cabinet AND m.user_id=:user AND m.left_at IS NULL
        AND b.archived_at IS NULL""",
        cabinet=cabinet_id,
        user=user_id,
    )
    require(membership, "membership_required", 403)
    assert membership is not None
    return membership


def lock_cabinet(c: Connection, cabinet_id: Any) -> dict[str, Any]:
    cabinet = one(c, "SELECT * FROM cabinets WHERE id=:id FOR UPDATE", id=cabinet_id)
    require(cabinet, "cabinet_not_found", 404)
    assert cabinet is not None
    return cabinet


def increment(c: Connection, cabinet_id: Any) -> int:
    result = one(
        c, "UPDATE cabinets SET revision=revision+1 WHERE id=:id RETURNING revision", id=cabinet_id
    )
    assert result is not None
    revision = result["revision"]
    execute(
        c,
        """INSERT INTO push_outbox(id,cabinet_id,cabinet_revision)
        VALUES (:id,:cabinet,:revision)""",
        id=uuid4(),
        cabinet=cabinet_id,
        revision=revision,
    )
    return revision


def profile(c: Connection, user_id: Any) -> dict[str, Any]:
    user = one(
        c,
        """SELECT u.id,u.display_name,m.cabinet_id AS active_cabinet_id,
        m.id AS active_membership_id FROM users u LEFT JOIN memberships m ON m.user_id=u.id
        AND m.left_at IS NULL WHERE u.id=:id""",
        id=user_id,
    )
    require(user, "user_not_found", 404)
    assert user is not None
    return user


def sprint_json(sprint: dict, day: date | None = None) -> dict:
    return {
        key: sprint[key]
        for key in (
            "id",
            "initial_minor",
            "start_date",
            "end_date",
            "initial_end_date",
            "calculation_version",
        )
    } | {
        "status": "completed" if (day or business_date()) > sprint["end_date"] else "active",
        "topups_today_from": sprint.get("topups_today_from"),
    }


def get_sprint(c: Connection, cabinet_id: Any, sprint_id: Any) -> dict:
    sprint = one(
        c,
        "SELECT * FROM sprints WHERE id=:id AND cabinet_id=:cabinet",
        id=sprint_id,
        cabinet=cabinet_id,
    )
    require(sprint, "sprint_not_found", 404)
    assert sprint is not None
    return sprint


def projection_data(c: Connection, sprint: dict, day: date | None = None) -> dict:
    day = day or business_date()
    totals = rows(
        c,
        """SELECT effective_day AS day,
        COALESCE(sum(amount_minor) FILTER(WHERE kind='topup'),0)::bigint AS topups_minor,
        COALESCE(sum(amount_minor) FILTER(WHERE kind='expense'),0)::bigint AS expenses_minor
        FROM entries WHERE sprint_id=:id AND deleted_at IS NULL GROUP BY effective_day ORDER BY effective_day""",
        id=sprint["id"],
    )
    revisions = rows(
        c,
        """SELECT id,sprint_id,sequence,effective_day,bank_adjustment_minor,
        bank_before_minor,bank_after_minor,end_date_before,end_date_after,basis_revision,actor_id,
        (SELECT display_name FROM users WHERE id=sprint_revisions.actor_id) AS actor_name,created_at
        FROM sprint_revisions WHERE sprint_id=:id ORDER BY sequence""",
        id=sprint["id"],
    )
    carries = rows(
        c,
        """SELECT day,mode,actor_id,basis_revision,decided_at FROM carry_decisions
        WHERE sprint_id=:id ORDER BY day""",
        id=sprint["id"],
    )
    data = {
        "initial_minor": sprint["initial_minor"],
        "start_date": str(sprint["start_date"]),
        "end_date": str(sprint["initial_end_date"]),
        "as_of_date": str(day),
        "days": [{**row, "day": str(row["day"])} for row in totals],
        "revisions": [
            {
                "sequence": row["sequence"],
                "effective_day": str(row["effective_day"]),
                "bank_adjustment_minor": row["bank_adjustment_minor"],
                "end_date": str(row["end_date_after"]),
            }
            for row in revisions
        ],
        "choices": {str(row["day"]): row["mode"] for row in carries},
    }
    if sprint["calculation_version"] == 3:
        require(sprint.get("topups_today_from") is not None, "invalid_ledger", 422)
        data["topups_today_from"] = str(sprint["topups_today_from"])
    else:
        require(sprint["calculation_version"] == 2, "invalid_ledger", 422)
    try:
        projection = project(data)
    except ValueError as exc:
        raise Problem("invalid_ledger", 422) from exc
    return {
        "daily_totals": totals,
        "sprint_revisions": revisions,
        "carry_decisions": carries,
        "projection": projection,
    }


def snapshot(c: Connection, cabinet_id: Any, user_id: Any) -> dict:
    # All callers hold the cabinet lock. The date is captured once for the whole snapshot.
    member(c, cabinet_id, user_id)
    day = business_date()
    cabinet = one(c, "SELECT id,name,timezone,revision FROM cabinets WHERE id=:id", id=cabinet_id)
    assert cabinet is not None
    cabinet["members"] = rows(
        c,
        """SELECT m.id AS membership_id,m.user_id,u.display_name,m.joined_at
        FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.cabinet_id=:id AND m.left_at IS NULL
        ORDER BY m.joined_at,m.id""",
        id=cabinet_id,
    )
    sprint = one(
        c,
        "SELECT * FROM sprints WHERE cabinet_id=:id ORDER BY start_date DESC,created_at DESC,id DESC LIMIT 1",
        id=cabinet_id,
    )
    payload = (
        projection_data(c, sprint, day)
        if sprint
        else {"daily_totals": [], "sprint_revisions": [], "carry_decisions": [], "projection": None}
    )
    return {
        "server_time": now(),
        "business_date": day,
        "cabinet_revision": cabinet["revision"],
        "cabinet": cabinet,
        "sprint": sprint_json(sprint, day) if sprint else None,
        **payload,
    }


def exposure(c: Connection, sprint: dict) -> int:
    record = one(
        c,
        """SELECT
        (SELECT COALESCE(sum(amount_minor),0) FROM entries WHERE sprint_id=:id AND deleted_at IS NULL)
        +(SELECT COALESCE(sum(abs(bank_adjustment_minor)),0) FROM sprint_revisions WHERE sprint_id=:id)
        AS amount""",
        id=sprint["id"],
    )
    assert record is not None
    return sprint["initial_minor"] + int(record["amount"])


def invitation_json(invitation: dict) -> dict | None:
    if (
        invitation["used_at"]
        or invitation["revoked_at"]
        or invitation["expires_at"] <= now()
        or not invitation["code_ciphertext"]
    ):
        return None
    return {
        "id": invitation["id"],
        "code": decrypt(invitation["code_ciphertext"]),
        "expires_at": invitation["expires_at"],
        "environment": settings().environment,
    }


def receipt(c: Connection, user_id: Any, command_id: Any) -> dict | None:
    return one(
        c,
        "SELECT * FROM command_receipts WHERE user_id=:user AND command_id=:id",
        user=user_id,
        id=command_id,
    )


def store_receipt(
    c: Connection,
    user_id: Any,
    command_id: Any,
    cabinet_id: Any,
    request: dict,
    result: dict | None,
    error: Problem | None = None,
) -> None:
    execute(
        c,
        """INSERT INTO command_receipts(user_id,command_id,cabinet_id,payload_hash,
        status,result_revision,result_json,error_json,command_type,request_json,error_status)
        VALUES (:user,:id,:cabinet,:hash,:status,:revision,CAST(:result AS jsonb),CAST(:error AS jsonb),
        :type,CAST(:request AS jsonb),:error_status)""",
        user=user_id,
        id=command_id,
        cabinet=cabinet_id,
        hash=payload_hash(request),
        status="rejected" if error else "applied",
        revision=result.get("result_revision") if result else None,
        result=encode(result) if result else None,
        error=encode(error.body) if error else None,
        type=request.get("type"),
        request=encode(request),
        error_status=error.status if error else None,
    )


def replay(
    c: Connection, old: dict, request: dict, cabinet_id: Any, user_id: Any
) -> tuple[dict, int]:
    require(
        old["payload_hash"] == payload_hash(request) and str(old["cabinet_id"]) == str(cabinet_id),
        "command_payload_conflict",
    )
    active = one(
        c,
        "SELECT id FROM memberships WHERE user_id=:user AND cabinet_id=:cabinet AND left_at IS NULL",
        user=user_id,
        cabinet=cabinet_id,
    )
    if old["status"] == "rejected":
        error = dict(old["error_json"])
        if not active:
            error["details"] = {}
        return error, old.get("error_status") or 409
    result = dict(old["result_json"])
    if "invitation" in result:
        invitation = (
            one(c, "SELECT * FROM invitations WHERE id=:id", id=result.get("entity_id"))
            if result.get("entity_id") and old["command_type"] == "create_invitation"
            else None
        )
        result["invitation"] = invitation_json(invitation) if active and invitation else None
    return result, 200


def apply_command(
    c: Connection, user_id: Any, cabinet_id: Any, command: Command
) -> tuple[dict, int]:
    cabinet = lock_cabinet(c, cabinet_id)
    request = command.model_dump(mode="json") | {"cabinet_id": str(cabinet_id)}
    old = receipt(c, user_id, command.command_id)
    if old:
        return replay(c, old, request, cabinet_id, user_id)
    membership = member(c, cabinet_id, user_id)
    result = None
    error = None
    try:
        with c.begin_nested():
            result = mutate(c, user_id, cabinet, membership, command)
    except Problem as exc:
        if exc.status >= 500 or exc.status in (401, 403, 429):
            raise
        error = exc
    # Invitation ciphertext is retained separately; the durable receipt never stores the code.
    stored_result = {**result, "invitation": None} if result else None
    store_receipt(c, user_id, command.command_id, cabinet_id, request, stored_result, error)
    return (error.body, error.status) if error else (result or {}, 200)


def mutate(c: Connection, user_id: Any, cabinet: dict, membership: dict, command: Command) -> dict:
    p, kind = command.payload, command.type
    today, timestamp = business_date(), now()
    cabinet_id = cabinet["id"]
    basis = p.get("expected_cabinet_revision")
    if basis is not None:
        require(
            basis == cabinet["revision"],
            "stale_revision",
            details={"cabinet_revision": cabinet["revision"]},
        )
    result = {
        "command_id": str(command.command_id),
        "status": "applied",
        "result_revision": cabinet["revision"],
        "entity_id": None,
        "invitation": None,
    }
    sprint = (
        get_sprint(c, cabinet_id, p["sprint_id"])
        if "sprint_id" in p and kind != "create_sprint"
        else None
    )
    if kind == "create_sprint":
        end = date.fromisoformat(p["end_date"])
        require(today <= end <= today + timedelta(days=365), "invalid_end_date", 422)
        existing = one(
            c, "SELECT * FROM sprints WHERE cabinet_id=:id AND closed_at IS NULL", id=cabinet_id
        )
        require(not existing or existing["end_date"] < today, "sprint_already_active")
        if existing:
            execute(c, "UPDATE sprints SET closed_at=now() WHERE id=:id", id=existing["id"])
        require(
            not one(c, "SELECT id FROM sprints WHERE id=:id", id=p["sprint_id"]),
            "sprint_id_conflict",
        )
        execute(
            c,
            """INSERT INTO sprints(id,cabinet_id,initial_minor,start_date,initial_end_date,end_date,creator_id,calculation_version,topups_today_from)
            VALUES (:id,:cabinet,:initial,:start,:end,:end,:user,3,:start)""",
            id=p["sprint_id"],
            cabinet=cabinet_id,
            initial=p["initial_minor"],
            start=today,
            end=end,
            user=user_id,
        )
        result["entity_id"] = p["sprint_id"]
    elif kind in ("add_entry", "correct_entry", "void_entry"):
        assert sprint is not None
        require(
            today <= sprint["end_date"] or p["allow_completed"], "completed_confirmation_required"
        )
        bank_before = projection_data(c, sprint, today)["projection"]["bank_minor"]
        before = None
        if kind == "add_entry":
            occurred = datetime.fromisoformat(p["occurred_at"].replace("Z", "+00:00"))
            day = occurred.astimezone(ZONE).date()
            require(occurred <= timestamp + timedelta(minutes=5), "clock_skew", 422)
            # An old epoch cannot silently acquire rights after rejoining the same cabinet.
            require(
                occurred >= membership["joined_at"] and p["membership_id"] == str(membership["id"]),
                "membership_epoch_mismatch",
                409,
            )
            if p.get("recovery_of_command_id"):
                original = receipt(c, user_id, p["recovery_of_command_id"])
                require(
                    original
                    and original["status"] == "rejected"
                    and str(original["cabinet_id"]) == str(cabinet_id)
                    and original["command_type"] == "add_entry",
                    "recovery_requires_rejected_command",
                )
                assert original is not None
                original_payload = original["request_json"]["payload"]
                require(
                    original["error_json"]["code"]
                    in {"clock_skew", "entry_outside_sprint", "completed_confirmation_required"},
                    "recovery_not_supported",
                )
                require(
                    original_payload.get("membership_id") == p["membership_id"],
                    "membership_epoch_mismatch",
                )
                require(
                    p["occurred_at"] == original_payload["occurred_at"]
                    or p.get("original_occurred_at") == original_payload["occurred_at"],
                    "recovery_original_date_required",
                )
                require(
                    all(
                        original_payload.get(key) == p.get(key)
                        for key in ("entry_id", "sprint_id", "kind", "amount_minor")
                    ),
                    "recovery_identity_mismatch",
                )
                if p.get("original_occurred_at"):
                    require(
                        p["original_occurred_at"] == original_payload["occurred_at"],
                        "recovery_date_mismatch",
                    )
            end = sprint["initial_end_date"]
            history = rows(
                c,
                "SELECT end_date_after FROM sprint_revisions WHERE sprint_id=:id AND effective_day<=:day ORDER BY sequence",
                id=sprint["id"],
                day=day,
            )
            if history:
                end = history[-1]["end_date_after"]
            require(
                sprint["start_date"] <= day <= end and day <= today, "entry_outside_sprint", 422
            )
            duplicate = one(c, "SELECT id FROM entries WHERE id=:id", id=p["entry_id"])
            require(not duplicate, "entry_id_conflict")
            require(
                exposure(c, sprint) + p["amount_minor"] <= MAX_EXPOSURE, "exposure_exceeded", 422
            )
            execute(
                c,
                """INSERT INTO entries(id,cabinet_id,sprint_id,kind,amount_minor,occurred_at,effective_day,
                author_id,last_editor_id,created_revision,updated_revision,membership_id,recovery_of_command_id,original_occurred_at)
                VALUES (:id,:cabinet,:sprint,:kind,:amount,:occurred,:day,:user,:user,:revision,:revision,:membership,:recovery,:original)""",
                id=p["entry_id"],
                cabinet=cabinet_id,
                sprint=sprint["id"],
                kind=p["kind"],
                amount=p["amount_minor"],
                occurred=occurred,
                day=day,
                user=user_id,
                revision=cabinet["revision"] + 1,
                membership=membership["id"],
                recovery=p.get("recovery_of_command_id"),
                original=p.get("original_occurred_at"),
            )
        else:
            before = one(
                c,
                "SELECT * FROM entries WHERE id=:id AND sprint_id=:sprint AND cabinet_id=:cabinet FOR UPDATE",
                id=p["entry_id"],
                sprint=sprint["id"],
                cabinet=cabinet_id,
            )
            require(before, "entry_not_found", 404)
            assert before is not None
            require(
                before["revision"] == p["expected_entry_revision"],
                "stale_entry_revision",
                details={"entry_revision": before["revision"]},
            )
            require(not before["deleted_at"], "entry_voided")
            if kind == "correct_entry":
                require(
                    exposure(c, sprint) - before["amount_minor"] + p["amount_minor"]
                    <= MAX_EXPOSURE,
                    "exposure_exceeded",
                    422,
                )
                execute(
                    c,
                    "UPDATE entries SET amount_minor=:amount,kind=:kind WHERE id=:id",
                    amount=p["amount_minor"],
                    kind=p["kind"],
                    id=p["entry_id"],
                )
            else:
                execute(c, "UPDATE entries SET deleted_at=now() WHERE id=:id", id=p["entry_id"])
            execute(
                c,
                """UPDATE entries SET revision=revision+1,last_editor_id=:user,updated_revision=:revision
                WHERE id=:id""",
                user=user_id,
                revision=cabinet["revision"] + 1,
                id=p["entry_id"],
            )
        bank_after = projection_data(c, sprint, today)["projection"]["bank_minor"]
        # The cabinet lock serializes spending; the command savepoint rolls back a rejection.
        # Existing historical debt can be corrected without allowing it to grow.
        require(bank_after >= min(bank_before, 0), "insufficient_funds")
        after = one(c, "SELECT * FROM entries WHERE id=:id", id=p["entry_id"])
        assert after is not None
        execute(
            c,
            """INSERT INTO entry_revisions(id,entry_id,entry_revision,actor_id,command_id,before_state,after_state)
            VALUES (:id,:entry,:revision,:user,:command,CAST(:before AS jsonb),CAST(:after AS jsonb))""",
            id=uuid4(),
            entry=p["entry_id"],
            revision=after["revision"],
            user=user_id,
            command=command.command_id,
            before=encode(before) if before else None,
            after=encode(after),
        )
        result["entity_id"] = p["entry_id"]
    elif kind == "update_sprint":
        assert sprint is not None
        require(p["expected_business_date"] == str(today), "business_date_changed")
        require(today <= sprint["end_date"], "sprint_completed")
        end = date.fromisoformat(p["end_date"]) if "end_date" in p else sprint["end_date"]
        require(today <= end <= sprint["start_date"] + timedelta(days=365), "invalid_end_date", 422)
        bank = projection_data(c, sprint, today)["projection"]["bank_minor"]
        target = p.get("bank_minor", bank)
        if "bank_minor" in p:
            require(target >= 0, "insufficient_funds")
        delta = target - bank
        if delta == 0 and end == sprint["end_date"]:
            return result
        require(exposure(c, sprint) + abs(delta) <= MAX_EXPOSURE, "exposure_exceeded", 422)
        sequence = one(
            c,
            "SELECT COALESCE(max(sequence),0)+1 AS value FROM sprint_revisions WHERE sprint_id=:id",
            id=sprint["id"],
        )
        assert sequence is not None
        revision_id = uuid4()
        execute(
            c,
            """INSERT INTO sprint_revisions(id,cabinet_id,sprint_id,sequence,effective_day,actor_id,command_id,
            basis_revision,bank_before_minor,bank_after_minor,bank_adjustment_minor,end_date_before,end_date_after)
            VALUES (:id,:cabinet,:sprint,:sequence,:day,:user,:command,:basis,:before,:after,:delta,:old_end,:new_end)""",
            id=revision_id,
            cabinet=cabinet_id,
            sprint=sprint["id"],
            sequence=sequence["value"],
            day=today,
            user=user_id,
            command=command.command_id,
            basis=cabinet["revision"],
            before=bank,
            after=target,
            delta=delta,
            old_end=sprint["end_date"],
            new_end=end,
        )
        execute(c, "UPDATE sprints SET end_date=:end WHERE id=:id", end=end, id=sprint["id"])
        result["entity_id"] = str(revision_id)
    elif kind == "choose_carry":
        assert sprint is not None
        require(p["day"] == str(today), "business_date_changed")
        projection = projection_data(c, sprint, today)["projection"]
        require(projection["carry_status"] == "pending", "carry_unavailable")
        carry_id = uuid4()
        execute(
            c,
            """INSERT INTO carry_decisions(id,sprint_id,day,mode,actor_id,basis_revision,
            preview_carry_minor,preview_today_minor,preview_distribute_minor,command_id)
            VALUES (:id,:sprint,:day,:mode,:user,:basis,:carry,:today,:distribute,:command)""",
            id=carry_id,
            sprint=sprint["id"],
            day=today,
            mode=p["mode"],
            user=user_id,
            basis=cabinet["revision"],
            carry=projection["carry_minor"],
            today=projection["today_mode_remaining_minor"],
            distribute=projection["distribute_remaining_minor"],
            command=command.command_id,
        )
        result["entity_id"] = str(carry_id)
    elif kind == "create_invitation":
        count = one(
            c,
            "SELECT count(*) AS value FROM memberships WHERE cabinet_id=:id AND left_at IS NULL",
            id=cabinet_id,
        )
        require(count and count["value"] < 10, "cabinet_full")
        code = "".join(secrets.choice("0123456789ABCDEFGHJKMNPQRSTVWXYZ") for _ in range(10))
        invitation_id, expires = uuid4(), timestamp + timedelta(hours=24)
        execute(
            c,
            """INSERT INTO invitations(id,cabinet_id,author_id,code_hmac,code_ciphertext,encryption_key_id,expires_at)
            VALUES (:id,:cabinet,:user,:hash,:cipher,'configured',:expires)""",
            id=invitation_id,
            cabinet=cabinet_id,
            user=user_id,
            hash=invitation_digest(code),
            cipher=encrypt(code),
            expires=expires,
        )
        result["entity_id"] = str(invitation_id)
        result["invitation"] = {
            "id": str(invitation_id),
            "code": code,
            "expires_at": expires.isoformat(),
            "environment": settings().environment,
        }
    elif kind == "revoke_invitation":
        invitation = one(
            c,
            "SELECT * FROM invitations WHERE id=:id AND cabinet_id=:cabinet",
            id=p["invitation_id"],
            cabinet=cabinet_id,
        )
        require(invitation, "invitation_not_found", 404)
        execute(
            c,
            "UPDATE invitations SET revoked_at=now(),code_ciphertext=NULL WHERE id=:id",
            id=p["invitation_id"],
        )
        result["entity_id"] = p["invitation_id"]
    elif kind == "rename_cabinet":
        execute(c, "UPDATE cabinets SET name=:name WHERE id=:id", name=p["name"], id=cabinet_id)
    elif kind == "leave_cabinet":
        execute(c, "UPDATE memberships SET left_at=now() WHERE id=:id", id=membership["id"])
        execute(
            c,
            """UPDATE widget_tokens SET revoked_at=now() WHERE cabinet_id=:cabinet AND device_id IN
            (SELECT id FROM devices WHERE user_id=:user)""",
            cabinet=cabinet_id,
            user=user_id,
        )
        remaining = one(
            c,
            "SELECT count(*) AS value FROM memberships WHERE cabinet_id=:id AND left_at IS NULL",
            id=cabinet_id,
        )
        if remaining and remaining["value"] == 0:
            execute(c, "UPDATE cabinets SET archived_at=now() WHERE id=:id", id=cabinet_id)
            execute(
                c,
                "UPDATE invitations SET revoked_at=now(),code_ciphertext=NULL WHERE cabinet_id=:id AND revoked_at IS NULL",
                id=cabinet_id,
            )
    result["result_revision"] = increment(c, cabinet_id)
    return result


def sync(c: Connection, cabinet_id: Any, user_id: Any, ids: list[UUID]) -> dict:
    lock_cabinet(c, cabinet_id)
    state = snapshot(c, cabinet_id, user_id)
    receipts = []
    found_by_id = (
        {
            str(item["command_id"]): item
            for item in rows(
                c,
                """SELECT command_id,cabinet_id,status,
            result_revision,result_json,error_json FROM command_receipts WHERE user_id=:user
            AND cabinet_id=:cabinet AND command_id=ANY(CAST(:ids AS uuid[]))""",
                user=user_id,
                cabinet=cabinet_id,
                ids=ids,
            )
        }
        if ids
        else {}
    )
    for command_id in ids:
        found = found_by_id.get(str(command_id))
        if not found or str(found["cabinet_id"]) != str(cabinet_id):
            receipts.append(
                {
                    "command_id": command_id,
                    "status": "unknown",
                    "result_revision": None,
                    "result": None,
                    "error": None,
                }
            )
        else:
            result = found["result_json"] if found["status"] == "applied" else None
            if result and "status" not in result:
                result = {
                    "command_id": str(command_id),
                    "status": "applied",
                    "result_revision": found["result_revision"],
                    "entity_id": str(found["cabinet_id"]),
                    "invitation": None,
                }
            receipts.append(
                {
                    "command_id": command_id,
                    "status": found["status"],
                    "result_revision": found["result_revision"],
                    "result": result,
                    "error": found["error_json"],
                }
            )
    return {"snapshot": state, "receipts": receipts}


def cursor_encode(value: list[str]) -> str:
    return base64.urlsafe_b64encode(encode(value).encode()).decode().rstrip("=")


def cursor_decode(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        require(
            isinstance(data, list) and len(data) == 2 and all(isinstance(x, str) for x in data),
            "invalid_cursor",
            422,
        )
        UUID(data[1])
        datetime.fromisoformat(data[0])
        return data
    except (ValueError, TypeError) as exc:
        raise Problem("invalid_cursor", 422) from exc


ENTRY_FIELDS = """id,sprint_id,kind,amount_minor,occurred_at,effective_day,received_at,
    author_id,last_editor_id,revision,deleted_at,
    (SELECT display_name FROM users WHERE id=entries.author_id) AS author_name,
    (SELECT display_name FROM users WHERE id=entries.last_editor_id) AS last_editor_name"""
