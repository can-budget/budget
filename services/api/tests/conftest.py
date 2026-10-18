import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from can_api import config


@pytest.fixture
def postgres(monkeypatch):
    """Real PostgreSQL only. An isolated generated schema in the explicitly selected test DB."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not supplied; real PostgreSQL integration NOT RUN")
    if not url.startswith("postgresql"):
        pytest.fail("TEST_DATABASE_URL must target PostgreSQL, never SQLite")
    schema = "can_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, hide_parameters=True, connect_args={"options": f"-csearch_path={schema}"}
    )
    cfg = config.Settings(
        encryption_key=Fernet.generate_key().decode(),
        invitation_hmac_key="test-only-invitation-key",
    )
    monkeypatch.setattr(config, "settings", lambda: cfg)
    from can_api import (
        app,
        auth,
        db,
        deletion_journal,
        deletion_replay,
        deletions,
        ledger,
        security,
        worker,
    )

    for module in (app, auth, ledger, security, worker, deletion_journal, deletions):
        monkeypatch.setattr(module, "settings", lambda: cfg)
    for module in (app, db, worker, deletions, deletion_replay):
        monkeypatch.setattr(module, "engine", lambda: engine)
    alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.begin() as connection:
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "head")
            command.upgrade(alembic, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
