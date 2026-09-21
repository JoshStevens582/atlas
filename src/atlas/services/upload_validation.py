from pathlib import Path

from atlas.services.readers import SUPPORTED_SUFFIXES


class UploadValidationError(ValueError):
    """Raised when an uploaded file fails early checks before ingest."""


def safe_upload_filename(filename: str | None) -> str:
    """Keep only the final path segment so clients cannot smuggle directories."""
    raw = (filename or "upload.txt").strip() or "upload.txt"
    # Normalize Windows separators so Linux CI / servers strip `..\..\` paths too.
    return Path(raw.replace("\\", "/")).name


def validate_upload_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UploadValidationError("Use a .md, .txt, or .pdf file.")
    return suffix


def validate_upload_contents(contents: bytes, *, max_bytes: int) -> None:
    if max_bytes <= 0:
        raise UploadValidationError("Upload size limit is not configured.")
    if not contents:
        raise UploadValidationError("The uploaded file is empty.")
    if len(contents) > max_bytes:
        if max_bytes < 1024 * 1024:
            raise UploadValidationError(
                f"File is too large. Maximum size is {max_bytes} bytes."
            )
        limit_mb = max_bytes // (1024 * 1024)
        raise UploadValidationError(f"File is too large. Maximum size is {limit_mb} MB.")
