import base64
import json

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.ebay_security import (
    EbaySecurityError,
    NotificationVerifier,
    OAuthState,
    TokenCipher,
    deletion_challenge_response,
)


def test_refresh_token_is_encrypted_and_round_trips() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())

    encrypted = cipher.encrypt("refresh-token-secret")

    assert encrypted != "refresh-token-secret"
    assert "refresh-token-secret" not in encrypted
    assert cipher.decrypt(encrypted) == "refresh-token-secret"


def test_oauth_state_is_time_limited_and_bound_to_one_user() -> None:
    state = OAuthState("a-long-test-session-secret")
    value = state.create(7)

    state.verify(value, 7)
    with pytest.raises(EbaySecurityError):
        state.verify(value, 8)


def test_deletion_challenge_uses_ebay_field_order() -> None:
    assert deletion_challenge_response("challenge", "token", "https://example.test/delete") == (
        "e4681d662cca1e71d7b7eac9309c8965b396b540c63d69ab8c54473a7be998bc"
    )


def test_notification_signature_verifies_exact_payload_bytes() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    class Keys:
        def get_public_key(self, key_id: str) -> str:
            assert key_id == "key-1"
            return public_pem.decode()

    body = b'{"notification":{"data":{"userId":"abc"}}}'
    signature = private_key.sign(body, ec.ECDSA(hashes.SHA256()))
    header = base64.b64encode(
        json.dumps(
            {
                "alg": "ecdsa",
                "kid": "key-1",
                "signature": base64.b64encode(signature).decode(),
                "digest": "SHA256",
            }
        ).encode()
    ).decode()
    verifier = NotificationVerifier(Keys())

    assert verifier.verify(body, header) is True
    assert verifier.verify(body + b" ", header) is False
