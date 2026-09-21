from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from atlas.config import Settings

logger = logging.getLogger("atlas.rate_limit")


class RateLimitExceeded(Exception):
    """Raised when a user exceeds an application rate limit."""

    def __init__(self, detail: str, *, retry_after_seconds: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    """Fixed-window per-user (and global) counters in Redis (INCR + EXPIRE)."""

    def __init__(self, client: Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def hit(self, *, username: str, bucket: str) -> None:
        """
        Record one hit against minute, daily, and (for ask) global daily caps.

        Raises RateLimitExceeded when any cap is exceeded.
        """
        if not username.strip():
            raise ValueError("username is required for rate limiting.")
        if bucket not in {"ask", "upload"}:
            raise ValueError(f"Unknown rate-limit bucket '{bucket}'.")

        minute_limit = (
            self._settings.rate_limit_ask_per_minute
            if bucket == "ask"
            else self._settings.rate_limit_upload_per_minute
        )
        daily_limit = (
            self._settings.rate_limit_ask_per_day
            if bucket == "ask"
            else self._settings.rate_limit_upload_per_day
        )

        await self._bump(
            key=f"atlas:ratelimit:{bucket}:min:{username}",
            limit=minute_limit,
            ttl_seconds=self._settings.rate_limit_window_seconds,
            label=f"{bucket} (per minute)",
        )
        await self._bump(
            key=f"atlas:ratelimit:{bucket}:day:{_utc_day()}:{username}",
            limit=daily_limit,
            ttl_seconds=_seconds_until_utc_midnight(),
            label=f"{bucket} (per day)",
        )
        if bucket == "ask":
            await self._bump(
                key=f"atlas:ratelimit:ask:day:{_utc_day()}:global",
                limit=self._settings.rate_limit_ask_global_per_day,
                ttl_seconds=_seconds_until_utc_midnight(),
                label="ask (app daily budget)",
            )

    async def _bump(
        self,
        *,
        key: str,
        limit: int,
        ttl_seconds: int,
        label: str,
    ) -> None:
        if limit <= 0:
            raise RateLimitExceeded(
                f"Rate limit blocked for {label}.",
                retry_after_seconds=max(1, ttl_seconds),
            )
        count = int(await self._client.incr(key))
        if count == 1:
            await self._client.expire(key, max(1, ttl_seconds))
        if count > limit:
            ttl = await self._client.ttl(key)
            retry_after = ttl_seconds if ttl is None or ttl < 0 else max(1, int(ttl))
            logger.info("rate limit exceeded key=%s count=%s limit=%s", key, count, limit)
            raise RateLimitExceeded(
                f"Rate limit exceeded for {label}. Try again in {retry_after} seconds.",
                retry_after_seconds=retry_after,
            )


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))
