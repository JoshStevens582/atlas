from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import chromadb
from chromadb.api.models.Collection import Collection

from atlas.schemas.chat import RetrievedChunk


class ExplicitEmbeddingFunction:
    """Placeholder so Chroma does not download a local ONNX model.

    Atlas always passes OpenAI vectors into ``add`` and ``query``.
    """

    def name(self) -> str:
        return "explicit-openai"

    def __call__(self, input: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("Pass embeddings explicitly from the OpenAI client.")


class ChromaChunkStore:
    def __init__(self, persist_path: str, collection_name: str = "atlas_chunks") -> None:
        Path(persist_path).mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name
        self._client = chromadb.PersistentClient(path=persist_path)
        self._collection: Collection = self._open_collection()

    def _open_collection(self) -> Collection:
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=cast(Any, ExplicitEmbeddingFunction()),
        )

    def count(self) -> int:
        return int(self._collection.count())

    def upsert_chunks(
        self,
        document_id: str,
        document_title: str,
        chunks: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Each chunk needs exactly one embedding vector.")
        if not chunks:
            return

        ids = [f"{document_id}:{index}" for index in range(len(chunks))]
        metadatas: list[Mapping[str, str | int]] = [
            {
                "document_id": document_id,
                "document_title": document_title,
                "chunk_index": index,
            }
            for index in range(len(chunks))
        ]
        self._collection.upsert(
            ids=ids,
            documents=list(chunks),
            embeddings=list(embeddings),
            metadatas=list(metadatas),
        )

    def query(self, embedding: Sequence[float], limit: int) -> list[RetrievedChunk]:
        if limit < 1:
            return []
        available = self.count()
        if available == 0:
            return []
        query_vectors: list[Sequence[float]] = [list(embedding)]
        results = self._collection.query(
            query_embeddings=query_vectors,
            n_results=min(limit, available),
            include=["documents", "metadatas", "distances"],
        )
        return _parse_query_results(results)

    def delete_document(self, document_id: str) -> None:
        self._collection.delete(where={"document_id": document_id})

    def reset(self) -> None:
        """Drop every chunk. Used when the corpus was duplicated by re-uploads."""
        self._client.delete_collection(self._collection_name)
        self._collection = self._open_collection()


def _parse_query_results(results: Mapping[str, Any]) -> list[RetrievedChunk]:
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    chunks: list[RetrievedChunk] = []
    for text, metadata, distance in zip(documents, metadatas, distances, strict=False):
        if not text or metadata is None:
            continue
        chunks.append(
            RetrievedChunk(
                document_id=str(metadata.get("document_id", "")),
                document_title=str(metadata.get("document_title", "Untitled")),
                chunk_index=int(metadata.get("chunk_index", 0)),
                text=str(text),
                distance=float(distance),
            )
        )
    return chunks
