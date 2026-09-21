"""Worker: read one Redis note and run ingest.

Redis holds the ticket. This Python chunks, calls the embedding model,
and writes Chroma/SQLite — then marks the job done/failed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from atlas.services.ingest import IngestError, IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.readers import DocumentReadError

logger = logging.getLogger("atlas.ingest_worker")

_DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0
_DEFAULT_MAX_BACKOFF_SECONDS = 30.0


async def process_next_ingest_job(
    queue: IngestQueue,
    ingest: IngestService,
    *,
    timeout_seconds: int = 2,
) -> bool:
    """
    Reserve and run at most one job.

    Returns True if a job was handled, False if the queue was idle.

    Note: ``queue.reserve()`` is intentionally left uncaught here. A Redis
    connection error while blocking on BRPOP should propagate to the caller
    (``run_worker_loop``) so it can back off and retry instead of being
    silently treated as "queue was idle".
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
        await asyncio.to_thread(path.unlink, missing_ok=True)
        logger.warning("ingest failed job_id=%s error=%s", payload.job_id, exc)
    except Exception as exc:
        await queue.mark_failed(
            payload.job_id,
            title=payload.title,
            original_filename=payload.original_filename,
            error="Ingest failed unexpectedly.",
        )
        await asyncio.to_thread(path.unlink, missing_ok=True)
        logger.exception("ingest crashed job_id=%s error=%s", payload.job_id, exc)
    return True


async def run_worker_loop(
    queue: IngestQueue,
    ingest: IngestService,
    *,
    stop: asyncio.Event,
    poll_timeout_seconds: int = 2,
    initial_backoff_seconds: float = _DEFAULT_INITIAL_BACKOFF_SECONDS,
    max_backoff_seconds: float = _DEFAULT_MAX_BACKOFF_SECONDS,
) -> None:
    """Poll Redis for ingest jobs until ``stop`` is set.

    Shared by the embedded worker (``main.py``) and the standalone CLI
    (``atlas.ingest_worker``). A Redis connection error (e.g. the container
    restarts) is caught here and retried with exponential backoff instead of
    letting the whole worker task/process die silently — without this, a
    transient Redis blip permanently stops ingest until the app is restarted,
    while uploads keep queuing jobs nobody will ever consume.
    """
    backoff = initial_backoff_seconds
    while not stop.is_set():
        try:
            await process_next_ingest_job(
                queue, ingest, timeout_seconds=poll_timeout_seconds
            )
            backoff = initial_backoff_seconds
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "ingest worker loop error; retrying in %.1fs: %s", backoff, exc
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, max_backoff_seconds)
