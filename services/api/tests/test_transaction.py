from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from can_api import db


def test_commit_failure_cannot_send_applied_response(monkeypatch):
    events = []

    class Engine:
        @contextmanager
        def begin(self):
            events.append("begin")
            yield object()
            events.append("commit_failed")
            raise OperationalError("COMMIT", {}, Exception("test failure"))

    monkeypatch.setattr(db, "engine", lambda: Engine())
    application = FastAPI()

    @application.exception_handler(OperationalError)
    async def failed(request: Request, exc: OperationalError):
        return JSONResponse({"code": "database_unavailable"}, status_code=503)

    @application.get("/command")
    def command(connection: Annotated[object, Depends(db.transaction, scope="function")]):
        events.append("applied_prepared")
        return {"status": "applied"}

    response = TestClient(application).get("/command")
    assert response.status_code == 503
    assert response.json() == {"code": "database_unavailable"}
    assert events == ["begin", "applied_prepared", "commit_failed"]
