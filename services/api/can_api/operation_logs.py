"""Metadata-only operation traces; durable financial receipts remain authoritative."""

import json
import logging
import re
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("can.operations")
# Uvicorn configures this handler at INFO even when HTTP access logging is disabled.
logger.parent = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)

_PATH = re.compile(r"/v1/cabinets/([0-9a-fA-F-]{36})/(commands|sync)\Z")
_FIELDS = {
    "request_id",
    "route",
    "method",
    "cabinet_id",
    "command_id",
    "command_type",
    "entry_id",
    "sprint_id",
    "replayed",
    "result_revision",
    "receipt_status",
    "http_status",
    "duration_ms",
    "transaction",
    "error_code",
    "exception_type",
    "requested_receipts",
    "snapshot_revision",
}


def context(request: Request) -> dict:
    return request.scope.get("state", {}).get("operation_trace", {})


def emit(event: str, trace: dict, **fields) -> None:
    if not trace:
        return
    record = {k: v for k, v in (trace | fields).items() if k in _FIELDS and v is not None}
    if record.get("transaction") != "committed":
        record.pop("receipt_status", None)
        record.pop("result_revision", None)
    record.update(event=event, timestamp=datetime.now(UTC).isoformat())
    logger.info(json.dumps(record, sort_keys=True, separators=(",", ":")))


class OperationLogsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        match = _PATH.fullmatch(scope.get("path", "")) if scope["type"] == "http" else None
        if not match or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        try:
            cabinet_id = str(UUID(match[1]))
        except ValueError:
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers", [])).get(b"x-request-id", b"")
        try:
            request_id = str(UUID(incoming.decode("ascii")))
        except (ValueError, UnicodeDecodeError):
            request_id = str(uuid4())
        trace = {
            "request_id": request_id,
            "cabinet_id": cabinet_id,
            "route": match[2],
            "method": "POST",
        }
        scope.setdefault("state", {})["operation_trace"] = trace
        started = time.monotonic()
        emit("request_started", trace)

        async def response_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode("ascii"))
                ]
                status = message["status"]
                # The function-scoped DB dependency commits before response.start.
                # A prepared result is not proof of commit if COMMIT itself failed.
                final = dict(trace)
                if status >= 500 or trace.get("transaction") != "committed":
                    final.pop("receipt_status", None)
                    final.pop("result_revision", None)
                emit(
                    "http_response",
                    final,
                    http_status=status,
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                )
            await send(message)

        try:
            await self.app(scope, receive, response_send)
        except BaseException as error:
            emit(
                "request_interrupted",
                trace,
                exception_type=type(error).__name__,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )
            raise
