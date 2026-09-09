import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from atlas.db.models import Base
from atlas.repositories.sql_repo import DocumentRepository


@pytest.mark.asyncio
async def test_delete_all_documents_clears_rows() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        repo = DocumentRepository(session)
        await repo.create_document("A", "a.md", 1)
        await repo.create_document("B", "b.md", 2)
        assert await repo.count_documents() == 2
        removed = await repo.delete_all_documents()
        assert removed == 2
        assert await repo.count_documents() == 0
    await engine.dispose()
