"""Signup, login, logout, and what a protected route does without a session."""

from __future__ import annotations

import uuid

from nexus_core.auth import SESSION_COOKIE_NAME
from nexus_core.models import Profile


async def test_signup_creates_an_account_and_logs_in(db_client):
    response = await db_client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": "correct-horse", "display_name": "New Person"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["email"] == "new@example.com"
    assert SESSION_COOKIE_NAME in response.cookies

    # The cookie just set actually works, not just "was present in the response".
    me = await db_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "New Person"


async def test_signup_rejects_a_duplicate_email(db_client):
    body = {"email": "taken@example.com", "password": "correct-horse", "display_name": "First"}
    first = await db_client.post("/auth/signup", json=body)
    assert first.status_code == 201

    second = await db_client.post(
        "/auth/signup",
        json={**body, "display_name": "Second"},
    )
    assert second.status_code == 409


async def test_login_with_the_right_password_works(db_client):
    await db_client.post(
        "/auth/signup",
        json={"email": "login-ok@example.com", "password": "correct-horse", "display_name": "Someone"},
    )
    db_client.cookies.clear()

    response = await db_client.post(
        "/auth/login", json={"email": "login-ok@example.com", "password": "correct-horse"}
    )

    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in response.cookies


async def test_login_with_the_wrong_password_is_rejected(db_client):
    await db_client.post(
        "/auth/signup",
        json={"email": "login-bad@example.com", "password": "correct-horse", "display_name": "Someone"},
    )
    db_client.cookies.clear()

    response = await db_client.post(
        "/auth/login", json={"email": "login-bad@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401


async def test_login_with_an_unknown_email_gives_the_same_error_as_wrong_password(db_client):
    """Deliberately indistinguishable — a login form shouldn't reveal which
    emails have accounts on this instance."""
    db_client.cookies.clear()

    response = await db_client.post(
        "/auth/login", json={"email": "nobody-here@example.com", "password": "anything"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "incorrect email or password"


async def test_a_google_only_account_gets_a_clear_error_on_password_login(db_client, session_factory):
    """A profile with no password_hash — the shape a Google sign-in creates.
    Written directly since there's no live Google to sign in through in a
    test; the endpoint itself is exercised, just not the OAuth round trip."""
    async with session_factory() as session:
        session.add(
            Profile(
                id=uuid.uuid4(),
                display_name="Google Person",
                email="google-only@example.com",
                google_id="fake-google-sub-123",
            )
        )
        await session.commit()

    db_client.cookies.clear()
    response = await db_client.post(
        "/auth/login", json={"email": "google-only@example.com", "password": "anything"}
    )

    assert response.status_code == 401
    assert "Google" in response.json()["detail"]


async def test_logout_actually_ends_the_session(db_client):
    await db_client.post(
        "/auth/signup",
        json={"email": "logout@example.com", "password": "correct-horse", "display_name": "Someone"},
    )

    await db_client.post("/auth/logout")
    db_client.cookies.clear()  # the delete_cookie response header doesn't retroactively unset it client-side here

    response = await db_client.get("/auth/me")
    assert response.status_code == 401


async def test_a_protected_route_401s_with_no_session(db_client):
    db_client.cookies.clear()

    response = await db_client.get("/projects")

    assert response.status_code == 401


async def test_google_login_503s_when_not_configured(db_client):
    """FakeSettings.google_oauth_enabled = False — the common case for
    anyone who hasn't set up a Google Cloud OAuth app yet."""
    response = await db_client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 503
