"""Password hashing, session tokens, and who is making this request.

The one place any of these three things happen. Every route that needs to
know who's asking calls current_user_id(request) from here now — nothing
reads request.app.state.settings.demo_user_id to answer that question
anymore; that shortcut only exists as the fixed identity tests authenticate
as (see conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request, Response

from .config import Settings

SESSION_COOKIE_NAME = "nexus_session"


def hash_password(password: str) -> str:
    """bcrypt, not a faster hash. Slow is the point — it's what makes
    brute-forcing a stolen table of hashes expensive rather than trivial."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_session_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.session_max_age_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str, settings: Settings) -> uuid.UUID | None:
    """None on anything wrong with the token — expired, forged, malformed —
    rather than raising. The caller (current_user_id) turns None into a 401;
    keeping the "why" out of the response body is deliberate, so a forged
    token doesn't get a hint about which part of it was wrong."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def set_session_cookie(response: Response, user_id: uuid.UUID, settings: Settings) -> None:
    token = create_session_token(user_id, settings)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_max_age_seconds,
        # httponly: JavaScript can never read this cookie, even from this
        # site's own frontend code — the only thing that can steal a session
        # this way is something that can already run arbitrary JS on the
        # page, at which point a cookie flag isn't the weak link anymore.
        httponly=True,
        # False in local dev (plain http). A real deployment sets
        # SESSION_COOKIE_SECURE=1 — browsers refuse to store or send a
        # Secure cookie over anything but https.
        secure=settings.session_cookie_secure,
        # lax: sent on normal navigation (including the Google OAuth
        # redirect back from accounts.google.com) but not on cross-site
        # POSTs from somewhere else — the standard middle ground.
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def current_user_id(request: Request) -> uuid.UUID:
    """Who this request is from. Raises 401 if there's no session cookie, or
    if there is one but it's expired or doesn't verify — the one place "not
    logged in" turns into an actual HTTP response, so every route that calls
    this gets that behavior for free instead of reimplementing it."""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="not logged in")
    user_id = decode_session_token(token, settings)
    if user_id is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return user_id
