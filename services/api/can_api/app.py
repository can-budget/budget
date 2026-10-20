from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import schemas
from .auth import DB, Bearer
from .auth import router as auth_router
from .config import settings
from .db import engine, execute, one, rows
from .errors import Problem, require
from .ledger import (
    ENTRY_FIELDS,
    apply_command,
    cursor_decode,
    cursor_encode,
    get_sprint,
    increment,
    lock_cabinet,
    member,
    payload_hash,
    projection_data,
    receipt,
    snapshot,
    sprint_json,
    store_receipt,
    sync,
)
from .security import digest, encrypt, identity, invitation_digest, now, rate_limit, token

app = FastAPI(title="Can shared budget API", version="1.2.0")
app.include_router(auth_router)


@app.exception_handler(Problem)
async def problem_handler(request: Request, error: Problem):
    headers = {"Retry-After": "60"} if error.status == 429 else None
    return JSONResponse(jsonable_encoder(error.body), status_code=error.status, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, error: RequestValidationError):
    # Pydantic errors may include passwords, JWTs or financial payloads in 'input'.
    details = {"fields": [".".join(str(part) for part in item["loc"]) for item in error.errors()]}
    return JSONResponse(Problem("invalid_request", 422, details=details).body, status_code=422)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, error: SQLAlchemyError):
    code, status = (
        ("concurrent_conflict", 409)
        if isinstance(error, IntegrityError)
        else ("database_unavailable", 503)
    )
    return JSONResponse(Problem(code, status, retryable=status == 503).body, status_code=status)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.middleware("http")
async def limit_requests(request: Request, call_next):
    if request.method in ("POST", "PATCH", "DELETE"):
        # Separate committed transaction: invalid credentials/codes also consume the limit.
        try:
            with engine().begin() as c:
                path = request.url.path
                limit = 30 if "/auth/" in path or "/invitations" in path else 300
                if path.endswith("/sync"):
                    limit = 1200
                rate_limit(
                    c, f"{request.client.host if request.client else 'unknown'}:{path}", limit
                )
        except Problem as error:
            return JSONResponse(error.body, status_code=error.status, headers={"Retry-After": "60"})
        except SQLAlchemyError:
            return JSONResponse(
                Problem("database_unavailable", 503, retryable=True).body, status_code=503
            )
    return await call_next(request)


@app.get("/health/live")
def live():
    return {"status": "ok", "version": "1.2.0"}


@app.get("/health/ready")
def ready():
    try:
        with engine().connect() as c:
            value = one(c, "SELECT version_num FROM alembic_version")
            require(value and value["version_num"] == "0003", "schema_not_ready", 503)
            overdue = one(
                c,
                "SELECT id FROM deletion_jobs WHERE status <> 'done' AND requested_at < now()-interval '24 hours' LIMIT 1",
            )
            require(not overdue, "deletion_overdue", 503)
        if settings().recovery_mode == "off_host":
            from .deletion_journal import check_health

            check_health()
    except SQLAlchemyError as exc:
        raise Problem("database_unavailable", 503, retryable=True) from exc
    return {"status": "ok", "version": "1.2.0", "recovery_mode": settings().recovery_mode}


@app.post("/v1/cabinets", status_code=201)
def create_cabinet(body: schemas.CreateCabinet, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)["user_id"]
    request = body.model_dump(mode="json") | {"type": "create_cabinet"}
    old = receipt(c, user, body.command_id)
    if old:
        require(old["payload_hash"] == payload_hash(request), "command_payload_conflict")
        return old["result_json"]
    require(
        not one(c, "SELECT id FROM memberships WHERE user_id=:id AND left_at IS NULL", id=user),
        "already_member",
    )
    cabinet_id, membership_id = uuid4(), uuid4()
    execute(
        c,
        "INSERT INTO cabinets(id,name,timezone,creator_id) VALUES (:id,:name,'Asia/Shanghai',:user)",
        id=cabinet_id,
        name=body.name,
        user=user,
    )
    execute(
        c,
        "INSERT INTO memberships(id,cabinet_id,user_id) VALUES (:id,:cabinet,:user)",
        id=membership_id,
        cabinet=cabinet_id,
        user=user,
    )
    increment(c, cabinet_id)
    result = {"command_id": str(body.command_id), "cabinet_id": str(cabinet_id)}
    store_receipt(c, user, body.command_id, cabinet_id, request, result)
    return result


@app.post("/v1/invitations/preview")
def preview_invitation(body: schemas.InvitationCode, c: DB, authorization: Bearer = None):
    identity(c, authorization)
    invitation = one(
        c, "SELECT * FROM invitations WHERE code_hmac=:hash", hash=invitation_digest(body.code)
    )
    require(invitation, "invalid_invitation", 404)
    assert invitation is not None
    cabinet = lock_cabinet(c, invitation["cabinet_id"])
    require(
        not cabinet["archived_at"]
        and not invitation["revoked_at"]
        and not invitation["used_at"]
        and invitation["expires_at"] > now(),
        "invalid_invitation",
        404,
    )
    count = one(
        c,
        "SELECT count(*) AS value FROM memberships WHERE cabinet_id=:id AND left_at IS NULL",
        id=cabinet["id"],
    )
    author = one(c, "SELECT display_name FROM users WHERE id=:id", id=invitation["author_id"])
    assert count is not None
    return {
        "cabinet_name": cabinet["name"],
        "inviter_name": author["display_name"] if author else "Участник",
        "available_slots": max(10 - count["value"], 0),
        "member_count": count["value"],
        "expires_at": invitation["expires_at"],
    }


@app.post("/v1/invitations/accept")
def accept_invitation(body: schemas.AcceptInvitation, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)["user_id"]
    # Codes are hashed in receipts; encrypted invitation storage is short lived.
    request = {
        "type": "accept_invitation",
        "command_id": str(body.command_id),
        "code_hash": invitation_digest(body.code).hex(),
    }
    old = receipt(c, user, body.command_id)
    if old:
        require(old["payload_hash"] == payload_hash(request), "command_payload_conflict")
        return old["result_json"]
    invitation = one(
        c, "SELECT * FROM invitations WHERE code_hmac=:hash", hash=invitation_digest(body.code)
    )
    require(invitation, "invalid_invitation", 404)
    assert invitation is not None
    cabinet = lock_cabinet(c, invitation["cabinet_id"])
    invitation = one(c, "SELECT * FROM invitations WHERE id=:id FOR UPDATE", id=invitation["id"])
    assert invitation is not None
    require(
        not cabinet["archived_at"]
        and not invitation["revoked_at"]
        and not invitation["used_at"]
        and invitation["expires_at"] > now(),
        "invalid_invitation",
        404,
    )
    require(
        not one(c, "SELECT id FROM memberships WHERE user_id=:id AND left_at IS NULL", id=user),
        "already_member",
    )
    count = one(
        c,
        "SELECT count(*) AS value FROM memberships WHERE cabinet_id=:id AND left_at IS NULL",
        id=cabinet["id"],
    )
    require(count and count["value"] < 10, "cabinet_full")
    execute(
        c,
        "INSERT INTO memberships(id,cabinet_id,user_id) VALUES (:id,:cabinet,:user)",
        id=uuid4(),
        cabinet=cabinet["id"],
        user=user,
    )
    execute(
        c,
        "UPDATE invitations SET used_at=now(),used_by=:user,code_ciphertext=NULL WHERE id=:id",
        user=user,
        id=invitation["id"],
    )
    increment(c, cabinet["id"])
    result = {"command_id": str(body.command_id), "cabinet_id": str(cabinet["id"])}
    store_receipt(c, user, body.command_id, cabinet["id"], request, result)
    return result


@app.post("/v1/cabinets/{cabinet_id}/commands")
def commands(cabinet_id: UUID, body: schemas.Command, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)["user_id"]
    result, status = apply_command(c, user, cabinet_id, body)
    return JSONResponse(jsonable_encoder(result), status_code=status)


@app.get("/v1/commands/{command_id}/receipt")
def own_receipt(command_id: UUID, c: DB, authorization: Bearer = None):
    user = identity(c, authorization)["user_id"]
    found = receipt(c, user, command_id)
    if not found:
        return {
            "command_id": command_id,
            "status": "unknown",
            "result_revision": None,
            "result": None,
            "error": None,
        }
    result = None
    error = None
    if found["status"] == "applied":
        stored = found["result_json"]
        result = {
            "command_id": command_id,
            "status": "applied",
            "result_revision": found["result_revision"],
            "entity_id": stored.get("entity_id", stored.get("cabinet_id")),
            "invitation": None,
        }
    else:
        # This endpoint never reveals financial preview details, including after leave.
        error = {**found["error_json"], "details": {}}
    return {
        "command_id": command_id,
        "status": found["status"],
        "result_revision": found["result_revision"],
        "result": result,
        "error": error,
    }


@app.post("/v1/cabinets/{cabinet_id}/sync")
def synchronize(cabinet_id: UUID, body: schemas.SyncRequest, c: DB, authorization: Bearer = None):
    user = identity(c, authorization)["user_id"]
    return sync(c, cabinet_id, user, body.unresolved_command_ids)


@app.get("/v1/cabinets/{cabinet_id}/snapshot")
def read_snapshot(
    cabinet_id: UUID,
    c: DB,
    authorization: Bearer = None,
    if_none_match: Annotated[str | None, Header()] = None,
):
    user = identity(c, authorization)["user_id"]
    lock_cabinet(c, cabinet_id)
    state = snapshot(c, cabinet_id, user)
    etag = f'"{cabinet_id}:{state["cabinet_revision"]}:{state["business_date"]}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(jsonable_encoder(state), headers={"ETag": etag})


@app.get("/v1/cabinets/{cabinet_id}/sprints")
def sprints(
    cabinet_id: UUID,
    c: DB,
    authorization: Bearer = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    user_id = identity(c, authorization)["user_id"]
    lock_cabinet(c, cabinet_id)
    member(c, cabinet_id, user_id)
    key = cursor_decode(cursor)
    found = rows(
        c,
        "SELECT * FROM sprints WHERE cabinet_id=:cabinet "
        + ("AND (start_date,id)<(CAST(:date AS date),CAST(:id AS uuid)) " if key else "")
        + "ORDER BY start_date DESC,id DESC LIMIT :limit",
        cabinet=cabinet_id,
        date=key[0] if key else None,
        id=key[1] if key else None,
        limit=limit + 1,
    )
    page = found[:limit]
    return {
        "items": [sprint_json(item) for item in page],
        "next_cursor": cursor_encode([str(page[-1]["start_date"]), str(page[-1]["id"])])
        if len(found) > limit
        else None,
    }


@app.get("/v1/cabinets/{cabinet_id}/sprints/{sprint_id}/entries")
def entries(
    cabinet_id: UUID,
    sprint_id: UUID,
    c: DB,
    authorization: Bearer = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    user = identity(c, authorization)["user_id"]
    cabinet = lock_cabinet(c, cabinet_id)
    member(c, cabinet_id, user)
    get_sprint(c, cabinet_id, sprint_id)
    key = cursor_decode(cursor)
    found = rows(
        c,
        f"SELECT {ENTRY_FIELDS} FROM entries WHERE sprint_id=:sprint "
        + ("AND (occurred_at,id)<(CAST(:date AS timestamptz),CAST(:id AS uuid)) " if key else "")
        + "ORDER BY occurred_at DESC,id DESC LIMIT :limit",
        sprint=sprint_id,
        date=key[0] if key else None,
        id=key[1] if key else None,
        limit=limit + 1,
    )
    page = found[:limit]
    return {
        "items": page,
        "next_cursor": cursor_encode([str(page[-1]["occurred_at"]), str(page[-1]["id"])])
        if len(found) > limit
        else None,
        "observed_cabinet_revision": cabinet["revision"],
    }


@app.get("/v1/cabinets/{cabinet_id}/sprints/{sprint_id}/entries/{entry_id}")
def entry(cabinet_id: UUID, sprint_id: UUID, entry_id: UUID, c: DB, authorization: Bearer = None):
    user_id = identity(c, authorization)["user_id"]
    lock_cabinet(c, cabinet_id)
    member(c, cabinet_id, user_id)
    found = one(
        c,
        f"SELECT {ENTRY_FIELDS} FROM entries WHERE id=:id AND sprint_id=:sprint AND cabinet_id=:cabinet",
        id=entry_id,
        sprint=sprint_id,
        cabinet=cabinet_id,
    )
    require(found, "entry_not_found", 404)
    return found


@app.get("/v1/cabinets/{cabinet_id}/sprints/{sprint_id}/entries/{entry_id}/revisions")
def entry_revisions(
    cabinet_id: UUID, sprint_id: UUID, entry_id: UUID, c: DB, authorization: Bearer = None
):
    entry(cabinet_id, sprint_id, entry_id, c, authorization)
    return {
        "items": rows(
            c,
            """SELECT r.entry_revision,r.actor_id,u.display_name AS actor_name,
        r.before_state,r.after_state,r.created_at FROM entry_revisions r LEFT JOIN users u ON u.id=r.actor_id
        WHERE entry_id=:id ORDER BY entry_revision""",
            id=entry_id,
        )
    }


@app.get("/v1/cabinets/{cabinet_id}/sprints/{sprint_id}/projection")
def archived_projection(cabinet_id: UUID, sprint_id: UUID, c: DB, authorization: Bearer = None):
    user = identity(c, authorization)["user_id"]
    cabinet = lock_cabinet(c, cabinet_id)
    member(c, cabinet_id, user)
    sprint = get_sprint(c, cabinet_id, sprint_id)
    return {
        "server_time": now(),
        "cabinet_revision": cabinet["revision"],
        "sprint": sprint_json(sprint),
        **projection_data(c, sprint),
    }


@app.post("/v1/devices", status_code=204)
def register_device(body: schemas.DeviceRegistration, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)
    existing = one(c, "SELECT * FROM devices WHERE id=:id FOR UPDATE", id=body.device_id)
    require(not existing or existing["user_id"] == user["user_id"], "device_owner_mismatch", 403)
    execute(
        c,
        """INSERT INTO devices(id,user_id,session_id,app_apns_token_ciphertext,widget_push_token_ciphertext,
        apns_environment,app_version,os_version) VALUES (:id,:user,:session,:app,:widget,:environment,:version,:os)
        ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,
        app_apns_token_ciphertext=excluded.app_apns_token_ciphertext,
        widget_push_token_ciphertext=excluded.widget_push_token_ciphertext,
        apns_environment=excluded.apns_environment,app_version=excluded.app_version,
        os_version=excluded.os_version,updated_at=now()""",
        id=body.device_id,
        user=user["user_id"],
        session=user["id"],
        app=encrypt(body.app_apns_token) if body.app_apns_token else None,
        widget=encrypt(body.widget_push_token) if body.widget_push_token else None,
        environment=body.apns_environment,
        version=body.app_version,
        os=body.os_version,
    )
    return Response(status_code=204)


@app.post("/v1/devices/{device_id}/widget-token")
def widget_token(device_id: UUID, c: DB, authorization: Bearer = None):
    user = identity(c, authorization, lock=True)
    membership = one(
        c,
        "SELECT cabinet_id FROM memberships WHERE user_id=:id AND left_at IS NULL",
        id=user["user_id"],
    )
    require(membership, "membership_required", 403)
    assert membership is not None
    cabinet_id = membership["cabinet_id"]
    lock_cabinet(c, cabinet_id)
    member(c, cabinet_id, user["user_id"])
    device = one(
        c,
        "SELECT * FROM devices WHERE id=:id AND user_id=:user AND session_id=:session",
        id=device_id,
        user=user["user_id"],
        session=user["id"],
    )
    require(device, "device_not_found", 404)
    raw, expires = token(), now() + timedelta(days=settings().widget_days)
    execute(
        c,
        "UPDATE widget_tokens SET revoked_at=now() WHERE device_id=:id AND revoked_at IS NULL",
        id=device_id,
    )
    execute(
        c,
        "INSERT INTO widget_tokens(id,device_id,cabinet_id,token_hash,expires_at) VALUES (:id,:device,:cabinet,:hash,:expires)",
        id=uuid4(),
        device=device_id,
        cabinet=cabinet_id,
        hash=digest(raw),
        expires=expires,
    )
    return {"token": raw, "expires_at": expires}


@app.get("/v1/widget/snapshot")
def widget_snapshot(c: DB, authorization: Bearer = None):
    require(authorization and authorization.startswith("Bearer "), "unauthorized", 401)
    widget = one(
        c,
        """SELECT w.*,d.user_id FROM widget_tokens w JOIN devices d ON d.id=w.device_id
        JOIN sessions s ON s.id=d.session_id WHERE w.token_hash=:hash AND w.revoked_at IS NULL
        AND w.expires_at>now() AND s.revoked_at IS NULL AND s.absolute_expires_at>now()""",
        hash=digest(str(authorization)[7:]),
    )
    require(widget, "widget_unauthorized", 401)
    assert widget is not None
    lock_cabinet(c, widget["cabinet_id"])
    state = snapshot(c, widget["cabinet_id"], widget["user_id"])
    projection = state["projection"]
    return {
        "server_time": state["server_time"],
        "business_date": state["business_date"],
        "cabinet_revision": state["cabinet_revision"],
        "calculation_version": state["sprint"]["calculation_version"] if state["sprint"] else 2,
        "status": projection["status"] if projection else "no_sprint",
        "today_remaining_minor": projection["today_remaining_minor"] if projection else None,
        "bank_minor": projection["bank_minor"] if projection else None,
        "carry_pending": bool(projection and projection["carry_status"] == "pending"),
    }
