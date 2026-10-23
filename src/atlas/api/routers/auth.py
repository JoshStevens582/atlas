from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from atlas.api.deps import (
    assert_login_not_locked,
    clear_login_lockout,
    enforce_demo_rate_limit,
    enforce_forgot_password_rate_limit,
    enforce_login_rate_limit,
    enforce_refresh_rate_limit,
    enforce_resend_verification_rate_limit,
    enforce_signup_rate_limit,
    record_login_outcome,
    require_user,
)
from atlas.db.models import User
from atlas.repositories.user_repo import UserRepository
from atlas.schemas.auth import (
    AuthUser,
    ForgotPasswordRequest,
    LoginRequest,
    MeOut,
    MessageOut,
    MfaCodeRequest,
    MfaConfirmOut,
    MfaDisableRequest,
    MfaLoginRequest,
    MfaSetupOut,
    MfaSetupRequest,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupOut,
    SignupRequest,
    TokenOut,
    VerifyEmailRequest,
)
from atlas.services.admin import user_is_disabled
from atlas.services.auth import (
    AuthError,
    authenticate_user,
    issue_mfa_challenge_token,
    signup_user,
    user_needs_email_verification,
    user_role,
    verify_mfa_challenge_token,
)
from atlas.services.email_verification import (
    resend_verification_email,
    send_verification_email_for_user,
    verify_email_with_token,
)
from atlas.services.mfa import (
    MfaRejected,
    accept_second_factor,
    begin_mfa_setup,
    confirm_mfa_setup,
    disable_mfa,
    regenerate_recovery_codes,
)
from atlas.services.password_reset import request_password_reset, reset_password_with_token
from atlas.services.refresh_tokens import (
    RefreshError,
    issue_session_pair,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

SIGNUP_VERIFY_MESSAGE = (
    "Account created. Check your email for a verification link before signing in."
)


async def _complete_login(request: Request, user: User, username: str) -> TokenOut:
    """Issue access + refresh, or an MFA challenge when authenticator is on."""
    settings = request.app.state.settings
    role = user_role(user)
    if user.mfa_enabled_at is not None:
        return TokenOut(
            username=username,
            role=role,
            mfa_required=True,
            mfa_token=issue_mfa_challenge_token(settings, username),
        )
    pair = await issue_session_pair(
        request.app.state.session_factory,
        settings,
        user=user,
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        username=pair.username,
        role=pair.role,
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


@router.post("/login", response_model=TokenOut)
async def login(
    payload: LoginRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> TokenOut:
    _require_auth_secret(request)
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
    if user is not None and user_is_disabled(user):
        await record_login_outcome(request, username=username, success=False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    if user is None:
        await record_login_outcome(request, username=username, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token = await _complete_login(request, user, username)
    if token.mfa_required:
        return token
    await record_login_outcome(request, username=username, success=True)
    return token


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
    async with request.app.state.session_factory() as session:
        demo_user = await UserRepository(session).get_by_username(username)
    if demo_user is not None and user_is_disabled(demo_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    if demo_user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo account is not available.",
        )
    return await _complete_login(request, demo_user, username)


@router.get("/me", response_model=MeOut)
async def me(user: Annotated[AuthUser, Depends(require_user)]) -> MeOut:
    return MeOut(username=user.username, role=user.role, mfa_enabled=user.mfa_enabled)


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


@router.post("/login/mfa", response_model=TokenOut)
async def login_mfa(
    payload: MfaLoginRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_login_rate_limit)],
) -> TokenOut:
    """Exchange a password-step challenge plus an authenticator code for a session."""
    _require_auth_secret(request)
    settings = request.app.state.settings
    username = verify_mfa_challenge_token(settings, payload.mfa_token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in step expired. Enter your password again.",
        )
    await assert_login_not_locked(request, username)
    async with request.app.state.session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in step expired. Enter your password again.",
        )
    if user_is_disabled(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    if user.mfa_enabled_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authenticator is not on for this account.",
        )
    try:
        await accept_second_factor(
            request.app.state.session_factory,
            settings,
            username=username,
            code=payload.code,
        )
    except MfaRejected as exc:
        await record_login_outcome(request, username=username, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_login_outcome(request, username=username, success=True)
    pair = await issue_session_pair(
        request.app.state.session_factory,
        settings,
        user=user,
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        username=pair.username,
        role=pair.role,
    )


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(
    payload: MfaSetupRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> MfaSetupOut:
    _require_auth_secret(request)
    try:
        setup = await begin_mfa_setup(
            request.app.state.session_factory,
            request.app.state.settings,
            username=user.username,
            password=payload.password,
        )
    except MfaRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MfaSetupOut(
        secret=setup.secret,
        otpauth_uri=setup.otpauth_uri,
        qr_data_uri=setup.qr_data_uri,
    )


@router.post("/mfa/confirm", response_model=MfaConfirmOut)
async def mfa_confirm(
    payload: MfaCodeRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> MfaConfirmOut:
    _require_auth_secret(request)
    try:
        codes = await confirm_mfa_setup(
            request.app.state.session_factory,
            request.app.state.settings,
            username=user.username,
            code=payload.code,
        )
    except (MfaRejected, AuthError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MfaConfirmOut(recovery_codes=codes)


@router.post("/mfa/disable", response_model=MessageOut)
async def mfa_disable(
    payload: MfaDisableRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> MessageOut:
    _require_auth_secret(request)
    try:
        await disable_mfa(
            request.app.state.session_factory,
            request.app.state.settings,
            username=user.username,
            password=payload.password,
            code=payload.code,
        )
    except (MfaRejected, AuthError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageOut(message="Authenticator turned off. Sign-in is password only again.")


@router.post("/mfa/recovery-codes/regenerate", response_model=MfaConfirmOut)
async def mfa_regenerate_recovery_codes(
    payload: MfaDisableRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> MfaConfirmOut:
    """New recovery codes after password plus a current authenticator code."""
    _require_auth_secret(request)
    try:
        codes = await regenerate_recovery_codes(
            request.app.state.session_factory,
            request.app.state.settings,
            username=user.username,
            password=payload.password,
            code=payload.code,
        )
    except (MfaRejected, AuthError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MfaConfirmOut(recovery_codes=codes)


@router.post("/refresh", response_model=TokenOut)
async def refresh_session(
    payload: RefreshTokenRequest,
    request: Request,
    _: Annotated[None, Depends(enforce_refresh_rate_limit)],
) -> TokenOut:
    _require_auth_secret(request)
    try:
        pair = await rotate_refresh_token(
            request.app.state.session_factory,
            request.app.state.settings,
            raw_refresh_token=payload.refresh_token,
        )
    except RefreshError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        username=pair.username,
        role=pair.role,
    )


@router.post("/logout", response_model=MessageOut)
async def logout_session(
    payload: RefreshTokenRequest,
    request: Request,
) -> MessageOut:
    """Revoke the refresh credential. The access JWT dies on its own soon after."""
    _require_auth_secret(request)
    await revoke_refresh_token(
        request.app.state.session_factory,
        raw_refresh_token=payload.refresh_token,
    )
    return MessageOut(message="Signed out.")
