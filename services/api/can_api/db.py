from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from .config import settings


@lru_cache
def engine() -> Engine:
    url = settings().database_url
    if not url.startswith("postgresql"):
        raise RuntimeError("Can requires PostgreSQL; SQLite is not supported")
    return create_engine(
        url, pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 3}
    )


def transaction() -> Iterator[Connection]:
    with engine().begin() as connection:
        yield connection


def rows(connection: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def one(connection: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    result = connection.execute(text(sql), params).mappings().first()
    return dict(result) if result else None


def execute(connection: Connection, sql: str, **params: Any) -> None:
    connection.execute(text(sql), params)
