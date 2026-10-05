"""Shared Redis connection.

Redis does three Atlas jobs (it never chunks or calls OpenAI):
1. Ingest queue — sticky notes for a worker ("index this file later")
2. Rate-limit counters
3. Ask answer cache

Upload: save file → write note here → return 202. Worker reads the note and runs ingest.

Production sets ``REDIS_REQUIRED=true`` (see docker-compose). A failed startup
ping then stops the process. The laptop default leaves it false: this function
returns None, and Atlas starts with synchronous ingest, no answer cache, and
fail-closed rate limits.
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis

from atlas.config import Settings

logger = logging.getLogger("atlas.redis")


class RedisRequiredError(RuntimeError):
    """REDIS_REQUIRED is set and Redis did not answer the startup ping."""


async def connect_redis(settings: Settings) -> Redis | None:
    """Ping Redis.

    Returns the client when Redis answers. Returns None when it does not and
    Redis is optional. Raises ``RedisRequiredError`` when it does not and
    ``settings.redis_required`` is true.
    """
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        if settings.redis_required:
            logger.error(
                "Redis required but unavailable (%s).",
                type(exc).__name__,
            )
            raise RedisRequiredError(
                "Redis is required but did not answer the startup ping. "
                "Start Redis and check REDIS_URL."
            ) from exc
        logger.warning(
            "Redis unavailable (%s); starting without the ingest queue, "
            "answer cache, or rate-limit counters. Rate-limited routes "
            "return 503 when RATE_LIMIT_FAIL_CLOSED is true.",
            type(exc).__name__,
        )
        return None
    logger.info("Redis answered the startup ping.")
    return client
