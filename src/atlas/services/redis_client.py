"""Shared Redis connection for ingest queue and rate limits."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

from atlas.config import Settings

logger = logging.getLogger("atlas.redis")


async def connect_redis(settings: Settings) -> Redis | None:
    """Ping Redis. Returns None when queue/rate limits should fall back."""
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s); sync ingest and no app rate limits.",
            exc,
        )
        await client.aclose()
        return None
    return client
