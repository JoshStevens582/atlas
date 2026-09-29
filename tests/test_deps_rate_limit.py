from typing import Any, cast

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from atlas.api.deps import _client_ip, enforce_login_rate_limit
from atlas.config import Settings
from atlas.services.rate_limit import RateLimiter, RateLimiterUnavailable, RateLimitExceeded


class ScriptedLimiter(RateLimiter):
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.subjects: list[str] = []

    async def hit(self, *, subject: str, bucket: str) -> None:
        self.subjects.append(subject)
        if self._error is not None:
            raise self._error


def _app(*, limiter: Any, fail_closed: bool) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(rate_limit_enabled=True, rate_limit_fail_closed=fail_closed)
    app.state.rate_limiter = limiter

    @app.post("/try", dependencies=[Depends(enforce_login_rate_limit)])
    async def try_it() -> dict[str, str]:
        return {"status": "let in"}

    return app


async def _post(app: FastAPI) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/try")


@pytest.mark.asyncio
async def test_no_redis_and_fail_closed_says_503() -> None:
    response = await _post(_app(limiter=None, fail_closed=True))

    assert response.status_code == 503
    assert "Rate limiting requires Redis" in response.json()["detail"]


@pytest.mark.asyncio
async def test_no_redis_and_fail_open_lets_the_request_through() -> None:
    response = await _post(_app(limiter=None, fail_closed=False))

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_redis_dropping_mid_request_and_fail_closed_says_503() -> None:
    limiter = ScriptedLimiter(error=RateLimiterUnavailable("gone"))

    response = await _post(_app(limiter=limiter, fail_closed=True))

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_redis_dropping_mid_request_and_fail_open_lets_the_request_through() -> None:
    limiter = ScriptedLimiter(error=RateLimiterUnavailable("gone"))

    response = await _post(_app(limiter=limiter, fail_closed=False))

    assert response.status_code == 200
    assert limiter.subjects == ["127.0.0.1"]


@pytest.mark.asyncio
async def test_going_over_the_limit_says_429_with_retry_after() -> None:
    limiter = ScriptedLimiter(error=RateLimitExceeded("Slow down.", retry_after_seconds=42))

    response = await _post(_app(limiter=limiter, fail_closed=True))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"


@pytest.mark.asyncio
async def test_a_limiter_of_the_wrong_type_fails_loudly() -> None:
    app = _app(limiter=object(), fail_closed=True)

    with pytest.raises(RuntimeError, match="Rate limiter is misconfigured"):
        await _post(app)


def test_client_ip_is_unknown_when_the_request_has_no_client() -> None:
    request = Request(cast(Any, {"type": "http", "headers": [], "client": None}))

    assert _client_ip(request) == "unknown"
