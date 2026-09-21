"""run_worker_loop must survive a Redis blip instead of dying silently.

Before this test, an unguarded ``queue.reserve()`` error (e.g. a Redis
ConnectionError mid-BRPOP) killed the embedded worker's asyncio.Task forever
after startup, or crashed the standalone CLI worker outright — uploads kept
returning 202 while nothing ever consumed the queued jobs again.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import run_worker_loop


@pytest.mark.asyncio
async def test_run_worker_loop_retries_after_reserve_error() -> None:
    queue = AsyncMock(spec=IngestQueue)
    queue.reserve = AsyncMock(
        side_effect=[ConnectionError("redis gone"), None]
    )
    ingest = AsyncMock(spec=IngestService)
    stop = asyncio.Event()

    async def _stop_after_second_reserve() -> None:
        # Let both reserve() calls happen, then stop the loop.
        while queue.reserve.await_count < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        run_worker_loop(
            queue,
            ingest,
            stop=stop,
            poll_timeout_seconds=1,
            initial_backoff_seconds=0.01,
            max_backoff_seconds=0.02,
        ),
        _stop_after_second_reserve(),
    )

    # The loop survived the ConnectionError and reserved again — it did not
    # die on the first error.
    assert queue.reserve.await_count >= 2


@pytest.mark.asyncio
async def test_run_worker_loop_stops_cleanly_when_idle() -> None:
    queue = AsyncMock(spec=IngestQueue)
    queue.reserve = AsyncMock(return_value=None)
    ingest = AsyncMock(spec=IngestService)
    stop = asyncio.Event()
    stop.set()

    # Should return immediately without raising when stop is already set.
    await run_worker_loop(queue, ingest, stop=stop, poll_timeout_seconds=1)
