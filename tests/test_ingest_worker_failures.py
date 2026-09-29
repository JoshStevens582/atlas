import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from atlas.schemas.ingest_job import IngestJobPayload
from atlas.services.ingest import IngestService
from atlas.services.ingest_queue import IngestQueue
from atlas.services.ingest_worker import process_next_ingest_job, run_worker_loop


class FakeQueue:
    def __init__(
        self, payload: IngestJobPayload | None, *, reserve_error: BaseException | None = None
    ) -> None:
        self._payload = payload
        self._reserve_error = reserve_error
        self.failures: list[dict[str, Any]] = []

    async def reserve(self, *, timeout_seconds: int = 2) -> IngestJobPayload | None:
        if self._reserve_error is not None:
            raise self._reserve_error
        return self._payload

    async def mark_failed(self, job_id: str, **details: Any) -> None:
        self.failures.append({"job_id": job_id, **details})


class ExplodingIngest:
    async def ingest_path(self, _path: Path, title: str | None = None) -> None:
        raise RuntimeError("database password is hunter2")


def _payload(path: Path) -> IngestJobPayload:
    return IngestJobPayload(
        job_id="job-1",
        path=str(path),
        title="Handbook",
        original_filename="handbook.md",
    )


@pytest.mark.asyncio
async def test_an_unexpected_crash_marks_the_job_failed_hides_the_details_and_deletes_the_file(
    tmp_path: Path,
) -> None:
    saved = tmp_path / "handbook.md"
    saved.write_text("Refunds take 14 days.", encoding="utf-8")
    queue = FakeQueue(_payload(saved))

    handled = await process_next_ingest_job(
        cast(IngestQueue, queue), cast(IngestService, ExplodingIngest())
    )

    assert handled is True
    assert queue.failures == [
        {
            "job_id": "job-1",
            "title": "Handbook",
            "original_filename": "handbook.md",
            "error": "Ingest failed unexpectedly.",
        }
    ]
    assert "hunter2" not in str(queue.failures)
    assert not saved.exists()


@pytest.mark.asyncio
async def test_an_idle_queue_reports_that_nothing_was_handled() -> None:
    handled = await process_next_ingest_job(
        cast(IngestQueue, FakeQueue(None)), cast(IngestService, ExplodingIngest())
    )

    assert handled is False


@pytest.mark.asyncio
async def test_cancelling_the_worker_while_it_waits_for_a_job_stops_it() -> None:
    queue = FakeQueue(None, reserve_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await run_worker_loop(
            cast(IngestQueue, queue),
            cast(IngestService, ExplodingIngest()),
            stop=asyncio.Event(),
        )


@pytest.mark.asyncio
async def test_cancelling_the_worker_while_it_backs_off_after_an_error_stops_it() -> None:
    queue = FakeQueue(None, reserve_error=ConnectionError("redis went away"))
    worker = asyncio.create_task(
        run_worker_loop(
            cast(IngestQueue, queue),
            cast(IngestService, ExplodingIngest()),
            stop=asyncio.Event(),
            initial_backoff_seconds=30,
        )
    )
    await asyncio.sleep(0.05)

    worker.cancel()

    with pytest.raises(asyncio.CancelledError):
        await worker
