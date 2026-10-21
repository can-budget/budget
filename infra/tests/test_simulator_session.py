"""Operator-only Simulator sessions: namespace isolation, expiry and secret transport."""

import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "apps/ios/tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "apps/ios/tools"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


issuer = load("issue_simulator_session")
runner = load("run_simulator")


@pytest.mark.parametrize("subject", ["real-apple-subject", "can-simulator:", "can-simulator:not-a-uuid"])
def test_operator_cannot_issue_a_session_for_an_apple_identity(subject):
    with pytest.raises(ValueError):
        issuer.issue(subject, "https://151.243.217.6")


@pytest.mark.parametrize("origin", ["http://host", "https://user:pass@host", "https://host/path"])
def test_operator_rejects_non_origin_urls(origin):
    with pytest.raises(ValueError):
        issuer.issue("can-simulator:" + str(uuid4()), origin)


def test_operator_bounds_tokens_and_revokes_only_the_selected_test_account(monkeypatch):
    from can_api import db, ledger, security

    now = datetime(2026, 10, 19, 4, tzinfo=timezone.utc)
    user, session_id = uuid4(), uuid4()
    connection = Mock()
    engine = Mock()
    engine.begin.return_value.__enter__ = Mock(return_value=connection)
    engine.begin.return_value.__exit__ = Mock(return_value=False)
    one = Mock(side_effect=[{"id": user, "deletion_requested_at": None},
                            {"id": session_id, "access_expires_at": now + timedelta(minutes=15)}])
    execute, revoke = Mock(), Mock()
    monkeypatch.setattr(db, "engine", lambda: engine)
    monkeypatch.setattr(db, "one", one)
    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(security, "now", lambda: now)
    monkeypatch.setattr(security, "revoke_user", revoke)
    monkeypatch.setattr(security, "issue_session", lambda c, u: {"access_token": "fixture-access", "refresh_token": "fixture-refresh"})
    monkeypatch.setattr(ledger, "profile", lambda c, u: {"id": u, "display_name": "Simulator"})
    subject = "can-simulator:" + str(uuid4())
    result = issuer.issue(subject, "https://151.243.217.6")
    revoke.assert_called_once_with(connection, user)
    assert one.call_args_list[0].kwargs["subject"] == subject
    assert one.call_args_list[1].kwargs["user"] == user
    assert one.call_args_list[1].kwargs["expiry"] == now + timedelta(hours=8)
    assert execute.call_args.kwargs == {"expiry": now + timedelta(hours=8), "session": session_id}
    assert result["session"]["refresh_expires_at"] == result["expires_at"]
    assert "reauth_token" not in result


def test_ssh_credentials_are_captured_and_never_passed_in_arguments(monkeypatch, capsys):
    secret = "fixture-private-access"
    execute = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({
        "api_origin": "https://151.243.217.6", "session": {"access_token": secret}
    })))
    monkeypatch.setattr(runner, "run", execute)
    result = runner.issue_session("budget", "can-production-api-1", "can-simulator:" + str(uuid4()), "https://151.243.217.6")
    args = execute.call_args.args[0]
    assert "StrictHostKeyChecking=yes" in args
    assert shlex.split(args[-1])[:6] == ["docker", "exec", "-i", "can-production-api-1", "python", "-"]
    assert execute.call_args.kwargs["stdout"] == subprocess.PIPE
    assert secret not in repr(args)
    assert not capsys.readouterr().out
    assert result["session"]["access_token"] == secret
