import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.db.models import Base
from atlas.repositories.sql_repo import (
    ThreadAccessDeniedError,
    ThreadNotFoundError,
    ThreadRepository,
)


@pytest.mark.asyncio
async def test_threads_are_scoped_to_owner() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repo = ThreadRepository(session)
        alice_thread = await repo.create_thread("Alice chat", "alice")
        await repo.create_thread("Bob chat", "bob")

        alice_threads = await repo.list_threads("alice")
        assert [thread.title for thread in alice_threads] == ["Alice chat"]

        owned = await repo.get_owned_thread(alice_thread.id, "alice")
        assert owned.id == alice_thread.id

        with pytest.raises(ThreadAccessDeniedError):
            await repo.get_owned_thread(alice_thread.id, "bob")
        with pytest.raises(ThreadNotFoundError):
            await repo.get_owned_thread("missing-id", "alice")
    await engine.dispose()
