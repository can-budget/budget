from typing import Any
from uuid import uuid4


class Problem(Exception):
    def __init__(
        self,
        code: str,
        status: int = 409,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        self.status = status
        self.body = {
            "code": code,
            "message": code,
            "request_id": str(uuid4()),
            "retryable": retryable,
            "details": details or {},
        }
        super().__init__(code)


def require(condition: Any, code: str, status: int = 409, **kwargs: Any) -> None:
    if not condition:
        raise Problem(code, status, **kwargs)
