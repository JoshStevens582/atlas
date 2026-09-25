import re
from collections.abc import Sequence

from rank_bm25 import BM25Okapi

from atlas.schemas.chat import RetrievedChunk

_TOKEN = re.compile(r"[a-z0-9]+")
_DEFAULT_RRF_K = 60


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def bm25_rank(
    question: str,
    corpus: Sequence[RetrievedChunk],
    limit: int,
) -> list[RetrievedChunk]:
    """Rank chunks by keyword overlap (BM25). Exact ids and rare words win here."""
    if limit < 1 or not question.strip() or not corpus:
        return []
    query_tokens = tokenize(question)
    if not query_tokens:
        return []

    tokenized = [tokenize(chunk.text) for chunk in corpus]
    if not any(tokenized):
        return []

    scores = BM25Okapi(tokenized).get_scores(query_tokens)
    ranked = sorted(
        zip(corpus, scores, strict=True),
        key=lambda pair: float(pair[1]),
        reverse=True,
    )
    query_terms = set(query_tokens)
    hits: list[RetrievedChunk] = []
    for chunk, _score in ranked:
        if not query_terms.intersection(tokenize(chunk.text)):
            continue
        hits.append(chunk.model_copy(update={"match": "lexical"}))
        if len(hits) == limit:
            break
    return hits


def fuse_hybrid(
    vector_hits: Sequence[RetrievedChunk],
    lexical_hits: Sequence[RetrievedChunk],
    *,
    limit: int,
    rrf_k: int = _DEFAULT_RRF_K,
) -> list[RetrievedChunk]:
    """Reciprocal rank fusion: a chunk in both lists ranks above a one-list hit."""
    if limit < 1:
        return []
    if rrf_k < 1:
        rrf_k = _DEFAULT_RRF_K

    scores: dict[tuple[str, int], float] = {}
    chosen: dict[tuple[str, int], RetrievedChunk] = {}
    vector_keys = {
        (chunk.document_id, chunk.chunk_index) for chunk in vector_hits
    }
    lexical_keys = {
        (chunk.document_id, chunk.chunk_index) for chunk in lexical_hits
    }

    for ranked in (vector_hits, lexical_hits):
        for rank, chunk in enumerate(ranked, start=1):
            key = (chunk.document_id, chunk.chunk_index)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            existing = chosen.get(key)
            if existing is None or chunk.distance < existing.distance:
                chosen[key] = chunk

    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
    fused: list[RetrievedChunk] = []
    for key in ordered[:limit]:
        chunk = chosen[key]
        if key in vector_keys and key in lexical_keys:
            match = "both"
        elif key in lexical_keys:
            match = "lexical"
        else:
            match = "vector"
        fused.append(chunk.model_copy(update={"match": match}))
    return fused
