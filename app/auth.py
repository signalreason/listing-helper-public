"""Password, session, and same-origin helpers."""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.accounts import AccountStore, User

PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
SESSION_COOKIE = "listing_helper_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(20))


class Passwords:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerifyMismatchError):
            return False


@dataclass(frozen=True)
class SessionManager:
    explicit_secret: str | None = None

    def _serializer(self) -> URLSafeTimedSerializer:
        secret = self.explicit_secret or os.environ.get("SESSION_SECRET")
        if not secret:
            raise RuntimeError("SESSION_SECRET is not configured")
        return URLSafeTimedSerializer(secret, salt="listing-helper-session-v1")

    def create(self, user: User) -> str:
        return self._serializer().dumps(
            {"user_id": user.id, "password_version": user.password_version}
        )

    def ensure_configured(self) -> None:
        self._serializer()

    def read(self, token: str) -> tuple[int, int] | None:
        try:
            payload = self._serializer().loads(token, max_age=SESSION_MAX_AGE)
            return int(payload["user_id"]), int(payload["password_version"])
        except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
            return None


def authenticated_user(
    request: Request, store: AccountStore, sessions: SessionManager
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = sessions.read(token)
    if session is None:
        return None
    user = store.get_user_by_id(session[0])
    if user is None or not user.enabled or user.password_version != session[1]:
        return None
    return user


def setup_token_matches(candidate: str) -> bool:
    expected = os.environ.get("APP_SETUP_TOKEN")
    return bool(expected) and hmac.compare_digest(candidate, expected)


def request_is_same_origin(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False

    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True
    parsed = urlparse(source)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0]
    return parsed.netloc == request.headers.get("host") and parsed.scheme == forwarded_proto.strip()
