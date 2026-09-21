"""Shared Redis connection.

Redis does three Atlas jobs (it never chunks or calls OpenAI):
1. Ingest queue — sticky notes for a worker ("index this file later")
2. Rate-limit counters
3. Ask answer cache

Upload: save file → write note here → return 202. Worker reads the note and runs ingest.
"""

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
