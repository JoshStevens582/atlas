from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from atlas.config import Settings
from atlas.db.session import (
    _ensure_document_check_columns,
    _ensure_thread_owner_column,
    create_engine,
    create_session_factory,
    init_database,
    session_iterator,
)


def _settings(database_file: Path) -> Settings:
    return Settings(database_url=f"sqlite+aiosqlite:///{database_file.as_posix()}")


@pytest.mark.asyncio
async def test_create_engine_makes_the_database_folder_and_sets_sqlite_pragmas(
    tmp_path: Path,
) -> None:
    database_file = tmp_path / "nested" / "folder" / "atlas.db"
    engine = create_engine(_settings(database_file))
    try:
        async with engine.connect() as connection:
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one()
    finally:
        await engine.dispose()

    assert database_file.parent.is_dir()
    assert journal_mode == "wal"
    assert busy_timeout == 5000


@pytest.mark.asyncio
async def test_init_database_creates_tables_and_can_run_twice(tmp_path: Path) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        await init_database(engine)
        await init_database(engine)
        async with engine.connect() as connection:
            tables = {
                row[0]
                for row in await connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
    finally:
        await engine.dispose()

    assert {"chat_threads", "chat_messages", "users", "indexed_documents"} <= tables


@pytest.mark.asyncio
async def test_init_database_adds_owner_column_to_an_old_threads_table(tmp_path: Path) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE chat_threads ("
                    "id VARCHAR(36) PRIMARY KEY, title VARCHAR(200), created_at DATETIME)"
                )
            )
            await connection.execute(
                text("INSERT INTO chat_threads (id, title) VALUES ('old-thread', 'Old')")
            )

        await init_database(engine)

        async with engine.connect() as connection:
            owner = (
                await connection.execute(
                    text("SELECT owner_id FROM chat_threads WHERE id = 'old-thread'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert owner == "legacy"


@pytest.mark.asyncio
async def test_init_database_adds_self_test_columns_to_an_old_documents_table(
    tmp_path: Path,
) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE indexed_documents ("
                    "id VARCHAR(36) PRIMARY KEY, title VARCHAR(300), "
                    "original_filename VARCHAR(300), chunk_count INTEGER, created_at DATETIME)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO indexed_documents (id, title, original_filename, chunk_count) "
                    "VALUES ('old-doc', 'Old', 'old.md', 3)"
                )
            )

        await init_database(engine)
        await init_database(engine)

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT retrieval_check_hits, retrieval_check_total "
                        "FROM indexed_documents WHERE id = 'old-doc'"
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert tuple(row) == (None, None)


@pytest.mark.asyncio
async def test_document_columns_check_does_nothing_when_there_is_no_documents_table(
    tmp_path: Path,
) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_ensure_document_check_columns)
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await engine.dispose()

    assert tables == []


@pytest.mark.asyncio
async def test_owner_column_check_does_nothing_when_there_is_no_threads_table(
    tmp_path: Path,
) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_ensure_thread_owner_column)
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await engine.dispose()

    assert tables == []


@pytest.mark.asyncio
async def test_session_iterator_yields_a_working_session(tmp_path: Path) -> None:
    engine = create_engine(_settings(tmp_path / "atlas.db"))
    try:
        factory = create_session_factory(engine)
        answers = [
            (await session.execute(text("SELECT 1"))).scalar_one()
            async for session in session_iterator(factory)
        ]
    finally:
        await engine.dispose()

    assert answers == [1]
