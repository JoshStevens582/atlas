from pathlib import Path

from pypdf import PdfReader


class DocumentReadError(ValueError):
    """Raised when a file cannot be turned into text."""


SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentReadError(
            f"Unsupported file type '{suffix}'. Use .md, .txt, or .pdf."
        )
    if suffix == ".pdf":
        return _extract_pdf(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentReadError("The file is not valid UTF-8 text.") from exc


def title_from_path(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def _extract_pdf(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise DocumentReadError("Could not read that PDF.") from exc

    pages: list[str] = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        if extracted.strip():
            pages.append(extracted.strip())
    text = "\n\n".join(pages)
    if not text.strip():
        raise DocumentReadError("The PDF had no extractable text.")
    return text
