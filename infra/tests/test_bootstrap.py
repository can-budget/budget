"""Exercise installation from a genuinely empty environment and reject modified downloads."""

import importlib.util
import io
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "bootstrap", Path(__file__).resolve().parents[2] / "scripts/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_fresh_environment_uses_bundled_pip_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "VENV", tmp_path / "venv")

    def no_network(*args, **kwargs):
        raise AssertionError("Bundled pip must not fetch a mutable bootstrap URL")

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", no_network)
    bootstrap.main()
    result = subprocess.run(
        [str(bootstrap.VENV / "bin/python"), "-m", "pip", "--version"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_fallback_rejects_unverified_wheel_before_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "VENV", tmp_path / "venv")
    calls = []

    def unavailable(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(bootstrap.subprocess, "run", unavailable)
    monkeypatch.setattr(
        bootstrap.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"modified wheel")
    )
    with pytest.raises(SystemExit, match="checksum mismatch"):
        bootstrap.main()
    assert len(calls) == 2  # Only availability checks; downloaded code never executes.
