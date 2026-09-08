import pytest
from fastapi.testclient import TestClient

from app.accounts import MemoryAccountStore
from app.auth import Passwords
from app.main import create_app
from tests.support import StubGenerator, authenticated_client, jpeg_bytes


def setup_client(monkeypatch):
    monkeypatch.setenv("APP_SETUP_TOKEN", "temporary-setup-token")
    store = MemoryAccountStore()
    app = create_app(
        StubGenerator(),
        store,
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
    )
    return TestClient(app), store


def test_unauthenticated_user_is_redirected_to_login() -> None:
    app = create_app(
        StubGenerator(),
        MemoryAccountStore(),
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
    )
    response = TestClient(app).get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_privacy_policy_is_public_and_describes_data_use(monkeypatch) -> None:
    monkeypatch.setenv("APP_OPERATOR_NAME", "Example Resale")
    monkeypatch.setenv("APP_PRIVACY_EMAIL", "privacy@example.com")
    app = create_app(
        StubGenerator(),
        MemoryAccountStore(),
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
    )
    response = TestClient(app).get("/privacy")
    policy = " ".join(response.text.split())

    assert response.status_code == 200
    assert "We do not sell personal information" in policy
    assert "server keeps uploaded photo files only in temporary request storage" in policy
    assert "browser profile that creates a saved draft keeps a device-local copy" in policy
    assert "Effective September 8, 2026" in policy
    assert "Example Resale" in response.text
    assert 'href="mailto:privacy@example.com"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert TestClient(app).get("/static/privacy.html").status_code == 404

    head = TestClient(app).head("/privacy")
    assert head.status_code == 200
    assert head.headers["content-type"] == "text/html; charset=utf-8"


@pytest.mark.parametrize("contact", ["", "not-an-email", 'a@example.com" onclick="alert(1)'])
def test_privacy_policy_rejects_missing_or_invalid_contact(monkeypatch, contact) -> None:
    monkeypatch.setenv("APP_OPERATOR_NAME", "Example Resale")
    monkeypatch.setenv("APP_PRIVACY_EMAIL", contact)
    client, _ = setup_client(monkeypatch)
    response = client.get("/privacy")
    assert response.status_code == 503
    assert "Privacy policy unavailable" in response.text
    assert "Example Resale" not in response.text


def test_privacy_policy_requires_operator_and_escapes_html(monkeypatch) -> None:
    monkeypatch.delenv("APP_OPERATOR_NAME", raising=False)
    monkeypatch.setenv("APP_PRIVACY_EMAIL", "privacy@example.com")
    client, _ = setup_client(monkeypatch)
    assert client.get("/privacy").status_code == 503
    monkeypatch.setenv("APP_OPERATOR_NAME", '<script>alert("example")</script> & Co')
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&amp; Co" in response.text


def test_owner_setup_generates_password_once_and_signs_in(monkeypatch) -> None:
    client, store = setup_client(monkeypatch)

    response = client.post(
        "/api/setup",
        json={"email": " Owner@Example.com ", "setup_token": "temporary-setup-token"},
    )

    assert response.status_code == 201
    password = response.json()["password"]
    assert len(password) == 20
    assert store.get_user_by_email("owner@example.com") is not None
    assert password not in store.get_user_by_email("owner@example.com").password_hash
    assert client.get("/api/session").json()["authenticated"] is True

    repeated = client.post(
        "/api/setup",
        json={"email": "other@example.com", "setup_token": "temporary-setup-token"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "setup_complete"


def test_owner_can_create_second_user_and_copy_generated_password() -> None:
    client, store, _ = authenticated_client()

    response = client.post("/api/users", json={"email": "Second@Example.com"})

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "second@example.com"
    assert len(response.json()["password"]) == 20
    assert (
        response.json()["password"]
        not in store.get_user_by_email("second@example.com").password_hash
    )

    too_many = client.post("/api/users", json={"email": "third@example.com"})
    assert too_many.status_code == 409
    assert too_many.json()["code"] == "account_limit"


def test_non_owner_cannot_manage_users() -> None:
    client, _, _ = authenticated_client(owner=False)

    response = client.get("/api/users")

    assert response.status_code == 403
    assert response.json()["code"] == "owner_required"


def test_password_reset_invalidates_existing_session() -> None:
    owner_client, store, _ = authenticated_client()
    password_hash = Passwords().hash("old-password")
    user = store.create_user("user@example.com", password_hash)
    user_app = create_app(
        StubGenerator(),
        store,
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
    )
    user_client = TestClient(user_app)
    assert (
        user_client.post(
            "/api/sessions", json={"email": user.email, "password": "old-password"}
        ).status_code
        == 200
    )

    reset = owner_client.post(f"/api/users/{user.id}/password-resets")

    assert reset.status_code == 200
    assert user_client.get("/api/session").json()["authenticated"] is False
    new_password = reset.json()["password"]
    assert (
        user_client.post(
            "/api/sessions", json={"email": user.email, "password": new_password}
        ).status_code
        == 200
    )


def test_cross_origin_mutation_is_rejected() -> None:
    client, _, _ = authenticated_client()

    response = client.post(
        "/api/listings/generate",
        headers={"Origin": "https://attacker.invalid"},
        files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 403
    assert response.json()["code"] == "invalid_origin"


def test_failed_logins_are_rate_limited() -> None:
    client, _, _ = authenticated_client()
    client.cookies.clear()

    for _ in range(10):
        response = client.post(
            "/api/sessions", json={"email": "owner@example.com", "password": "wrong"}
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/sessions",
        json={"email": "owner@example.com", "password": "correct-horse-battery-staple"},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"
