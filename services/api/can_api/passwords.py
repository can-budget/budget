"""Private password accounts; credentials never double as API bearer tokens."""

import hashlib
import hmac
import re
import secrets

ROUNDS = 600_000


def normalize_login(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", value):
        raise ValueError(
            "Login must contain 3-40 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def hash_password(value: str) -> str:
    if not 15 <= len(value) <= 128:
        raise ValueError("Password must contain 15-128 characters")
    salt = secrets.token_bytes(32)
    derived = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, ROUNDS)
    return f"pbkdf2_sha256${ROUNDS}${salt.hex()}${derived.hex()}"


def verify_password(value: str, encoded: str | None) -> bool:
    # Unknown users take the same expensive path as a wrong password for a real user.
    salt, expected, valid = bytes(32), bytes(32), False
    if encoded:
        parts = encoded.split("$")
        if len(parts) == 4 and parts[:2] == ["pbkdf2_sha256", str(ROUNDS)]:
            try:
                candidate_salt, candidate_hash = bytes.fromhex(parts[2]), bytes.fromhex(parts[3])
                if len(candidate_salt) == 32 and len(candidate_hash) == 32:
                    salt, expected, valid = candidate_salt, candidate_hash, True
            except ValueError:
                pass
    bounded = value if 1 <= len(value) <= 128 else "invalid-password"
    derived = hashlib.pbkdf2_hmac("sha256", bounded.encode(), salt, ROUNDS)
    return hmac.compare_digest(derived, expected) and valid and 1 <= len(value) <= 128
