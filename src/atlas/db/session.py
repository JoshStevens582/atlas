from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.config import Settings
from atlas.db.models import Base


def create_engine(settings: Settings) -> AsyncEngine:
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(settings.database_url, echo=False)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _ensure_thread_owner_column(connection: Connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("chat_threads"):
        return
    column_names = {column["name"] for column in inspector.get_columns("chat_threads")}
    if "owner_id" in column_names:
        return
    connection.execute(
        text(
            "ALTER TABLE chat_threads "
            "ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT 'legacy'"
        )
    )


async def init_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_thread_owner_column)


async def session_iterator(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
