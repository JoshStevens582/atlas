from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from atlas.api.deps import (
    assert_login_not_locked,
    clear_login_lockout,
    enforce_demo_rate_limit,
    enforce_forgot_password_rate_limit,
    enforce_login_rate_limit,
    enforce_resend_verification_rate_limit,
    enforce_signup_rate_limit,
    record_login_outcome,
)
from atlas.repositories.user_repo import UserRepository
from atlas.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageOut,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupOut,
    SignupRequest,
    TokenOut,
    VerifyEmailRequest,
)
from atlas.services.auth import (
    AuthError,
    authenticate_user,
    issue_access_token,
    signup_user,
    user_needs_email_verification,
)
from atlas.services.email_verification import (
    resend_verification_email,
    send_verification_email_for_user,
    verify_email_with_token,
)
from atlas.services.password_reset import request_password_reset, reset_password_with_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

SIGNUP_VERIFY_MESSAGE = (
    "Account created. Check your email for a verification link before signing in."
)


def _require_auth_secret(request: Request) -> None:
    if not request.app.state.settings.atlas_auth_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATLAS_AUTH_SECRET is not set.",
        )


@router.post("/signup", response_model=SignupOut)
async def signup(
    payload: SignupRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_signup_rate_limit)],
) -> SignupOut:
    """Anyone can create a real account: password is bcrypt-hashed and
    stored, never kept or logged in plaintext."""
    _require_auth_secret(request)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    try:
        username = await signup_user(
            session_factory,
            payload.username,
            payload.password,
            email=payload.email,
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account was created but could not be loaded.",
        )

    if user_needs_email_verification(user):
        verify_result = await send_verification_email_for_user(
            session_factory,
            settings,
            user=user,
        )
        return SignupOut(
            username=username,
            message=SIGNUP_VERIFY_MESSAGE,
            dev_verify_token=verify_result.dev_verify_token,
        )

    return SignupOut(
        username=username,
        access_token=issue_access_token(settings, username),
    )


@router.post("/login", response_model=TokenOut)
async def login(
    payload: LoginRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> TokenOut:
    _require_auth_secret(request)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    await assert_login_not_locked(request, payload.username)
    username = await authenticate_user(
        session_factory, payload.username, payload.password
    )
    if username is None:
        await record_login_outcome(request, username=payload.username, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
    if user is not None and user_needs_email_verification(user):
        await record_login_outcome(request, username=username, success=False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Verify your email before signing in. "
                "Use resend verification if you need a new link."
            ),
        )

    await record_login_outcome(request, username=username, success=True)
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


@router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_forgot_password_rate_limit)],
) -> MessageOut:
    """Always the same message — do not reveal whether the username exists."""
    _require_auth_secret(request)
    result = await request_password_reset(
        request.app.state.session_factory,
        request.app.state.settings,
        username=payload.username,
    )
    return MessageOut(message=result.message, dev_reset_token=result.dev_reset_token)


@router.post("/reset-password", response_model=MessageOut)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> MessageOut:
    _require_auth_secret(request)
    try:
        username = await reset_password_with_token(
            request.app.state.session_factory,
            raw_token=payload.token,
            new_password=payload.password,
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await clear_login_lockout(request, username)
    return MessageOut(message="Password updated. You can sign in with the new password.")


@router.post("/verify-email", response_model=MessageOut)
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> MessageOut:
    _require_auth_secret(request)
    try:
        username = await verify_email_with_token(
            request.app.state.session_factory,
            raw_token=payload.token,
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut(message=f"Email verified for {username}. You can sign in now.")


@router.post("/resend-verification", response_model=MessageOut)
async def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_resend_verification_rate_limit)],
) -> MessageOut:
    """Same message whether or not the account needs verification."""
    _require_auth_secret(request)
    result = await resend_verification_email(
        request.app.state.session_factory,
        request.app.state.settings,
        username=payload.username,
    )
    return MessageOut(
        message=result.message,
        dev_verify_token=result.dev_verify_token,
    )
