"""Signup email verification — link in mail, hashed token in SQLite."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.db.models import User
from atlas.repositories.email_verification_repo import EmailVerificationRepository
from atlas.repositories.user_repo import UserRepository
from atlas.services.auth import AuthError, user_needs_email_verification
from atlas.services.password_reset import hash_reset_token
from atlas.services.smtp import send_plain_email

logger = logging.getLogger("atlas.email_verification")

RESEND_VERIFICATION_MESSAGE = (
    "If an account exists with a pending verification email, a new link was sent."
)


@dataclass(frozen=True)
class SignupVerificationResult:
    dev_verify_token: str | None


@dataclass(frozen=True)
class ResendVerificationResult:
    message: str
    dev_verify_token: str | None


def verification_link(settings: Settings, raw_token: str) -> str:
    base = settings.app_public_url.strip().rstrip("/")
    if not base:
        base = "http://localhost:5173"
    return f"{base}/?verify_token={raw_token}"


async def send_verification_email_for_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user: User,
) -> SignupVerificationResult:
    if not user.email or not user_needs_email_verification(user):
        return SignupVerificationResult(dev_verify_token=None)

    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_reset_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.email_verification_ttl_seconds
    )

    async with session_factory() as session:
        await EmailVerificationRepository(session).replace_token_for_user(
            user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    link = verification_link(settings, raw_token)
    sent = await send_plain_email(
        settings,
        to_address=user.email,
        subject="Verify your Atlas account",
        body=(
            "Confirm this email address to finish creating your Atlas account.\n\n"
            f"{link}\n\n"
            "If you did not sign up, you can ignore this email."
        ),
    )
    if not sent:
        logger.warning(
            "verification email not sent username=%s smtp_configured=%s",
            user.username,
            bool(settings.smtp_host),
        )

    dev_token: str | None = None
    if settings.email_verification_expose_token_in_response and not sent:
        dev_token = raw_token

    return SignupVerificationResult(dev_verify_token=dev_token)


async def verify_email_with_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    raw_token: str,
) -> str:
    token_hash = hash_reset_token(raw_token.strip())
    now = datetime.now(UTC)
    async with session_factory() as session:
        verify_repo = EmailVerificationRepository(session)
        row = await verify_repo.find_valid_token(token_hash, now=now)
        if row is None:
            raise AuthError("Invalid or expired verification token.")
        user = await session.get(User, row.user_id)
        if user is None:
            raise AuthError("Invalid or expired verification token.")
        user.email_verified_at = now
        await verify_repo.delete_token(row.id)
        await session.commit()
        return user.username


async def resend_verification_email(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
) -> ResendVerificationResult:
    username = username.strip()
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)

    if user is None or not user_needs_email_verification(user):
        return ResendVerificationResult(
            message=RESEND_VERIFICATION_MESSAGE,
            dev_verify_token=None,
        )

    result = await send_verification_email_for_user(
        session_factory,
        settings,
        user=user,
    )
    return ResendVerificationResult(
        message=RESEND_VERIFICATION_MESSAGE,
        dev_verify_token=result.dev_verify_token,
    )
