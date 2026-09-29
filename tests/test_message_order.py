from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.db.models import Base
from atlas.repositories.sql_repo import ThreadRepository


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest.mark.asyncio
async def test_messages_saved_back_to_back_keep_the_order_they_were_written(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """SQLite's CURRENT_TIMESTAMP is one second wide, so these used to tie."""
    async with factory() as session:
        repo = ThreadRepository(session)
        thread = await repo.create_thread("Order", "alice")
        written = [f"message {number}" for number in range(12)]
        for number, content in enumerate(written):
            await repo.add_message(thread.id, "user" if number % 2 == 0 else "assistant", content)

        recent = await repo.last_messages(thread.id, 12)

    assert [message.content for message in recent] == written


@pytest.mark.asyncio
async def test_last_messages_returns_the_newest_ones_still_in_order(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        repo = ThreadRepository(session)
        thread = await repo.create_thread("Order", "alice")
        for number in range(6):
            await repo.add_message(thread.id, "user", f"message {number}")

        recent = await repo.last_messages(thread.id, 3)
        none_wanted = await repo.last_messages(thread.id, 0)

    assert [message.content for message in recent] == ["message 3", "message 4", "message 5"]
    assert none_wanted == []


@pytest.mark.asyncio
async def test_threads_created_back_to_back_list_newest_first(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        repo = ThreadRepository(session)
        for number in range(5):
            await repo.create_thread(f"thread {number}", "alice")

        listed = await repo.list_threads("alice")

    assert [thread.title for thread in listed] == [f"thread {number}" for number in (4, 3, 2, 1, 0)]
