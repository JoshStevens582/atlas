import pytest

from atlas.services.chunking import ChunkingError, chunk_document, chunk_text


def test_refund_sentence_with_overlap() -> None:
    text = "Return shoes within 30 days."
    chunks = chunk_text(text, chunk_size=10, overlap=3)
    assert chunks == ["Return sho", "shoes with", "ithin 30 d", "0 days."]


def test_alphabet_window() -> None:
    chunks = chunk_text("ABCDEFGHIJ", chunk_size=4, overlap=1)
    assert chunks == ["ABCD", "DEFG", "GHIJ"]


def test_empty_and_whitespace() -> None:
    assert chunk_text("   ", 10, 2) == []
    assert chunk_document("\n\n", 10, 2) == []


def test_rejects_overlap_that_cannot_advance() -> None:
    with pytest.raises(ChunkingError, match="bigger than overlap"):
        chunk_text("hello world", chunk_size=10, overlap=10)


def test_rejects_invalid_sizes() -> None:
    with pytest.raises(ChunkingError):
        chunk_text("hello", chunk_size=0, overlap=0)
    with pytest.raises(ChunkingError):
        chunk_text("hello", chunk_size=8, overlap=-1)


def test_paragraph_packing_keeps_short_sections_together() -> None:
    text = "Alpha paragraph.\n\nBeta paragraph."
    chunks = chunk_document(text, chunk_size=80, overlap=10)
    assert chunks == ["Alpha paragraph.\n\nBeta paragraph."]


def test_long_paragraph_falls_back_to_window() -> None:
    paragraph = "x" * 25
    chunks = chunk_document(paragraph, chunk_size=10, overlap=2)
    assert chunks == chunk_text(paragraph, chunk_size=10, overlap=2)
