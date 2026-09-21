from enum import StrEnum

from pydantic import BaseModel, Field


class IngestJobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class IngestJobOut(BaseModel):
    job_id: str
    status: IngestJobStatus
    title: str
    original_filename: str
    document_id: str | None = None
    chunk_count: int | None = None
    error: str | None = None


class IngestJobPayload(BaseModel):
    """Queued work item stored on the Redis list."""

    job_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
