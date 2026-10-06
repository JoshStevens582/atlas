import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from atlas.schemas.auth import AuthUser
from atlas.services.auth import verify_access_token
from atlas.services.login_lockout import (
    AccountLocked,
    LoginLockout,
    LoginLockoutUnavailable,
)
from atlas.services.rate_limit import RateLimiter, RateLimiterUnavailable, RateLimitExceeded

logger = logging.getLogger("atlas.deps")

_bearer = HTTPBearer(auto_error=False)


def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = verify_access_token(request.app.state.settings, credentials.credentials)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthUser(username=username)


def _get_rate_limiter(request: Request) -> RateLimiter | None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return None
    if not isinstance(limiter, RateLimiter):
        raise RuntimeError("Rate limiter is misconfigured.")
    return limiter


async def enforce_ask_rate_limit(
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> AuthUser:
    await _enforce_bucket(request, subject=user.username, bucket="ask")
    return user


async def enforce_upload_rate_limit(
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> AuthUser:
    await _enforce_bucket(request, subject=user.username, bucket="upload")
    return user


async def enforce_login_rate_limit(request: Request) -> None:
    await _enforce_bucket(request, subject=_client_ip(request), bucket="login")


async def enforce_signup_rate_limit(request: Request) -> None:
    await _enforce_bucket(request, subject=_client_ip(request), bucket="signup")


async def enforce_demo_rate_limit(request: Request) -> None:
    await _enforce_bucket(request, subject=_client_ip(request), bucket="demo")


async def enforce_forgot_password_rate_limit(request: Request) -> None:
    await _enforce_bucket(request, subject=_client_ip(request), bucket="forgot_password")


def _get_login_lockout(request: Request) -> LoginLockout | None:
    lockout = getattr(request.app.state, "login_lockout", None)
    if lockout is None:
        return None
    if not isinstance(lockout, LoginLockout):
        raise RuntimeError("Login lockout is misconfigured.")
    return lockout


async def assert_login_not_locked(request: Request, username: str) -> None:
    settings = request.app.state.settings
    if not settings.login_lockout_enabled:
        return
    lockout = _get_login_lockout(request)
    if lockout is None:
        if settings.login_lockout_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Login lockout requires Redis. Start Redis or set "
                    "LOGIN_LOCKOUT_ENABLED=false for local use without lockout."
                ),
            )
        return
    try:
        await lockout.assert_not_locked(username)
    except AccountLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except LoginLockoutUnavailable as exc:
        logger.warning("login lockout unavailable mid-request")
        if settings.login_lockout_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Login lockout requires Redis. Start Redis or set "
                    "LOGIN_LOCKOUT_ENABLED=false for local use without lockout."
                ),
            ) from exc


async def record_login_outcome(
    request: Request, *, username: str, success: bool
) -> None:
    settings = request.app.state.settings
    if not settings.login_lockout_enabled:
        return
    lockout = _get_login_lockout(request)
    if lockout is None:
        if settings.login_lockout_fail_closed and not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Login lockout requires Redis. Start Redis or set "
                    "LOGIN_LOCKOUT_ENABLED=false for local use without lockout."
                ),
            )
        return
    try:
        if success:
            await lockout.record_success(username)
            return
        locked = await lockout.record_failure(username)
        if locked:
            duration = max(1, settings.login_lockout_duration_seconds)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Too many failed login attempts for this account. "
                    f"Try again in {duration} seconds."
                ),
                headers={"Retry-After": str(duration)},
            )
    except LoginLockoutUnavailable as exc:
        logger.warning("login lockout unavailable recording outcome")
        if settings.login_lockout_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Login lockout requires Redis. Start Redis or set "
                    "LOGIN_LOCKOUT_ENABLED=false for local use without lockout."
                ),
            ) from exc


async def clear_login_lockout(request: Request, username: str) -> None:
    """After a verified password reset, drop any lock state for that user."""
    settings = request.app.state.settings
    if not settings.login_lockout_enabled:
        return
    lockout = _get_login_lockout(request)
    if lockout is None:
        return
    try:
        await lockout.record_success(username)
    except LoginLockoutUnavailable:
        logger.warning("login lockout unavailable while clearing after reset")


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


async def _enforce_bucket(request: Request, *, subject: str, bucket: str) -> None:
    settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return
    limiter = _get_rate_limiter(request)
    if limiter is None:
        if settings.rate_limit_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Rate limiting requires Redis. Start Redis or set "
                    "RATE_LIMIT_ENABLED=false for local use without caps."
                ),
            )
        return
    try:
        await limiter.hit(subject=subject, bucket=bucket)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimiterUnavailable as exc:
        # Redis was up at startup but dropped mid-request. Match the same
        # fail-closed contract as "no Redis client at all" below, instead of
        # letting a raw RedisError bubble up as an uncaught 500.
        logger.warning("rate limiter unavailable mid-request bucket=%s", bucket)
        if settings.rate_limit_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Rate limiting requires Redis. Start Redis or set "
                    "RATE_LIMIT_ENABLED=false for local use without caps."
                ),
            ) from exc
