from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atlas.db.models import ChatMessage, ChatThread, IndexedDocument


class ThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_threads(self) -> list[ChatThread]:
        result = await self._session.execute(
            select(ChatThread).order_by(ChatThread.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_thread(self, thread_id: str) -> ChatThread | None:
        result = await self._session.execute(
            select(ChatThread)
            .options(selectinload(ChatThread.messages))
            .where(ChatThread.id == thread_id)
        )
        return result.scalar_one_or_none()

    async def create_thread(self, title: str) -> ChatThread:
        thread = ChatThread(id=str(uuid4()), title=title[:200])
        self._session.add(thread)
        await self._session.commit()
        await self._session.refresh(thread)
        return thread

    async def add_message(self, thread_id: str, role: str, content: str) -> ChatMessage:
        message = ChatMessage(
            id=str(uuid4()),
            thread_id=thread_id,
            role=role,
            content=content,
        )
        self._session.add(message)
        await self._session.commit()
        await self._session.refresh(message)
        return message

    async def last_messages(self, thread_id: str, limit: int) -> list[ChatMessage]:
        if limit < 1:
            return []
        result = await self._session.execute(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_documents(self) -> list[IndexedDocument]:
        result = await self._session.execute(
            select(IndexedDocument).order_by(IndexedDocument.created_at.desc())
        )
        return list(result.scalars().all())

    async def count_documents(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(IndexedDocument)
        )
        return int(result.scalar_one())

    async def get_document(self, document_id: str) -> IndexedDocument | None:
        result = await self._session.execute(
            select(IndexedDocument).where(IndexedDocument.id == document_id)
        )
        return result.scalar_one_or_none()

    async def create_document(
        self,
        title: str,
        original_filename: str,
        chunk_count: int,
        document_id: str | None = None,
    ) -> IndexedDocument:
        document = IndexedDocument(
            id=document_id or str(uuid4()),
            title=title,
            original_filename=original_filename,
            chunk_count=chunk_count,
        )
        self._session.add(document)
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def delete_document(self, document_id: str) -> bool:
        document = await self.get_document(document_id)
        if document is None:
            return False
        await self._session.delete(document)
        await self._session.commit()
        return True

    async def delete_all_documents(self) -> int:
        documents = await self.list_documents()
        count = len(documents)
        for document in documents:
            await self._session.delete(document)
        await self._session.commit()
        return count
