from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import User

ROLE_USER = "user"
ROLE_ADMIN = "admin"
VALID_ROLES = frozenset({ROLE_USER, ROLE_ADMIN})


class UsernameTakenError(Exception):
    pass


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        if not normalized:
            return None
        result = await self._session.execute(select(User).where(User.email == normalized))
        return result.scalar_one_or_none()

    async def create(
        self,
        username: str,
        password_hash: str,
        *,
        email: str | None = None,
    ) -> User:
        normalized_email = email.strip().lower() if email and email.strip() else None
        user = User(
            id=str(uuid4()),
            username=username,
            password_hash=password_hash,
            email=normalized_email,
            role=ROLE_USER,
        )
        self._session.add(user)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # Two signups for the same username can race between the
            # get_by_username check and this insert; the unique index is
            # the real guard, this just turns the DB error into ours.
            await self._session.rollback()
            raise UsernameTakenError(f"Username '{username}' is already taken.") from exc
        await self._session.refresh(user)
        return user

    async def update_password_hash(self, user_id: str, password_hash: str) -> None:
        user = await self._session.get(User, user_id)
        if user is None:
            return
        user.password_hash = password_hash
        await self._session.commit()

    async def list_users(self) -> list[User]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.asc(), User.username.asc())
        )
        return list(result.scalars().all())

    async def count_admins(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(User).where(User.role == ROLE_ADMIN)
        )
        return int(result.scalar_one())

    async def set_role(self, user_id: str, role: str) -> User | None:
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        user.role = role
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def set_disabled_at(
        self, user_id: str, disabled_at: datetime | None
    ) -> User | None:
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        user.disabled_at = disabled_at
        await self._session.commit()
        await self._session.refresh(user)
        return user
