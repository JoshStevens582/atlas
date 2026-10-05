from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import PasswordResetToken


class PasswordResetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_token_for_user(
        self,
        user_id: str,
        *,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
        )
        self._session.add(
            PasswordResetToken(
                id=str(uuid4()),
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        await self._session.commit()

    async def find_valid_token(
        self, token_hash: str, *, now: datetime
    ) -> PasswordResetToken | None:
        result = await self._session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            return None
        return row

    async def delete_token(self, token_id: str) -> None:
        await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.id == token_id)
        )
        await self._session.commit()
