from typing import Literal

from pydantic import BaseModel, Field

from atlas.db.models import IndexedDocument


class AnswerCheck(BaseModel):
    """A second model's verdict on whether an answer is backed by its sources."""

    verdict: Literal["supported", "partly_supported", "not_supported"]
    reason: str


class RetrievedChunk(BaseModel):
    document_id: str
    document_title: str
    chunk_index: int
    text: str
    distance: float
    match: str = "vector"
    cite_n: int = 0
    cited: bool | None = None


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, object]
    result: str


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ThreadOut(BaseModel):
    id: str
    title: str
    created_at: str


class ThreadDetailOut(ThreadOut):
    messages: list[ChatMessageOut]


class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)


class RetrievalCheckOut(BaseModel):
    """Upload-time self-test: ``hits`` of ``total`` questions found their own passage."""

    hits: int
    total: int


class DocumentOut(BaseModel):
    id: str
    title: str
    original_filename: str
    chunk_count: int
    created_at: str
    retrieval_check: RetrievalCheckOut | None = None

    @classmethod
    def from_document(cls, document: IndexedDocument) -> "DocumentOut":
        hits = document.retrieval_check_hits
        total = document.retrieval_check_total
        return cls(
            id=document.id,
            title=document.title,
            original_filename=document.original_filename,
            chunk_count=document.chunk_count,
            created_at=document.created_at.isoformat(),
            retrieval_check=(
                RetrievalCheckOut(hits=hits, total=total)
                if hits is not None and total is not None
                else None
            ),
        )


class HealthOut(BaseModel):
    status: str
    openai_configured: bool
    vector_store: str
    document_count: int
    chunk_count: int
