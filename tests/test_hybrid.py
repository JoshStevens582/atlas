from atlas.schemas.chat import RetrievedChunk
from atlas.services.hybrid import bm25_rank, fuse_hybrid, tokenize


def _chunk(
    document_id: str,
    text: str,
    *,
    distance: float = 0.2,
    chunk_index: int = 0,
    title: str = "Doc",
) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        document_title=title,
        chunk_index=chunk_index,
        text=text,
        distance=distance,
    )


def test_tokenize_lowercases_and_keeps_ids() -> None:
    assert tokenize("T-104 refund") == ["t", "104", "refund"]


def test_bm25_keeps_overlap_when_idf_is_zero() -> None:
    """Two-doc corpus: a term in one doc has BM25 IDF 0. Still a keyword hit."""
    left = _chunk("a", "alpha only")
    right = _chunk("b", "beta only")
    ranked = bm25_rank("beta", [left, right], 2)
    assert [chunk.document_id for chunk in ranked] == ["b"]


def test_bm25_ranks_exact_keyword_above_unrelated() -> None:
    northstar = _chunk("a", "The project codename is Northstar.")
    sku = _chunk("b", "Warranty serial ZX441NORTH is a SKU, not the project name.")
    ranked = bm25_rank("ZX441NORTH", [northstar, sku], 2)
    assert ranked
    assert ranked[0].document_id == "b"
    assert ranked[0].match == "lexical"


def test_bm25_returns_empty_when_query_has_no_tokens() -> None:
    assert bm25_rank("???", [_chunk("a", "Northstar")], 5) == []


def test_bm25_returns_empty_on_empty_corpus() -> None:
    assert bm25_rank("Northstar", [], 5) == []


def test_fusion_prefers_chunk_present_in_both_lists() -> None:
    both = _chunk("a", "Northstar", distance=0.3)
    vector_only = _chunk("b", "office hours", distance=0.1)
    lexical_only = _chunk("c", "ZX441NORTH", distance=1.0)
    fused = fuse_hybrid(
        [both, vector_only],
        [both, lexical_only],
        limit=3,
        rrf_k=60,
    )
    assert [chunk.document_id for chunk in fused] == ["a", "b", "c"]
    assert fused[0].match == "both"
    assert fused[1].match == "vector"
    assert fused[2].match == "lexical"


def test_fusion_keeps_closer_vector_distance_when_chunk_repeats() -> None:
    from_vector = _chunk("a", "Northstar", distance=0.15)
    from_bm25 = _chunk("a", "Northstar", distance=1.0)
    fused = fuse_hybrid([from_vector], [from_bm25], limit=1)
    assert fused[0].distance == 0.15
    assert fused[0].match == "both"


def test_fusion_respects_limit() -> None:
    hits = [_chunk(str(index), f"chunk {index}") for index in range(4)]
    fused = fuse_hybrid(hits, hits, limit=2)
    assert len(fused) == 2
