"""Refresh session rows for step 17 (rotation and revocation)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import RefreshSession


class RefreshRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        *,
        user_id: str,
        token_hash: str,
        family_id: str,
        expires_at: datetime,
    ) -> RefreshSession:
        row = RefreshSession(
            id=str(uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def find_by_hash(self, token_hash: str) -> RefreshSession | None:
        result = await self._session.execute(
            select(RefreshSession).where(RefreshSession.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, session_id: str, *, revoked_at: datetime) -> None:
        await self._session.execute(
            update(RefreshSession)
            .where(RefreshSession.id == session_id)
            .values(revoked_at=revoked_at)
        )
        await self._session.commit()

    async def revoke_family(self, family_id: str, *, revoked_at: datetime) -> None:
        await self._session.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == family_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        await self._session.commit()

    async def revoke_all_for_user(self, user_id: str, *, revoked_at: datetime) -> None:
        await self._session.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        await self._session.commit()


def normalize_expiry(expires_at: datetime) -> datetime:
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=UTC)
    return expires_at
