from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from redis.asyncio import Redis

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk

logger = logging.getLogger("atlas.answer_cache")


class AnswerCache:
    """Cache full Ask answers in Redis after retrieve (skip OpenAI on hit)."""

    def __init__(self, client: Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def build_key(
        self,
        *,
        question: str,
        sources: list[RetrievedChunk],
        history: list[tuple[str, str]],
        model: str,
        instructions: str,
    ) -> str:
        chunk_parts = [
            f"{chunk.document_id}:{chunk.chunk_index}:{chunk.text}" for chunk in sources
        ]
        history_parts = [f"{role}:{content}" for role, content in history]
        raw = "|".join(
            [
                model,
                instructions,
                question.strip(),
                "\n".join(chunk_parts),
                "\n".join(history_parts),
            ]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"atlas:answer:{digest}"

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("answer cache corrupt key=%s", key)
            return None
        if not isinstance(payload, dict):
            return None
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return None
        return payload

    async def set(self, key: str, *, answer: str, sources: list[RetrievedChunk]) -> None:
        ttl = self._settings.answer_cache_ttl_seconds
        if ttl <= 0:
            return
        payload = {
            "answer": answer,
            "sources": [chunk.model_dump() for chunk in sources],
        }
        await self._client.set(key, json.dumps(payload), ex=ttl)
        logger.info("answer cache store key=%s ttl=%s", key[:24], ttl)
