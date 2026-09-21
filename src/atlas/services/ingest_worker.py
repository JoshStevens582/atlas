"""Process one queued ingest job (shared by embedded worker and CLI)."""

from __future__ import annotations

import logging
from pathlib import Path

from atlas.services.ingest import IngestError, IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.readers import DocumentReadError

logger = logging.getLogger("atlas.ingest_worker")


async def process_next_ingest_job(
    queue: IngestQueue,
    ingest: IngestService,
    *,
    timeout_seconds: int = 2,
) -> bool:
    """
    Reserve and run at most one job.

    Returns True if a job was handled, False if the queue was idle.
    """
    payload = await queue.reserve(timeout_seconds=timeout_seconds)
    if payload is None:
        return False

    path = Path(payload.path)
    try:
        document = await ingest.ingest_path(path, title=payload.title)
        await queue.mark_done(
            payload.job_id,
            document_id=document.id,
            chunk_count=document.chunk_count,
            title=document.title,
            original_filename=document.original_filename,
        )
        logger.info(
            "ingest done job_id=%s document_id=%s chunks=%s",
            payload.job_id,
            document.id,
            document.chunk_count,
        )
    except (DocumentReadError, IngestError, OSError) as exc:
        await queue.mark_failed(
            payload.job_id,
            title=payload.title,
            original_filename=payload.original_filename,
            error=str(exc),
        )
        path.unlink(missing_ok=True)
        logger.warning("ingest failed job_id=%s error=%s", payload.job_id, exc)
    except Exception as exc:
        await queue.mark_failed(
            payload.job_id,
            title=payload.title,
            original_filename=payload.original_filename,
            error="Ingest failed unexpectedly.",
        )
        path.unlink(missing_ok=True)
        logger.exception("ingest crashed job_id=%s error=%s", payload.job_id, exc)
    return True
