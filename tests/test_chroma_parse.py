from pathlib import Path

from atlas.repositories.chroma_repo import ChromaChunkStore, _parse_query_results


def test_parse_skips_empty_documents() -> None:
    results = {
        "documents": [["useful passage", ""]],
        "metadatas": [
            [
                {
                    "document_id": "doc-1",
                    "document_title": "RAG",
                    "chunk_index": 2,
                },
                {"document_id": "doc-2", "document_title": "Skip", "chunk_index": 0},
            ]
        ],
        "distances": [[0.11, 0.22]],
    }
    chunks = _parse_query_results(results)
    assert len(chunks) == 1
    assert chunks[0].document_title == "RAG"
    assert chunks[0].chunk_index == 2
    assert chunks[0].distance == 0.11


def test_list_chunks_returns_upserted_text(tmp_path: Path) -> None:
    store = ChromaChunkStore(str(tmp_path / "chroma"))
    store.upsert_chunks("doc-1", "Handbook", ["Northstar is the codename."], [[1.0, 0.0]])
    chunks = store.list_chunks()
    assert len(chunks) == 1
    assert chunks[0].text == "Northstar is the codename."
    assert chunks[0].document_title == "Handbook"
    assert store.list_chunks()[0].document_id == "doc-1"


def test_list_chunks_empty_store(tmp_path: Path) -> None:
    store = ChromaChunkStore(str(tmp_path / "chroma"))
    assert store.list_chunks() == []
