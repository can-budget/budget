"""Create an isolated Python toolchain without requiring system ensurepip."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
PIP_URL = "https://bootstrap.pypa.io/pip/pip.pyz"
PIP_SHA256 = "91d5fd9f6f25549fd839c60536c6f1b945316ce3588d34a605635b6071c91526"


def main() -> None:
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=False).create(VENV)
    if (
        subprocess.run(
            [str(python), "-m", "pip", "--version"], capture_output=True, check=False
        ).returncode
        == 0
    ):
        print(f"Python environment ready: {VENV}")
        return
    with urllib.request.urlopen(PIP_URL, timeout=60) as response:
        content = response.read(20 * 1024 * 1024)
    if hashlib.sha256(content).hexdigest() != PIP_SHA256:
        raise SystemExit("pip bootstrap checksum changed; review before updating the pin")
    with tempfile.TemporaryDirectory(prefix="can-bootstrap-") as directory:
        archive = Path(directory) / "pip.pyz"
        archive.write_bytes(content)
        subprocess.run(
            [str(python), str(archive), "install", "--no-cache-dir", "pip==26.2.1"],
            check=True,
        )
    print(f"Python environment ready: {VENV}")


if __name__ == "__main__":
    if sys.version_info < (3, 11):  # noqa: UP036 -- bootstrap may run outside the supported interpreter.
        raise SystemExit("Python 3.11 or newer is required")
    main()
