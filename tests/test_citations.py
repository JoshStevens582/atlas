from atlas.schemas.chat import RetrievedChunk
from atlas.services.citations import (
    assign_cite_numbers,
    mark_cited_chunks,
    parse_citation_numbers,
)


def _chunk(document_id: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        document_title="Demo Note",
        chunk_index=0,
        text=text,
        distance=0.2,
    )


def test_assign_cite_numbers_is_one_based() -> None:
    numbered = assign_cite_numbers([_chunk("a", "Northstar"), _chunk("b", "hours")])
    assert [chunk.cite_n for chunk in numbered] == [1, 2]
    assert all(chunk.cited is None for chunk in numbered)


def test_parse_keeps_in_range_numbers_in_order() -> None:
    assert parse_citation_numbers("Northstar [2] and hours [1] [2].", 2) == [2, 1]


def test_parse_drops_fake_and_zero() -> None:
    assert parse_citation_numbers("See [0] [9] [1].", 2) == [1]


def test_parse_empty_answer_or_no_sources() -> None:
    assert parse_citation_numbers("Northstar [1]", 0) == []
    assert parse_citation_numbers("   ", 3) == []


def test_mark_cited_chunks_flags_numbers_in_the_answer() -> None:
    numbered = assign_cite_numbers([_chunk("a", "Northstar"), _chunk("b", "hours")])
    marked = mark_cited_chunks(numbered, "The project is Northstar [1].")
    assert marked[0].cited is True
    assert marked[1].cited is False
    assert [chunk.cite_n for chunk in marked] == [1, 2]


def test_mark_cited_chunks_numbers_unnumbered_chunks() -> None:
    marked = mark_cited_chunks([_chunk("a", "Northstar")], "Northstar [1].")
    assert marked[0].cite_n == 1
    assert marked[0].cited is True
