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
PIP_WHEEL = "pip-26.2.1-py3-none-any.whl"
PIP_URL = (
    "https://files.pythonhosted.org/packages/"
    "f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/" + PIP_WHEEL
)
PIP_SHA256 = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e"


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
    # Prefer the interpreter's bundled installer; normal fresh CI needs no download.
    if (
        subprocess.run(
            [str(python), "-I", "-m", "ensurepip", "--upgrade"], capture_output=True
        ).returncode
        == 0
    ):
        print(f"Python environment ready: {VENV}")
        return
    with urllib.request.urlopen(PIP_URL, timeout=60) as response:
        content = response.read(20 * 1024 * 1024)
    if hashlib.sha256(content).hexdigest() != PIP_SHA256:
        raise SystemExit("pip wheel checksum mismatch; refusing to execute the download")
    with tempfile.TemporaryDirectory(prefix="can-bootstrap-") as directory:
        archive = Path(directory) / PIP_WHEEL
        archive.write_bytes(content)
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from pip._internal.cli.main import main; sys.exit(main())",
                str(archive),
                "install",
                "--no-index",
                "--no-deps",
                str(archive),
            ],
            check=True,
        )
    print(f"Python environment ready: {VENV}")


if __name__ == "__main__":
    if sys.version_info < (3, 11):  # noqa: UP036 -- bootstrap may run outside the supported interpreter.
        raise SystemExit("Python 3.11 or newer is required")
    main()
