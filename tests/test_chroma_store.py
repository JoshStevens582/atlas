from pathlib import Path
from typing import Any

import pytest

from atlas.repositories.chroma_repo import ChromaChunkStore, ExplicitEmbeddingFunction

NORTH = [1.0, 0.0, 0.0]
EAST = [0.0, 1.0, 0.0]
UP = [0.0, 0.0, 1.0]


@pytest.fixture
def store(tmp_path: Path) -> ChromaChunkStore:
    return ChromaChunkStore(str(tmp_path / "chroma"))


def _fill(store: ChromaChunkStore) -> None:
    store.upsert_chunks("doc-a", "Refunds", ["refund text", "shipping text"], [NORTH, EAST])
    store.upsert_chunks("doc-b", "Returns", ["returns text"], [UP])


def test_empty_store_has_nothing_to_count_query_or_list(store: ChromaChunkStore) -> None:
    assert store.count() == 0
    assert store.query(NORTH, 5) == []
    assert store.list_chunks() == []


def test_upsert_refuses_a_different_number_of_chunks_and_vectors(
    store: ChromaChunkStore,
) -> None:
    with pytest.raises(ValueError, match="exactly one embedding"):
        store.upsert_chunks("doc-a", "Refunds", ["one", "two"], [NORTH])

    assert store.count() == 0


def test_upsert_with_no_chunks_does_nothing(store: ChromaChunkStore) -> None:
    store.upsert_chunks("doc-a", "Refunds", [], [])

    assert store.count() == 0


def test_query_returns_the_closest_chunk_first_with_its_metadata(
    store: ChromaChunkStore,
) -> None:
    _fill(store)

    hits = store.query(NORTH, 3)

    assert [hit.text for hit in hits][0] == "refund text"
    assert (hits[0].document_id, hits[0].document_title, hits[0].chunk_index) == (
        "doc-a",
        "Refunds",
        0,
    )
    assert hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert hits[0].distance <= hits[1].distance <= hits[2].distance


def test_query_never_asks_for_more_than_exists(store: ChromaChunkStore) -> None:
    _fill(store)

    assert len(store.query(NORTH, 50)) == 3


@pytest.mark.parametrize("limit", [0, -1])
def test_query_with_a_limit_below_one_returns_nothing(store: ChromaChunkStore, limit: int) -> None:
    _fill(store)

    assert store.query(NORTH, limit) == []


def test_upserting_the_same_document_again_replaces_its_chunks(
    store: ChromaChunkStore,
) -> None:
    _fill(store)

    store.upsert_chunks("doc-a", "Refunds", ["rewritten"], [NORTH])

    texts = {chunk.text for chunk in store.list_chunks() if chunk.document_id == "doc-a"}
    assert "rewritten" in texts


def test_list_chunks_returns_everything_marked_as_keyword_candidates(
    store: ChromaChunkStore,
) -> None:
    _fill(store)

    chunks = store.list_chunks()

    assert {chunk.text for chunk in chunks} == {"refund text", "shipping text", "returns text"}
    assert {chunk.match for chunk in chunks} == {"lexical"}


def test_get_document_chunks_returns_one_document_in_reading_order(
    store: ChromaChunkStore,
) -> None:
    store.upsert_chunks("doc-a", "Refunds", ["first", "second", "third"], [NORTH, EAST, UP])
    store.upsert_chunks("doc-b", "Returns", ["other"], [UP])

    chunks = store.get_document_chunks("doc-a")

    assert [(chunk.chunk_index, chunk.text) for chunk in chunks] == [
        (0, "first"),
        (1, "second"),
        (2, "third"),
    ]
    assert {chunk.document_id for chunk in chunks} == {"doc-a"}
    assert store.get_document_chunks("missing") == []


def test_delete_document_removes_only_that_document(store: ChromaChunkStore) -> None:
    _fill(store)

    store.delete_document("doc-a")

    assert {chunk.document_id for chunk in store.list_chunks()} == {"doc-b"}


def test_reset_empties_the_store_and_it_stays_usable(store: ChromaChunkStore) -> None:
    _fill(store)

    store.reset()
    assert store.count() == 0

    store.upsert_chunks("doc-c", "New", ["fresh"], [NORTH])
    assert store.count() == 1


def test_data_survives_reopening_the_same_folder(tmp_path: Path) -> None:
    path = str(tmp_path / "chroma")
    first = ChromaChunkStore(path)
    first.upsert_chunks("doc-a", "Refunds", ["refund text"], [NORTH])

    assert ChromaChunkStore(path).count() == 1


def test_chroma_is_never_allowed_to_make_its_own_embeddings() -> None:
    function = ExplicitEmbeddingFunction()

    assert function.name() == "explicit-openai"
    with pytest.raises(RuntimeError, match="Pass embeddings explicitly"):
        function(["some text"])


class StubCollection:
    """A collection whose stored rows are a little broken."""

    def __init__(self, documents: list[Any], metadatas: list[Any]) -> None:
        self._documents = documents
        self._metadatas = metadatas

    def count(self) -> int:
        return len(self._documents)

    def get(self, include: list[str]) -> dict[str, Any]:
        return {"documents": self._documents, "metadatas": self._metadatas}


def test_list_chunks_skips_rows_missing_text_or_metadata_and_repairs_a_bad_index(
    store: ChromaChunkStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = StubCollection(
        documents=["kept", "", "no metadata", "odd index"],
        metadatas=[
            {"document_id": "d", "document_title": "T", "chunk_index": 3},
            {"document_id": "d"},
            None,
            {"document_id": "d", "chunk_index": "not a number"},
        ],
    )
    monkeypatch.setattr(store, "_collection", broken)

    chunks = store.list_chunks()

    assert [(chunk.text, chunk.chunk_index) for chunk in chunks] == [("kept", 3), ("odd index", 0)]
    assert chunks[1].document_title == "Untitled"
