"""Signup, login, logout, and Google sign-in.

Email/password and Google are two ways to prove you own the same kind of
account — both end at the same place: a profiles row and a session cookie.
Nothing downstream (chats, projects, RAG, memory) knows or cares which one
was used.

Self-service: anyone who can reach this instance can create an account.
That's a deliberate choice for now, not an oversight — an enterprise
deployment might want invite-only accounts instead, but that's a decision
for later, tracked in steps-to-implement-Bhoumish.md, not built here.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    clear_session_cookie,
    current_user_id,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..config import Settings
from ..db import session_dependency
from ..models import Profile
from ..schemas import AuthConfigOut, LoginRequest, ProfileOut, SignupRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigOut)
async def auth_config(request: Request) -> AuthConfigOut:
    """No session needed -- the login page calls this before anyone is
    logged in, just to decide whether the Google button is worth showing."""
    settings: Settings = request.app.state.settings
    return AuthConfigOut(google_oauth_enabled=settings.google_oauth_enabled)


@router.post("/signup", response_model=ProfileOut, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Profile:
    existing = await session.execute(select(Profile).where(Profile.email == body.email))
    if existing.scalar_one_or_none() is not None:
        # 409 Conflict, not 400 — the request itself was well-formed, it's
        # the state of the world (this email is taken) that's the problem.
        raise HTTPException(status_code=409, detail="an account with this email already exists")

    profile = Profile(
        display_name=body.display_name,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    session.add(profile)
    await session.flush()

    settings: Settings = request.app.state.settings
    set_session_cookie(response, profile.id, settings)
    return profile


@router.post("/login", response_model=ProfileOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Profile:
    result = await session.execute(select(Profile).where(Profile.email == body.email))
    profile = result.scalar_one_or_none()

    # Same error either way — "no such email" and "wrong password" are kept
    # indistinguishable on purpose, so a login form can't be used to probe
    # which emails have accounts on this instance.
    invalid = HTTPException(status_code=401, detail="incorrect email or password")

    if profile is None:
        raise invalid
    if profile.password_hash is None:
        # A real, distinct case (not a wrong password) — this account only
        # has Google sign-in. Worth a clearer message than "incorrect
        # password", which would send someone hunting for a password that
        # was never set.
        raise HTTPException(status_code=401, detail="this account uses Google sign-in, not a password")
    if not verify_password(body.password, profile.password_hash):
        raise invalid

    settings: Settings = request.app.state.settings
    set_session_cookie(response, profile.id, settings)
    return profile


@router.post("/logout")
async def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=ProfileOut)
async def me(
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Profile:
    """401s if not logged in — deliberately the same shape as any other
    protected route, not a special "am I logged in" contract. The frontend
    treats a 401 here as "show the login page", nothing fancier."""
    user_id = current_user_id(request)
    profile = await session.get(Profile, user_id)
    if profile is None:
        # The session cookie verifies, but the row it points at is gone —
        # only possible if an account was deleted out from under a live
        # session. Same response as no session at all: not logged in.
        raise HTTPException(status_code=401, detail="not logged in")
    return profile


@router.get("/google/login")
async def google_login(request: Request):
    settings: Settings = request.app.state.settings
    if not settings.google_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on this server (GOOGLE_CLIENT_ID/SECRET unset)",
        )
    redirect_uri = str(request.url_for("google_callback"))
    return await request.app.state.oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
):
    settings: Settings = request.app.state.settings
    if not settings.google_oauth_enabled:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured on this server")

    try:
        token = await request.app.state.oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=f"Google sign-in failed: {exc.error}") from exc

    userinfo = token.get("userinfo") or {}
    google_id = userinfo.get("sub")
    email = userinfo.get("email")
    name = userinfo.get("name") or (email.split("@")[0] if email else "Nexus User")
    if not google_id or not email:
        raise HTTPException(status_code=401, detail="Google did not return an email/id")

    result = await session.execute(select(Profile).where(Profile.google_id == google_id))
    profile = result.scalar_one_or_none()

    if profile is None:
        # Same email, no Google id yet — link Google onto the existing
        # password account rather than creating a second, orphaned profile
        # with the same email (which the unique constraint would reject
        # anyway, but "why did signup silently fail" is a worse experience
        # than "your Google account just got linked").
        result = await session.execute(select(Profile).where(Profile.email == email))
        profile = result.scalar_one_or_none()

        if profile is not None:
            profile.google_id = google_id
        else:
            profile = Profile(display_name=name, email=email, google_id=google_id)
            session.add(profile)
        await session.flush()

    # set_session_cookie writes onto a specific Response object — has to be
    # this exact redirect, since anything set on a throwaway Response never
    # reaches the browser.
    redirect = RedirectResponse(url=settings.frontend_url)
    set_session_cookie(redirect, profile.id, settings)
    return redirect
