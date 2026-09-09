from atlas.repositories.chroma_repo import _parse_query_results


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
