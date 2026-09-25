from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from atlas.api.deps import (
    enforce_demo_rate_limit,
    enforce_login_rate_limit,
    enforce_signup_rate_limit,
)
from atlas.schemas.auth import LoginRequest, SignupRequest, TokenOut
from atlas.services.auth import AuthError, authenticate_user, issue_access_token, signup_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _require_auth_secret(request: Request) -> None:
    if not request.app.state.settings.atlas_auth_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATLAS_AUTH_SECRET is not set.",
        )


@router.post("/signup", response_model=TokenOut)
async def signup(
    payload: SignupRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_signup_rate_limit)],
) -> TokenOut:
    """Anyone can create a real account: password is bcrypt-hashed and
    stored, never kept or logged in plaintext."""
    _require_auth_secret(request)
    settings = request.app.state.settings
    try:
        username = await signup_user(
            request.app.state.session_factory, payload.username, payload.password
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TokenOut(access_token=issue_access_token(settings, username), username=username)


@router.post("/login", response_model=TokenOut)
async def login(
    payload: LoginRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> TokenOut:
    _require_auth_secret(request)
    settings = request.app.state.settings
    username = await authenticate_user(
        request.app.state.session_factory, payload.username, payload.password
    )
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    return TokenOut(access_token=issue_access_token(settings, username), username=username)


@router.post("/demo", response_model=TokenOut)
async def demo_login(
    request: Request,
    _: Annotated[None, Depends(enforce_demo_rate_limit)],
) -> TokenOut:
    """One-click path for anyone just looking at the app (recruiters,
    reviewers): logs in as the first seeded demo account with no typing.
    Goes through the exact same authenticate_user() as a real login —
    it's a real account, just a pre-made one."""
    _require_auth_secret(request)
    settings = request.app.state.settings
    first_entry = settings.atlas_demo_users.split("|")[0]
    if ":" not in first_entry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No demo account is configured.",
        )
    demo_username, demo_password = (part.strip() for part in first_entry.split(":", 1))
    username = await authenticate_user(
        request.app.state.session_factory, demo_username, demo_password
    )
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo account is not available.",
        )
    return TokenOut(access_token=issue_access_token(settings, username), username=username)
