import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from atlas.schemas.auth import AuthUser
from atlas.services.auth import verify_access_token
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
    await _enforce_bucket(request, user, bucket="ask")
    return user


async def enforce_upload_rate_limit(
    request: Request,
    user: Annotated[AuthUser, Depends(require_user)],
) -> AuthUser:
    await _enforce_bucket(request, user, bucket="upload")
    return user


async def _enforce_bucket(request: Request, user: AuthUser, *, bucket: str) -> None:
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
        await limiter.hit(username=user.username, bucket=bucket)
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
