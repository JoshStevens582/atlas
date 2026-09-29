from typing import Any, cast

import pytest
from redis.asyncio import Redis

from atlas.config import Settings
from atlas.schemas.ingest_job import IngestJobPayload, IngestJobStatus
from atlas.services.ingest_queue import IngestQueue


class ScriptedRedis:
    """brpop returns whatever was queued up; status keys live in a dict."""

    def __init__(self, popped: tuple[str, str] | None) -> None:
        self._popped = popped
        self.values: dict[str, str] = {}

    async def brpop(self, _key: str, timeout: int) -> tuple[str, str] | None:
        return self._popped

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value


def _queue(redis: ScriptedRedis) -> IngestQueue:
    return IngestQueue(cast(Redis, redis), Settings())


@pytest.mark.asyncio
async def test_reserve_on_an_idle_queue_returns_none() -> None:
    assert await _queue(ScriptedRedis(popped=None)).reserve() is None


@pytest.mark.asyncio
async def test_reserve_recreates_the_status_when_the_status_key_expired() -> None:
    payload = IngestJobPayload(
        job_id="job-1", path="/tmp/handbook.md", title="Handbook", original_filename="handbook.md"
    )
    redis = ScriptedRedis(popped=("atlas:ingest", payload.model_dump_json()))
    queue = _queue(redis)

    reserved = await queue.reserve()

    assert reserved == payload
    status = await queue.get_job("job-1")
    assert status is not None
    assert status.status == IngestJobStatus.running
    assert (status.title, status.original_filename) == ("Handbook", "handbook.md")


@pytest.mark.asyncio
async def test_get_job_for_an_unknown_id_is_none() -> None:
    values: Any = ScriptedRedis(popped=None)

    assert await _queue(values).get_job("nope") is None
