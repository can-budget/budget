"""Check authored source for recognizable credentials; never print matched values.

This narrow offline check does not replace account-side secret detection or prove
the absence of arbitrary secrets. It does not read runtime secret/env files.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ("services/api", "apps/ios", "infra", "contracts", "scripts", "docs", ".github")
SKIP = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "DerivedData",
    "secrets",
    "backups",
}
PATTERNS = {
    "private-key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{64,}"
    ),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{24,}\b"),
    "database-password-url": re.compile(r"\bpostgres(?:ql)?(?:\+psycopg)?://[^\s:/]+:[^\s/@]+@"),
}


def main() -> None:
    paths = [ROOT / name for name in ("README.md", "Makefile", ".gitignore")]
    for name in SOURCE:
        paths.extend((ROOT / name).rglob("*"))
    failures: list[str] = []
    checked = 0
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if path.is_symlink() or not path.is_file() or set(relative.parts) & SKIP:
            continue
        if path.name.startswith(".env") or path.suffix in {".p8", ".p12", ".key", ".pem"}:
            failures.append(f"{relative}: credential file must remain outside authored source")
            continue
        if path.suffix in {".png", ".jpg", ".jpeg", ".zip", ".pyc"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        checked += 1
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                failures.append(f"{relative}:{line}: possible {kind}; value withheld")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: {checked} authored text files checked for recognizable credential patterns")


if __name__ == "__main__":
    main()
