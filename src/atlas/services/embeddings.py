from openai import AsyncOpenAI


class EmbeddingClient:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        batch_size = 64
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        return vectors

    async def embed_query(self, query: str) -> list[float]:
        embeddings = await self.embed_texts([query])
        if not embeddings:
            raise ValueError("Embedding API returned no vectors for the query.")
        return embeddings[0]
