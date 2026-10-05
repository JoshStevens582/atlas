import logging
from typing import Any, cast

import pytest

from atlas.config import Settings
from atlas.services import redis_client
from atlas.services.redis_client import RedisRequiredError, connect_redis


class FakeRedis:
    """Stands in for redis.asyncio.Redis so no server is needed."""

    instances: list["FakeRedis"] = []

    def __init__(self, *, ping_error: Exception | None) -> None:
        self._ping_error = ping_error
        self.closed = False
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str, **_options: Any) -> "FakeRedis":
        return cls(ping_error=cls.next_ping_error)

    next_ping_error: Exception | None = None

    async def ping(self) -> bool:
        if self._ping_error is not None:
            raise self._ping_error
        return True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRedis.instances = []
    FakeRedis.next_ping_error = None
    monkeypatch.setattr(redis_client, "Redis", FakeRedis)


@pytest.mark.asyncio
async def test_connect_redis_returns_the_client_when_ping_works() -> None:
    client = await connect_redis(Settings())

    assert client is not None
    assert cast(object, client) is FakeRedis.instances[0]
    assert FakeRedis.instances[0].closed is False


@pytest.mark.asyncio
async def test_connect_redis_returns_none_closes_client_and_warns_when_ping_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    FakeRedis.next_ping_error = ConnectionError("connection refused")

    with caplog.at_level(logging.WARNING, logger="atlas.redis"):
        client = await connect_redis(Settings())

    assert client is None
    assert FakeRedis.instances[0].closed is True
    assert "Redis unavailable" in caplog.text
    assert "503" in caplog.text


@pytest.mark.asyncio
async def test_connect_redis_refuses_to_start_when_redis_is_required(
    caplog: pytest.LogCaptureFixture,
) -> None:
    FakeRedis.next_ping_error = ConnectionError("connection refused")

    with caplog.at_level(logging.ERROR, logger="atlas.redis"):
        with pytest.raises(RedisRequiredError, match="Redis is required"):
            await connect_redis(Settings(redis_required=True))

    assert FakeRedis.instances[0].closed is True
    assert "Redis required but unavailable" in caplog.text
    assert "connection refused" not in caplog.text
