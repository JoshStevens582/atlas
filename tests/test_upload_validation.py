import pytest

from atlas.services.upload_validation import (
    UploadValidationError,
    safe_upload_filename,
    validate_upload_contents,
    validate_upload_suffix,
)


def test_safe_upload_filename_strips_directories() -> None:
    assert safe_upload_filename(r"..\..\secret.txt") == "secret.txt"
    assert safe_upload_filename(None) == "upload.txt"
    assert safe_upload_filename("  ") == "upload.txt"


def test_validate_upload_suffix_allows_supported_types() -> None:
    assert validate_upload_suffix("notes.md") == ".md"
    assert validate_upload_suffix("Notes.TXT") == ".txt"
    assert validate_upload_suffix("memo.PDF") == ".pdf"


def test_validate_upload_suffix_rejects_unsupported() -> None:
    with pytest.raises(UploadValidationError, match=r"\.md, \.txt, or \.pdf"):
        validate_upload_suffix("notes.docx")


def test_validate_upload_contents_rejects_empty() -> None:
    with pytest.raises(UploadValidationError, match="empty"):
        validate_upload_contents(b"", max_bytes=1024)


def test_validate_upload_contents_rejects_oversized() -> None:
    with pytest.raises(UploadValidationError, match="10 bytes"):
        validate_upload_contents(b"x" * 11, max_bytes=10)


def test_validate_upload_contents_reports_mb_limit() -> None:
    with pytest.raises(UploadValidationError, match="5 MB"):
        validate_upload_contents(b"x" * ((5 * 1024 * 1024) + 1), max_bytes=5 * 1024 * 1024)


def test_validate_upload_contents_accepts_within_limit() -> None:
    validate_upload_contents(b"ok", max_bytes=10)
