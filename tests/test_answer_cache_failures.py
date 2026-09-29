import json
import logging
from typing import Any, cast

import pytest
from redis.asyncio import Redis

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk
from atlas.services.answer_cache import AnswerCache


class ScriptedRedis:
    def __init__(
        self,
        *,
        stored: str | None = None,
        fail_get: bool = False,
        fail_set: bool = False,
    ) -> None:
        self.stored = stored
        self.writes: list[tuple[str, str, int | None]] = []
        self._fail_get = fail_get
        self._fail_set = fail_set

    async def get(self, _key: str) -> str | None:
        if self._fail_get:
            raise ConnectionError("redis went away")
        return self.stored

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self._fail_set:
            raise ConnectionError("redis went away")
        self.writes.append((key, value, ex))


def _cache(redis: ScriptedRedis, **settings: Any) -> AnswerCache:
    return AnswerCache(cast(Redis, redis), Settings(**settings))


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        document_title="Handbook",
        chunk_index=0,
        text="Refunds take 14 days.",
        distance=0.1,
    )


@pytest.mark.asyncio
async def test_a_redis_error_on_get_is_a_cache_miss_not_a_failed_ask(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="atlas.answer_cache"):
        payload = await _cache(ScriptedRedis(fail_get=True)).get("atlas:answer:abc")

    assert payload is None
    assert "answer cache get failed" in caplog.text


@pytest.mark.asyncio
async def test_nothing_stored_is_a_miss() -> None:
    assert await _cache(ScriptedRedis(stored=None)).get("atlas:answer:abc") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        "{not json",
        json.dumps(["a", "list"]),
        json.dumps({"sources": []}),
        json.dumps({"answer": 42}),
        json.dumps({"answer": "   "}),
    ],
    ids=["corrupt-json", "not-an-object", "no-answer", "answer-not-text", "blank-answer"],
)
async def test_unusable_cached_values_are_misses(stored: str) -> None:
    assert await _cache(ScriptedRedis(stored=stored)).get("atlas:answer:abc") is None


@pytest.mark.asyncio
async def test_a_good_cached_value_comes_back() -> None:
    stored = json.dumps({"answer": "14 days [1]", "sources": []})

    payload = await _cache(ScriptedRedis(stored=stored)).get("atlas:answer:abc")

    assert payload is not None
    assert payload["answer"] == "14 days [1]"


@pytest.mark.asyncio
async def test_set_stores_the_answer_and_sources_with_the_configured_ttl() -> None:
    redis = ScriptedRedis()

    await _cache(redis, answer_cache_ttl_seconds=120).set(
        "atlas:answer:abc", answer="14 days [1]", sources=[_chunk()]
    )

    key, value, ttl = redis.writes[0]
    assert (key, ttl) == ("atlas:answer:abc", 120)
    stored = json.loads(value)
    assert stored["answer"] == "14 days [1]"
    assert stored["sources"][0]["document_id"] == "doc-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, -5])
async def test_set_does_nothing_when_the_ttl_is_zero_or_negative(ttl: int) -> None:
    redis = ScriptedRedis()

    await _cache(redis, answer_cache_ttl_seconds=ttl).set(
        "atlas:answer:abc", answer="x", sources=[]
    )

    assert redis.writes == []


@pytest.mark.asyncio
async def test_a_redis_error_on_set_does_not_fail_the_ask(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="atlas.answer_cache"):
        await _cache(ScriptedRedis(fail_set=True)).set("atlas:answer:abc", answer="x", sources=[])

    assert "answer cache set failed" in caplog.text
