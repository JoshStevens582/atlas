from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    document_id: str
    document_title: str
    chunk_index: int
    text: str
    distance: float


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


class DocumentOut(BaseModel):
    id: str
    title: str
    original_filename: str
    chunk_count: int
    created_at: str


class HealthOut(BaseModel):
    status: str
    openai_configured: bool
    vector_store: str
    document_count: int
    chunk_count: int
