from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from atlas.config import Settings
from atlas.services.rate_limit import (
    RateLimiter,
    RateLimiterUnavailable,
    RateLimitExceeded,
    _limits_for_bucket,
)


class ScriptedRedis:
    """Counts up on incr; each command can be told to fail."""

    def __init__(self, *, fail_incr: bool = False, fail_ttl: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.incr_calls = 0
        self._fail_incr = fail_incr
        self._fail_ttl = fail_ttl

    async def incr(self, key: str) -> int:
        self.incr_calls += 1
        if self._fail_incr:
            raise RedisError("incr failed")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, _key: str, _seconds: int) -> None:
        return None

    async def ttl(self, _key: str) -> int:
        if self._fail_ttl:
            raise RedisError("ttl failed")
        return 30


def _limiter(redis: ScriptedRedis, **settings: Any) -> RateLimiter:
    return RateLimiter(cast(Redis, redis), Settings(**settings))


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "   "])
async def test_hit_refuses_a_blank_subject_without_touching_redis(subject: str) -> None:
    redis = ScriptedRedis()

    with pytest.raises(ValueError, match="subject is required"):
        await _limiter(redis).hit(subject=subject, bucket="ask")

    assert redis.incr_calls == 0


@pytest.mark.asyncio
async def test_hit_refuses_an_unknown_bucket() -> None:
    with pytest.raises(ValueError, match="Unknown rate-limit bucket 'chatty'"):
        await _limiter(ScriptedRedis()).hit(subject="alice", bucket="chatty")


def test_limits_for_bucket_refuses_an_unknown_bucket() -> None:
    with pytest.raises(ValueError, match="Unknown rate-limit bucket"):
        _limits_for_bucket(Settings(), "chatty")


@pytest.mark.asyncio
async def test_a_limit_of_zero_blocks_everything_and_never_asks_redis() -> None:
    redis = ScriptedRedis()

    with pytest.raises(RateLimitExceeded) as caught:
        await _limiter(redis, rate_limit_ask_per_minute=0).hit(subject="alice", bucket="ask")

    assert caught.value.retry_after_seconds >= 1
    assert redis.incr_calls == 0


@pytest.mark.asyncio
async def test_redis_failing_on_incr_becomes_rate_limiter_unavailable() -> None:
    with pytest.raises(RateLimiterUnavailable):
        await _limiter(ScriptedRedis(fail_incr=True)).hit(subject="alice", bucket="ask")


@pytest.mark.asyncio
async def test_redis_failing_while_reading_the_ttl_becomes_rate_limiter_unavailable() -> None:
    limiter = _limiter(ScriptedRedis(fail_ttl=True), rate_limit_ask_per_minute=1)
    await limiter.hit(subject="alice", bucket="ask")

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit(subject="alice", bucket="ask")


@pytest.mark.asyncio
async def test_going_over_the_limit_tells_the_caller_how_long_to_wait() -> None:
    limiter = _limiter(ScriptedRedis(), rate_limit_ask_per_minute=1)
    await limiter.hit(subject="alice", bucket="ask")

    with pytest.raises(RateLimitExceeded) as caught:
        await limiter.hit(subject="alice", bucket="ask")

    assert caught.value.retry_after_seconds == 30
    assert "per minute" in caught.value.detail
