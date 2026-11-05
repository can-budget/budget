import json
import logging
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from can_api import db, ledger
from can_api.operation_logs import OperationLogsMiddleware, context, emit

CABINET = str(uuid4())
COMMAND = str(uuid4())
PATH = f"/v1/cabinets/{CABINET}/commands"


def records(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == "can.operations"]


def application(monkeypatch, *, fail_commit=False, rejected=False):
    class Engine:
        @contextmanager
        def begin(self):
            yield object()
            if fail_commit:
                raise OperationalError("COMMIT contains-private-data", {}, Exception("secret"))

    monkeypatch.setattr(db, "engine", lambda: Engine())
    app = FastAPI()
    app.add_middleware(OperationLogsMiddleware)

    @app.exception_handler(OperationalError)
    async def failed(request: Request, exc: OperationalError):
        context(request)["error_code"] = "database_unavailable"
        return JSONResponse({"code": "database_unavailable"}, status_code=503)

    @app.post("/v1/cabinets/{cabinet_id}/commands")
    def command(request: Request, c: Annotated[object, Depends(db.transaction, scope="function")]):
        context(request).update(
            command_id=COMMAND,
            command_type="add_entry",
            replayed=True,
            receipt_status="rejected" if rejected else "applied",
            result_revision=None if rejected else 42,
        )
        return JSONResponse(
            {"status": "rejected" if rejected else "applied"}, status_code=409 if rejected else 200
        )

    @app.post("/v1/auth/password")
    def login():
        return {"status": "ok"}

    return app


def test_success_is_logged_only_after_commit_and_correlates_client_attempt(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="can.operations")
    attempt = str(uuid4())
    response = TestClient(application(monkeypatch)).post(PATH, headers={"X-Request-ID": attempt})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == attempt
    logged = records(caplog)
    assert [r["event"] for r in logged] == [
        "request_started",
        "transaction_committed",
        "http_response",
    ]
    assert all(r["request_id"] == attempt for r in logged)
    assert logged[-1]["receipt_status"] == "applied"
    assert logged[-1]["result_revision"] == 42
    assert logged[-1]["replayed"] is True


def test_commit_failure_never_logs_prepared_result_as_applied(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="can.operations")
    response = TestClient(application(monkeypatch, fail_commit=True)).post(PATH)
    assert response.status_code == 503
    logged = records(caplog)
    assert [r["event"] for r in logged] == [
        "request_started",
        "transaction_failed_or_unknown",
        "http_response",
    ]
    assert logged[-1]["transaction"] == "failed_or_unknown"
    assert logged[-1]["error_code"] == "database_unavailable"
    assert all("receipt_status" not in r and "result_revision" not in r for r in logged)
    assert "secret" not in caplog.text and "private-data" not in caplog.text


def test_rejection_commits_its_receipt_without_claiming_an_applied_entry(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="can.operations")
    response = TestClient(application(monkeypatch, rejected=True)).post(PATH)
    assert response.status_code == 409
    assert records(caplog)[-1]["receipt_status"] == "rejected"
    assert records(caplog)[-1]["transaction"] == "committed"


@pytest.mark.parametrize("incoming", ["private-token-in-header", "A" * 500, "not-a-uuid"])
def test_untrusted_correlation_header_is_replaced_not_logged(monkeypatch, caplog, incoming):
    caplog.set_level(logging.INFO, logger="can.operations")
    response = TestClient(application(monkeypatch)).post(PATH, headers={"X-Request-ID": incoming})
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]
    assert incoming not in caplog.text


def test_only_financial_route_metadata_is_logged(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="can.operations")
    client = TestClient(application(monkeypatch))
    client.post("/v1/auth/password", json={"password": "private-password"})
    assert records(caplog) == []
    client.post(
        PATH,
        headers={"Authorization": "Bearer private-token"},
        json={"amount_minor": 123456, "note": "private-note"},
    )
    assert "private-" not in caplog.text and "123456" not in caplog.text


def test_allowlist_ignores_accidental_extra_payload_fields(caplog):
    caplog.set_level(logging.INFO, logger="can.operations")
    emit(
        "test",
        {"request_id": COMMAND, "password": "private-password", "amount_minor": 123456},
        body="private-body",
    )
    assert set(records(caplog)[0]) == {"event", "timestamp", "request_id"}


def test_receipt_replay_is_marked_without_mutating_ledger(monkeypatch):
    monkeypatch.setattr(ledger, "lock_cabinet", lambda *args: {})
    monkeypatch.setattr(ledger, "receipt", lambda *args: {"existing": True})
    monkeypatch.setattr(ledger, "replay", lambda *args: ({"status": "applied"}, 200))
    from can_api.schemas import Command

    command = Command.model_construct(command_id=UUID(COMMAND), type="add_entry", payload={})
    trace = {}
    result, status = ledger.apply_command(object(), uuid4(), CABINET, command, diagnostics=trace)
    assert trace == {"replayed": True}
    assert result == {"status": "applied"} and status == 200


def test_real_command_route_shares_trace_through_existing_middleware(monkeypatch, caplog):
    from can_api import app as api

    class Engine:
        @contextmanager
        def begin(self):
            yield object()

    monkeypatch.setattr(db, "engine", lambda: Engine())
    monkeypatch.setattr(api, "engine", lambda: Engine())
    monkeypatch.setattr(api, "rate_limit", lambda *args: None)
    monkeypatch.setattr(api, "identity", lambda *args, **kwargs: {"user_id": uuid4()})

    def apply(c, user, cabinet, command, *, diagnostics):
        diagnostics["replayed"] = False
        return {
            "command_id": str(command.command_id),
            "status": "applied",
            "result_revision": 5,
        }, 200

    monkeypatch.setattr(api, "apply_command", apply)
    caplog.set_level(logging.INFO, logger="can.operations")
    entry, sprint, membership = str(uuid4()), str(uuid4()), str(uuid4())
    response = TestClient(api.app).post(
        PATH,
        json={
            "command_id": COMMAND,
            "type": "add_entry",
            "payload": {
                "entry_id": entry,
                "sprint_id": sprint,
                "membership_id": membership,
                "kind": "expense",
                "amount_minor": 987654321,
                "occurred_at": "2026-11-05T08:00:00Z",
                "allow_completed": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    final = records(caplog)[-1]
    assert final["command_id"] == COMMAND and final["entry_id"] == entry
    assert final["sprint_id"] == sprint and final["replayed"] is False
    assert final["transaction"] == "committed" and final["result_revision"] == 5
    assert "987654321" not in caplog.text and membership not in caplog.text
