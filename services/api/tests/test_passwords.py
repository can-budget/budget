from unittest.mock import patch

import pytest

from can_api.passwords import ROUNDS, hash_password, normalize_login, verify_password
from can_api.schemas import PasswordLogin


def test_passwords_are_salted_and_wrong_unknown_and_malformed_credentials_fail():
    value = "a long test password 123"
    encoded = hash_password(value)
    assert encoded != hash_password(value) and value not in encoded
    assert verify_password(value, encoded)
    assert not verify_password("a different test password", encoded)
    assert not verify_password(value, None)
    assert not verify_password(value, "pbkdf2_sha256$1$00$00")
    assert not verify_password(value, f"pbkdf2_sha256${ROUNDS}$invalid$invalid")
    assert not verify_password(value + " ", encoded)


def test_unknown_users_and_wrong_passwords_both_run_the_kdf():
    encoded = hash_password("a long test password")
    for stored in [None, encoded, "malformed"]:
        with patch("can_api.passwords.hashlib.pbkdf2_hmac", return_value=bytes(32)) as derive:
            assert not verify_password("wrong", stored)
        assert derive.call_args.args[0] == "sha256"
        assert derive.call_args.args[3] == ROUNDS


def test_password_length_is_bounded_without_silent_truncation():
    for value in ["short", "a" * 129]:
        with pytest.raises(ValueError):
            hash_password(value)
    assert not verify_password("a" * 129, None)
    value = "длинный пароль для проверки"
    assert verify_password(value, hash_password(value))


def test_login_is_canonical_and_request_repr_never_contains_password():
    assert normalize_login("  Owner.One ") == "owner.one"
    request = PasswordLogin(login=" OWNER ", password="a long test password")
    assert request.login == "owner" and "a long test password" not in repr(request)
    for value in ["x", "a" * 41, "login@example.com", "тест", "../../x"]:
        with pytest.raises(ValueError):
            normalize_login(value)
