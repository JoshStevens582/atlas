"""Refresh tokens: short access JWT + revocable refresh rows (step 17)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.db.models import User
from atlas.repositories.refresh_repo import RefreshRepository, normalize_expiry
from atlas.repositories.user_repo import UserRepository
from atlas.schemas.auth import UserRole
from atlas.services.auth import AuthError, issue_access_token, user_role

REFRESH_TOKEN_BYTES = 32


class RefreshError(AuthError):
    pass


@dataclass(frozen=True)
class SessionPair:
    access_token: str
    refresh_token: str
    username: str
    role: UserRole


def hash_refresh_token(raw: str) -> str:
    normalized = raw.strip()
    if not normalized:
        raise ValueError("Refresh token is empty.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _new_raw_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


async def issue_session_pair(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user: User,
    now: datetime | None = None,
) -> SessionPair:
    moment = now or datetime.now(UTC)
    raw = _new_raw_refresh_token()
    family_id = str(secrets.token_hex(16))
    expires_at = moment + timedelta(seconds=settings.refresh_token_ttl_seconds)
    async with session_factory() as session:
        await RefreshRepository(session).insert(
            user_id=user.id,
            token_hash=hash_refresh_token(raw),
            family_id=family_id,
            expires_at=expires_at,
        )
    return SessionPair(
        access_token=issue_access_token(settings, user.username, now=moment),
        refresh_token=raw,
        username=user.username,
        role=user_role(user),
    )


async def rotate_refresh_token(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    raw_refresh_token: str,
    now: datetime | None = None,
) -> SessionPair:
    moment = now or datetime.now(UTC)
    token_hash = hash_refresh_token(raw_refresh_token)
    async with session_factory() as session:
        repo = RefreshRepository(session)
        row = await repo.find_by_hash(token_hash)
        if row is None:
            raise RefreshError("Refresh token is not valid.")
        if row.revoked_at is not None:
            await repo.revoke_family(row.family_id, revoked_at=moment)
            raise RefreshError("Refresh token was revoked. Sign in again.")
        expires_at = normalize_expiry(row.expires_at)
        if expires_at <= moment:
            await repo.revoke(row.id, revoked_at=moment)
            raise RefreshError("Refresh token expired. Sign in again.")
        user = await UserRepository(session).get_by_id(row.user_id)
        if user is None or user.disabled_at is not None:
            await repo.revoke_family(row.family_id, revoked_at=moment)
            raise RefreshError("This account is not available.")
        await repo.revoke(row.id, revoked_at=moment)
        new_raw = _new_raw_refresh_token()
        new_expires = moment + timedelta(seconds=settings.refresh_token_ttl_seconds)
        await repo.insert(
            user_id=user.id,
            token_hash=hash_refresh_token(new_raw),
            family_id=row.family_id,
            expires_at=new_expires,
        )
    return SessionPair(
        access_token=issue_access_token(settings, user.username, now=moment),
        refresh_token=new_raw,
        username=user.username,
        role=user_role(user),
    )


async def revoke_refresh_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    raw_refresh_token: str,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    token_hash = hash_refresh_token(raw_refresh_token)
    async with session_factory() as session:
        repo = RefreshRepository(session)
        row = await repo.find_by_hash(token_hash)
        if row is None:
            return
        if row.revoked_at is None:
            await repo.revoke(row.id, revoked_at=moment)


async def revoke_all_refresh_sessions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        await RefreshRepository(session).revoke_all_for_user(user_id, revoked_at=moment)
