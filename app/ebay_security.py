"""Secret handling, OAuth state, and eBay notification verification."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


class EbaySecurityError(Exception):
    pass


class TokenCipher:
    def __init__(self, key: str | None = None) -> None:
        self._key = key

    def _fernet(self) -> Fernet:
        key = self._key or os.environ.get("EBAY_TOKEN_ENCRYPTION_KEY")
        if not key:
            raise EbaySecurityError("eBay token encryption is not configured")
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, UnicodeError) as error:
            raise EbaySecurityError("eBay token encryption is not configured") from error

    def encrypt(self, plaintext: str) -> str:
        return self._fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")

    def configured(self) -> bool:
        try:
            self._fernet()
            return True
        except EbaySecurityError:
            return False

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as error:
            raise EbaySecurityError("the saved eBay authorization cannot be read") from error


class OAuthState:
    def __init__(self, secret: str | None = None) -> None:
        self._serializer = URLSafeTimedSerializer(
            secret or os.environ.get("SESSION_SECRET", ""), salt="ebay-oauth-state"
        )

    def create(self, user_id: int) -> str:
        return self._serializer.dumps({"user_id": user_id})

    def verify(self, value: str, user_id: int, *, max_age: int = 600) -> None:
        try:
            payload = self._serializer.loads(value, max_age=max_age)
        except (BadSignature, SignatureExpired) as error:
            raise EbaySecurityError("the eBay connection request expired") from error
        if payload != {"user_id": user_id}:
            raise EbaySecurityError("the eBay connection request is not valid")


def deletion_challenge_response(challenge: str, verification_token: str, endpoint: str) -> str:
    return hashlib.sha256(f"{challenge}{verification_token}{endpoint}".encode()).hexdigest()


class PublicKeyProvider(Protocol):
    def get_public_key(self, key_id: str) -> str: ...


@dataclass
class _CachedKey:
    pem: str
    expires_at: float


class EbayPublicKeyProvider:
    """Fetch and cache eBay notification public keys for one hour."""

    def __init__(self, *, environment: str = "production") -> None:
        self.environment = environment
        self._cache: dict[str, _CachedKey] = {}
        self._application_token: tuple[str, float] | None = None

    def _access_token(self) -> str:
        if self._application_token and self._application_token[1] > monotonic():
            return self._application_token[0]
        client_id = os.environ.get("EBAY_CLIENT_ID")
        client_secret = os.environ.get("EBAY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise EbaySecurityError("eBay notification verification is not configured")
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        host = "api.sandbox.ebay.com" if self.environment == "sandbox" else "api.ebay.com"
        response = httpx.post(
            f"https://{host}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 7200))
        self._application_token = (token, monotonic() + max(expires_in - 60, 60))
        return token

    def get_public_key(self, key_id: str) -> str:
        cached = self._cache.get(key_id)
        if cached and cached.expires_at > monotonic():
            return cached.pem
        host = "api.sandbox.ebay.com" if self.environment == "sandbox" else "api.ebay.com"
        response = httpx.get(
            f"https://{host}/commerce/notification/v1/public_key/{key_id}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=10,
        )
        response.raise_for_status()
        pem = response.json()["key"]
        self._cache[key_id] = _CachedKey(pem=pem, expires_at=monotonic() + 3600)
        return pem


class NotificationVerifier:
    """Verify the ECDSA signature over the exact request body bytes."""

    def __init__(self, key_provider: PublicKeyProvider) -> None:
        self.key_provider = key_provider

    def verify(self, body: bytes, header: str) -> bool:
        try:
            envelope = json.loads(base64.b64decode(header, validate=True))
            if envelope.get("alg") != "ecdsa" or envelope.get("digest") != "SHA256":
                return False
            key_id = envelope["kid"]
            signature = base64.b64decode(envelope["signature"], validate=True)
            public_key = serialization.load_pem_public_key(
                self.key_provider.get_public_key(key_id).encode("ascii")
            )
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                return False
            public_key.verify(signature, body, ec.ECDSA(hashes.SHA256()))
            return True
        except (
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            InvalidSignature,
            EbaySecurityError,
            httpx.HTTPError,
        ):
            return False
