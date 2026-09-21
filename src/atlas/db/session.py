from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.config import Settings
from atlas.db.models import Base


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """WAL + a busy timeout so concurrent writers (ingest worker committing a
    document while a chat turn is being saved) retry internally instead of
    raising ``database is locked`` with no retry anywhere in the repo layer.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    is_sqlite = settings.database_url.startswith("sqlite")
    if is_sqlite:
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(settings.database_url, echo=False)
    if is_sqlite:
        event.listens_for(engine.sync_engine, "connect")(_set_sqlite_pragmas)
    return engine


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
