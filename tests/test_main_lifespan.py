import logging
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from atlas import main
from atlas.config import InsecureAuthSecretError, Settings
from atlas.db.models import User
from atlas.services.ingest_queue import IngestQueue
from atlas.services.rag import RagChatService
from atlas.services.rate_limit import RateLimiter

GOOD_AUTH_KEY = "lifespan-test-atlas-api-key-32-bytes-or-more"


class ClosableRedis:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'atlas.db').as_posix()}",
        "upload_dir": str(tmp_path / "uploads"),
        "chroma_path": str(tmp_path / "chroma"),
        "openai_api_key": "",
        "atlas_auth_secret": GOOD_AUTH_KEY,
        "atlas_demo_users": "alice:secret-a",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _run_inside_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # lifespan makes a relative ./data folder
    monkeypatch.chdir(tmp_path)


def _use(monkeypatch: pytest.MonkeyPatch, settings: Settings, redis: ClosableRedis | None) -> None:
    async def fake_connect_redis(_settings: Settings) -> Any:
        return redis

    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main, "connect_redis", fake_connect_redis)


async def _user_count() -> int:
    async with main.app.state.session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(User))).scalar_one())


@pytest.mark.asyncio
async def test_startup_without_redis_builds_the_app_and_skips_queue_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _use(monkeypatch, settings, redis=None)

    async with main.lifespan(main.app):
        state = main.app.state
        assert isinstance(state.rag_service, RagChatService)
        assert state.settings is settings
        assert state.ingest_queue is None
        assert state.rate_limiter is None
        assert state.redis_client is None
        assert (tmp_path / "uploads").is_dir()
        assert (tmp_path / "chroma").is_dir()
        assert (tmp_path / "atlas.db").is_file()
        assert await _user_count() == 1  # the alice demo user


@pytest.mark.asyncio
async def test_startup_with_redis_builds_queue_and_limiter_and_closes_redis_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = ClosableRedis()
    _use(monkeypatch, _settings(tmp_path), redis=redis)

    async with main.lifespan(main.app):
        assert isinstance(main.app.state.ingest_queue, IngestQueue)
        assert isinstance(main.app.state.rate_limiter, RateLimiter)
        assert redis.closed is False

    assert redis.closed is True


@pytest.mark.asyncio
async def test_startup_refuses_the_default_atlas_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _settings(tmp_path, atlas_auth_secret="dev-only-change-me"), redis=None)

    with pytest.raises(InsecureAuthSecretError):
        async with main.lifespan(main.app):
            pass

    assert not (tmp_path / "atlas.db").exists()


@pytest.mark.asyncio
async def test_startup_with_auth_turned_off_does_not_seed_demo_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _settings(tmp_path, atlas_auth_secret=""), redis=None)

    async with main.lifespan(main.app):
        assert await _user_count() == 0


def test_ask_logger_is_configured_once() -> None:
    ask_logger = logging.getLogger("atlas.ask")
    ask_logger.handlers.clear()

    main._configure_logging()
    main._configure_logging()

    assert len(ask_logger.handlers) == 1
    assert ask_logger.level == logging.INFO
    assert ask_logger.propagate is False
