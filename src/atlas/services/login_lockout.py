from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from atlas.config import Settings

logger = logging.getLogger("atlas.login_lockout")


class AccountLocked(Exception):
    """Raised when too many failed logins have locked this username."""

    def __init__(self, detail: str, *, retry_after_seconds: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


class LoginLockoutUnavailable(Exception):
    """Redis dropped mid-request while login lockout is fail-closed."""


class LoginLockout:
    """Per-username failed-login counter and temporary lock in Redis."""

    def __init__(self, client: Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def assert_not_locked(self, username: str) -> None:
        key = _lock_key(username)
        try:
            ttl = await self._client.ttl(key)
        except RedisError as exc:
            logger.warning("login lockout Redis error on ttl key=%s: %s", key, exc)
            raise LoginLockoutUnavailable("Login lockout store is unreachable.") from exc
        if ttl is None or ttl < 0:
            return
        retry_after = max(1, int(ttl))
        raise AccountLocked(
            (
                "Too many failed login attempts for this account. "
                f"Try again in {retry_after} seconds."
            ),
            retry_after_seconds=retry_after,
        )

    async def record_success(self, username: str) -> None:
        failures_key = _failures_key(username)
        lock_key = _lock_key(username)
        try:
            await self._client.delete(failures_key, lock_key)
        except RedisError as exc:
            logger.warning(
                "login lockout Redis error clearing keys for username=%s: %s",
                _subject(username),
                exc,
            )
            raise LoginLockoutUnavailable("Login lockout store is unreachable.") from exc

    async def record_failure(self, username: str) -> bool:
        """
        Count one failed login. Returns True if the account is locked after this
        attempt (including when this attempt crossed the threshold).
        """
        failures_key = _failures_key(username)
        lock_key = _lock_key(username)
        window = max(1, self._settings.login_lockout_failure_window_seconds)
        duration = max(1, self._settings.login_lockout_duration_seconds)
        max_failures = max(1, self._settings.login_lockout_max_failures)

        try:
            count = int(await self._client.incr(failures_key))
            if count == 1:
                await self._client.expire(failures_key, window)
            if count >= max_failures:
                await self._client.set(lock_key, "1", ex=duration)
                await self._client.delete(failures_key)
                logger.info(
                    "login lockout engaged username=%s failures=%s",
                    _subject(username),
                    count,
                )
                return True
        except RedisError as exc:
            logger.warning(
                "login lockout Redis error recording failure username=%s: %s",
                _subject(username),
                exc,
            )
            raise LoginLockoutUnavailable("Login lockout store is unreachable.") from exc
        return False


def _subject(username: str) -> str:
    return username.strip()


def _failures_key(username: str) -> str:
    return f"atlas:login:failures:{_subject(username)}"


def _lock_key(username: str) -> str:
    return f"atlas:login:locked:{_subject(username)}"
