"""Ingest job queue on Redis.

Redis only stores notes + status. It does not index files.
Upload enqueues here and returns 202; a worker later BRPOPs and runs ingest
(chunk → call embedding model → write Chroma/SQLite).
"""

from __future__ import annotations

import logging
from uuid import uuid4

from redis.asyncio import Redis

from atlas.config import Settings
from atlas.schemas.ingest_job import IngestJobOut, IngestJobPayload, IngestJobStatus

logger = logging.getLogger("atlas.ingest_queue")


class IngestQueueError(RuntimeError):
    """Raised when the ingest queue cannot store or load a job."""


class IngestQueue:
    """Redis list of 'index this file' notes + per-job status keys."""

    def __init__(self, client: Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _status_key(self, job_id: str) -> str:
        return f"atlas:ingest:job:{job_id}"

    async def enqueue(
        self,
        *,
        path: str,
        title: str,
        original_filename: str,
    ) -> IngestJobOut:
        job_id = str(uuid4())
        status = IngestJobOut(
            job_id=job_id,
            status=IngestJobStatus.pending,
            title=title,
            original_filename=original_filename,
        )
        payload = IngestJobPayload(
            job_id=job_id,
            path=path,
            title=title,
            original_filename=original_filename,
        )
        try:
            await self._client.set(
                self._status_key(job_id),
                status.model_dump_json(),
                ex=self._settings.ingest_job_ttl_seconds,
            )
            await self._client.lpush(
                self._settings.ingest_queue_key,
                payload.model_dump_json(),
            )
        except Exception as exc:
            raise IngestQueueError("Could not enqueue the ingest job.") from exc
        logger.info("enqueued ingest job_id=%s filename=%s", job_id, original_filename)
        return status

    async def get_job(self, job_id: str) -> IngestJobOut | None:
        raw = await self._client.get(self._status_key(job_id))
        if raw is None:
            return None
        return IngestJobOut.model_validate_json(raw)

    async def reserve(self, *, timeout_seconds: int = 2) -> IngestJobPayload | None:
        """Block-pop the next job and mark it running. None on idle timeout."""
        item = await self._client.brpop(
            self._settings.ingest_queue_key,
            timeout=timeout_seconds,
        )
        if item is None:
            return None
        _key, raw = item
        payload = IngestJobPayload.model_validate_json(raw)
        current = await self.get_job(payload.job_id)
        if current is None:
            current = IngestJobOut(
                job_id=payload.job_id,
                status=IngestJobStatus.pending,
                title=payload.title,
                original_filename=payload.original_filename,
            )
        await self._write_status(current.model_copy(update={"status": IngestJobStatus.running}))
        return payload

    async def mark_done(
        self,
        job_id: str,
        *,
        document_id: str,
        chunk_count: int,
        title: str,
        original_filename: str,
    ) -> IngestJobOut:
        status = IngestJobOut(
            job_id=job_id,
            status=IngestJobStatus.done,
            title=title,
            original_filename=original_filename,
            document_id=document_id,
            chunk_count=chunk_count,
        )
        await self._write_status(status)
        return status

    async def mark_failed(
        self,
        job_id: str,
        *,
        title: str,
        original_filename: str,
        error: str,
    ) -> IngestJobOut:
        status = IngestJobOut(
            job_id=job_id,
            status=IngestJobStatus.failed,
            title=title,
            original_filename=original_filename,
            error=error,
        )
        await self._write_status(status)
        return status

    async def _write_status(self, status: IngestJobOut) -> None:
        await self._client.set(
            self._status_key(status.job_id),
            status.model_dump_json(),
            ex=self._settings.ingest_job_ttl_seconds,
        )
