from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import User


class UsernameTakenError(Exception):
    pass


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create(self, username: str, password_hash: str) -> User:
        user = User(id=str(uuid4()), username=username, password_hash=password_hash)
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
