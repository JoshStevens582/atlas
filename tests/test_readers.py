from pathlib import Path

import pytest

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
