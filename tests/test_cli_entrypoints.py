from pathlib import Path
from typing import Any

import pytest

from atlas import eval_cli, ingest_worker
from atlas.config import Settings
from atlas.services.redis_client import RedisRequiredError


@pytest.mark.asyncio
async def test_eval_cli_exits_1_and_explains_when_the_openai_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(eval_cli, "load_settings", lambda: Settings(openai_api_key=""))

    exit_code = await eval_cli._async_main(Path("questions.json"), Path("docs"))

    assert exit_code == 1
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_standalone_ingest_worker_refuses_to_start_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_redis(_settings: Settings) -> Any:
        return None

    monkeypatch.setattr(ingest_worker, "load_settings", lambda: Settings())
    monkeypatch.setattr(ingest_worker, "connect_redis", no_redis)

    with pytest.raises(SystemExit, match="Redis is required"):
        await ingest_worker._run()


@pytest.mark.asyncio
async def test_standalone_ingest_worker_exits_when_redis_is_required_and_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def redis_down(_settings: Settings) -> Any:
        raise RedisRequiredError(
            "Redis is required but did not answer the startup ping."
        )

    monkeypatch.setattr(ingest_worker, "load_settings", lambda: Settings(redis_required=True))
    monkeypatch.setattr(ingest_worker, "connect_redis", redis_down)

    with pytest.raises(SystemExit, match="did not answer the startup ping"):
        await ingest_worker._run()
