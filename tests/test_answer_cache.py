import pytest
from fakeredis.aioredis import FakeRedis

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk
from atlas.services.answer_cache import AnswerCache
from atlas.services.prompting import DEVELOPER_INSTRUCTIONS


def _chunk(text: str = "Northstar is the codename.") -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        document_title="Demo Note",
        chunk_index=0,
        text=text,
        distance=0.1,
    )


@pytest.mark.asyncio
async def test_answer_cache_round_trip() -> None:
    settings = Settings(answer_cache_ttl_seconds=60)
    redis = FakeRedis(decode_responses=True)
    cache = AnswerCache(redis, settings)
    sources = [_chunk()]
    key = cache.build_key(
        question="What is the project codename?",
        sources=sources,
        history=[],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    await cache.set(key, answer="Northstar.", sources=sources)
    hit = await cache.get(key)
    assert hit is not None
    assert hit["answer"] == "Northstar."
    await redis.aclose()


@pytest.mark.asyncio
async def test_answer_cache_key_changes_with_question_or_chunks() -> None:
    settings = Settings()
    redis = FakeRedis(decode_responses=True)
    cache = AnswerCache(redis, settings)
    sources = [_chunk()]
    key_a = cache.build_key(
        question="What is the codename?",
        sources=sources,
        history=[],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    key_b = cache.build_key(
        question="What are office hours?",
        sources=sources,
        history=[],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    key_c = cache.build_key(
        question="What is the codename?",
        sources=[_chunk("Different passage.")],
        history=[],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    assert key_a != key_b
    assert key_a != key_c
    await redis.aclose()


@pytest.mark.asyncio
async def test_answer_cache_key_includes_history() -> None:
    settings = Settings()
    redis = FakeRedis(decode_responses=True)
    cache = AnswerCache(redis, settings)
    sources = [_chunk()]
    key_empty = cache.build_key(
        question="Follow up?",
        sources=sources,
        history=[],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    key_with_history = cache.build_key(
        question="Follow up?",
        sources=sources,
        history=[("user", "What is the codename?"), ("assistant", "Northstar.")],
        model="gpt-4o-mini",
        instructions=DEVELOPER_INSTRUCTIONS,
    )
    assert key_empty != key_with_history
    await redis.aclose()
