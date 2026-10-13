import json
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from can_api import security
from can_api.config import Settings
from can_api.errors import Problem
from can_api.worker import push_request


@pytest.fixture
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": "test-key",
        "alg": "RS256",
    }
    config = Settings(
        apple_client_id="com.example.can",
        encryption_key=Fernet.generate_key().decode(),
        invitation_hmac_key="test-only-hmac-key",
    )
    monkeypatch.setattr(security, "settings", lambda: config)
    monkeypatch.setattr(security, "_key_cache", {"keys": [jwk], "expires": time.time() + 3600})
    return key, config


def apple_token(key, **overrides):
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": "com.example.can",
        "sub": "apple-user",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "correct-nonce",
    } | overrides
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def test_real_signature_nonce_audience_and_issuer_are_checked(signing):
    key, _ = signing
    assert security.verify_apple(apple_token(key), "correct-nonce")["sub"] == "apple-user"
    for changes in (
        {"aud": "other.app"},
        {"iss": "https://attacker.invalid"},
        {"exp": 1},
        {"nonce": "wrong"},
    ):
        with pytest.raises(Problem):
            security.verify_apple(apple_token(key, **changes), "correct-nonce")
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Problem):
        security.verify_apple(apple_token(wrong_key), "correct-nonce")


def test_hmac_or_unsigned_token_never_accepted(signing):
    encoded = jwt.encode(
        {"sub": "user"}, "a-test-secret-at-least-thirty-two-bytes", algorithm="HS256"
    )
    with pytest.raises(Problem):
        security.verify_apple(encoded)


def test_crypto_roundtrip_and_payload_identity(signing):
    plaintext = "test-token-" + str(uuid4())
    encrypted = security.encrypt(plaintext)
    assert plaintext.encode() not in encrypted
    assert security.decrypt(encrypted) == plaintext
    assert security.payload_hash({"a": 1, "b": 2}) == security.payload_hash({"b": 2, "a": 1})
    assert security.payload_hash({"cabinet": "a"}) != security.payload_hash({"cabinet": "b"})


def test_push_uses_separate_tokens_and_data_free_payload(signing, monkeypatch):
    from can_api import worker

    _, config = signing
    key = ec.generate_private_key(ec.SECP256R1())
    config.apns_key_id = "key"
    config.apns_team_id = "team"
    config.apns_private_key = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    config.apns_app_topic = "com.example.can"
    config.apns_widget_topic = "com.example.can.push-type.widgets"
    monkeypatch.setattr(worker, "settings", lambda: config)
    device = {
        "apns_environment": "production",
        "app_apns_token_ciphertext": security.encrypt("app-token"),
        "widget_push_token_ciphertext": security.encrypt("widget-token"),
    }
    url, headers, payload = push_request(device, False)
    assert url == "https://api.push.apple.com/3/device/app-token"
    assert headers["apns-push-type"] == "background"
    assert payload == {"aps": {"content-available": 1}}
    url, headers, payload = push_request(device, True)
    assert url.endswith("/widget-token")
    assert headers["apns-topic"].endswith(".push-type.widgets")
    assert payload == {"aps": {"content-changed": True}}
