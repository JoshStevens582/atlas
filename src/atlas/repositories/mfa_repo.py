"""Authenticator secret and one-time recovery codes on the user row."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import delete, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import MfaRecoveryCode, User


def _one_row_changed(result: object) -> bool:
    return cast(CursorResult[Any], result).rowcount == 1


class MfaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_pending_secret(self, user: User, encrypted_secret: str) -> None:
        """Store a secret that is not active until a matching code confirms it."""
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
        )
        user.mfa_secret = encrypted_secret
        user.mfa_enabled_at = None
        user.mfa_last_step = None
        await self._session.commit()

    async def confirm(
        self,
        user: User,
        *,
        enabled_at: datetime,
        last_step: int,
        code_hashes: list[str],
    ) -> bool:
        """Turn MFA on and store recovery-code hashes in one commit.

        Returns False when another request already turned it on.
        """
        result = await self._session.execute(
            update(User)
            .where(User.id == user.id, User.mfa_enabled_at.is_(None))
            .values(mfa_enabled_at=enabled_at, mfa_last_step=last_step)
            .execution_options(synchronize_session=False)
        )
        if not _one_row_changed(result):
            await self._session.rollback()
            return False
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
        )
        for code_hash in code_hashes:
            self._session.add(
                MfaRecoveryCode(
                    id=str(uuid4()),
                    user_id=user.id,
                    code_hash=code_hash,
                )
            )
        user.mfa_enabled_at = enabled_at
        user.mfa_last_step = last_step
        await self._session.commit()
        return True

    async def clear(self, user: User) -> None:
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
        )
        user.mfa_secret = None
        user.mfa_enabled_at = None
        user.mfa_last_step = None
        await self._session.commit()

    async def claim_totp_step(self, user_id: str, step: int) -> bool:
        """Record this time-step only if a newer code has not already been used."""
        result = await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .where(or_(User.mfa_last_step.is_(None), User.mfa_last_step < step))
            .values(mfa_last_step=step)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return _one_row_changed(result)

    async def replace_recovery_codes(self, user_id: str, code_hashes: list[str]) -> None:
        """Drop unused recovery hashes and store a fresh set (regenerate flow)."""
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        for code_hash in code_hashes:
            self._session.add(
                MfaRecoveryCode(
                    id=str(uuid4()),
                    user_id=user_id,
                    code_hash=code_hash,
                )
            )
        await self._session.commit()

    async def consume_recovery_code(
        self,
        user_id: str,
        code_hash: str,
        *,
        used_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.code_hash == code_hash,
                MfaRecoveryCode.used_at.is_(None),
            )
            .values(used_at=used_at)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return _one_row_changed(result)
