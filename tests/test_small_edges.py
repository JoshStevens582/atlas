"""One-line edge cases: empty input, zero limits, and setup that is missing."""

import pytest

from atlas.schemas.chat import RetrievedChunk
from atlas.schemas.eval import GoldenQuestion
from atlas.services.hybrid import bm25_rank, fuse_hybrid
from atlas.services.prompting import keep_close_chunks
from atlas.services.rag_eval import score_case
from atlas.services.upload_validation import UploadValidationError, validate_upload_contents


def _chunk(text: str, *, index: int = 0, distance: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        document_title="Handbook",
        chunk_index=index,
        text=text,
        distance=distance,
    )


def test_keyword_search_of_chunks_that_have_no_words_finds_nothing() -> None:
    assert bm25_rank("refund", [_chunk("!!! ???"), _chunk("...")], limit=5) == []


def test_fusing_with_a_limit_below_one_returns_nothing() -> None:
    assert fuse_hybrid([_chunk("a")], [_chunk("b", index=1)], limit=0) == []


def test_a_nonsense_rrf_constant_falls_back_to_the_default() -> None:
    vector_hits = [_chunk("a", index=0), _chunk("b", index=1)]

    assert [c.text for c in fuse_hybrid(vector_hits, [], limit=2, rrf_k=0)] == ["a", "b"]


def test_no_chunks_means_no_close_chunks() -> None:
    assert keep_close_chunks([], max_distance=0.4) == []


def test_when_every_chunk_is_too_far_the_nearest_one_is_kept() -> None:
    far = [_chunk("far", index=0, distance=0.9), _chunk("nearer", index=1, distance=0.7)]

    assert [c.text for c in keep_close_chunks(far, max_distance=0.4)] == ["nearer"]


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_upload_limit_that_is_not_configured_is_refused(max_bytes: int) -> None:
    with pytest.raises(UploadValidationError, match="not configured"):
        validate_upload_contents(b"hello", max_bytes=max_bytes)


def test_an_eval_answer_without_the_forbidden_phrase_passes() -> None:
    question = GoldenQuestion(
        id="q1",
        question="How long do refunds take?",
        expected_answer="14 days",
        must_contain=["14 days"],
        must_not_contain=["30 days"],
        chunk_contains=["Refunds"],
    )

    score = score_case(question, [_chunk("Refunds take 14 days.")], "Refunds take 14 days.")

    assert score.generation_pass is True
    assert score.case_pass is True


def test_an_eval_answer_with_the_forbidden_phrase_fails() -> None:
    question = GoldenQuestion(
        id="q1",
        question="How long do refunds take?",
        expected_answer="14 days",
        must_contain=["14 days"],
        must_not_contain=["30 days"],
        chunk_contains=["Refunds"],
    )

    score = score_case(question, [_chunk("Refunds take 14 days.")], "14 days, or 30 days.")

    assert score.generation_pass is False
    assert any("forbidden" in note for note in score.notes)
