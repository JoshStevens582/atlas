from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.services import readers
from atlas.services.readers import DocumentReadError, extract_text, title_from_path


class FakePdfReader:
    """Stands in for pypdf.PdfReader; pages answer with the text you give."""

    page_texts: list[str | None] = []
    fail_to_open = False

    def __init__(self, _path: str) -> None:
        if FakePdfReader.fail_to_open:
            raise ValueError("not a pdf")
        self.pages = [
            SimpleNamespace(extract_text=lambda text=text: text) for text in self.page_texts
        ]


@pytest.fixture
def pdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    FakePdfReader.page_texts = []
    FakePdfReader.fail_to_open = False
    monkeypatch.setattr(readers, "PdfReader", FakePdfReader)
    path = tmp_path / "handbook.pdf"
    path.write_bytes(b"%PDF-fake")
    return path


def test_unsupported_file_type_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.docx"
    path.write_bytes(b"hello")

    with pytest.raises(DocumentReadError, match="Unsupported file type '.docx'"):
        extract_text(path)


def test_suffix_check_ignores_case(tmp_path: Path) -> None:
    path = tmp_path / "NOTES.MD"
    path.write_text("# Hello", encoding="utf-8")

    assert extract_text(path) == "# Hello"


def test_text_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"caf\xe9 \xff\xfe")

    with pytest.raises(DocumentReadError, match="not valid UTF-8"):
        extract_text(path)


def test_a_pdf_that_cannot_be_opened_is_refused(pdf: Path) -> None:
    FakePdfReader.fail_to_open = True

    with pytest.raises(DocumentReadError, match="Could not read that PDF"):
        extract_text(pdf)


def test_pdf_pages_are_trimmed_and_blank_pages_are_skipped(pdf: Path) -> None:
    FakePdfReader.page_texts = ["  first page  ", "   ", None, "second page"]

    assert extract_text(pdf) == "first page\n\nsecond page"


def test_a_pdf_with_no_text_is_refused(pdf: Path) -> None:
    FakePdfReader.page_texts = ["  ", None]

    with pytest.raises(DocumentReadError, match="no extractable text"):
        extract_text(pdf)


@pytest.mark.parametrize(
    ("filename", "title"),
    [
        ("00-demo-note.md", "00 Demo Note"),
        ("refund_policy.txt", "Refund Policy"),
        ("HANDBOOK.pdf", "Handbook"),
    ],
)
def test_title_comes_from_the_file_name(filename: str, title: str) -> None:
    assert title_from_path(Path(filename)) == title
