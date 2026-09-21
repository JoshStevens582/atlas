from pathlib import Path

import pytest
from pypdf import PdfWriter

from atlas.services.readers import DocumentReadError, extract_text, title_from_path


def test_reads_markdown(tmp_path: Path) -> None:
    path = tmp_path / "atlas-overview.md"
    path.write_text("Hello Atlas.\n", encoding="utf-8")
    assert extract_text(path) == "Hello Atlas.\n"
    assert title_from_path(path) == "Atlas Overview"


def test_rejects_unknown_suffix(tmp_path: Path) -> None:
    path = tmp_path / "notes.docx"
    path.write_bytes(b"not supported")
    with pytest.raises(DocumentReadError, match="Unsupported file type"):
        extract_text(path)


def test_rejects_invalid_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "broken.txt"
    path.write_bytes(b"hello\xff\xfe world")
    with pytest.raises(DocumentReadError, match="UTF-8"):
        extract_text(path)


def test_rejects_pdf_with_no_extractable_text(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(DocumentReadError, match="no extractable text"):
        extract_text(path)
