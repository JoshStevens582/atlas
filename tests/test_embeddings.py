from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from atlas.services.embeddings import EmbeddingClient


class StubEmbeddingsApi:
    """Records each request and answers with a vector that encodes the input."""

    def __init__(self, *, shuffle: bool = False, empty: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self._shuffle = shuffle
        self._empty = empty
        self.embeddings = self

    async def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        self.requests.append({"model": model, "input": input})
        if self._empty:
            return SimpleNamespace(data=[])
        items = [
            SimpleNamespace(index=position, embedding=[float(len(text)), float(position)])
            for position, text in enumerate(input)
        ]
        if self._shuffle:
            items.reverse()
        return SimpleNamespace(data=items)


def _client(api: StubEmbeddingsApi) -> EmbeddingClient:
    return EmbeddingClient(cast(AsyncOpenAI, api), "test-embedding-model")


@pytest.mark.asyncio
async def test_embed_texts_with_nothing_makes_no_request() -> None:
    api = StubEmbeddingsApi()

    assert await _client(api).embed_texts([]) == []
    assert api.requests == []


@pytest.mark.asyncio
async def test_embed_texts_sends_the_configured_model() -> None:
    api = StubEmbeddingsApi()

    await _client(api).embed_texts(["hello"])

    assert api.requests == [{"model": "test-embedding-model", "input": ["hello"]}]


@pytest.mark.asyncio
async def test_embed_texts_splits_into_batches_of_64_and_keeps_order() -> None:
    api = StubEmbeddingsApi()
    texts = ["x" * (number + 1) for number in range(130)]

    vectors = await _client(api).embed_texts(texts)

    assert [len(request["input"]) for request in api.requests] == [64, 64, 2]
    assert [vector[0] for vector in vectors] == [float(len(text)) for text in texts]


@pytest.mark.asyncio
async def test_embed_texts_puts_vectors_back_in_input_order_when_api_shuffles() -> None:
    api = StubEmbeddingsApi(shuffle=True)

    vectors = await _client(api).embed_texts(["a", "bb", "ccc"])

    assert [vector[0] for vector in vectors] == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_embed_query_returns_one_vector() -> None:
    api = StubEmbeddingsApi()

    assert await _client(api).embed_query("refund") == [6.0, 0.0]


@pytest.mark.asyncio
async def test_embed_query_raises_when_the_api_returns_no_vectors() -> None:
    api = StubEmbeddingsApi(empty=True)

    with pytest.raises(ValueError, match="no vectors"):
        await _client(api).embed_query("refund")
