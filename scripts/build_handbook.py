"""Build the Atlas library from the TTS Handbook (public domain / CC0).

Upstream: https://github.com/18F/handbook  (GSA Technology Transformation Services)
Licence:  work of the US Government plus a CC0 1.0 dedication, see upstream LICENSE.md.

The upstream pages are Eleventy templates: front matter, ``{% page "..." %}`` link
tags and markdown links whose URLs are noise for search. This script keeps the
text, drops that markup and writes one plain markdown file per page into
``handbook/``. Re-run it to rebuild the library from a fresh clone:

    git clone --depth 1 https://github.com/18F/handbook <clone>
    uv run python scripts/build_handbook.py <clone>
"""

import re
import sys
from pathlib import Path

PAGES = "pages"

# (upstream path under pages/, output file name)
SELECTED_PAGES: list[tuple[str, str]] = [
    ("travel-and-leave/leave.md", "leave.md"),
    ("travel-and-leave/overtime.md", "overtime-comp-time-and-credit-hours.md"),
    ("travel-and-leave/fmla.md", "fmla.md"),
    ("travel-and-leave/advanced-sick-leave.md", "advanced-sick-leave.md"),
    ("travel-and-leave/voluntary-leave-transfer-program.md", "voluntary-leave-transfer.md"),
    ("travel-and-leave/paid-parental-leave.md", "paid-parental-leave.md"),
    ("travel-and-leave/going-out-of-office.md", "going-out-of-office.md"),
    (
        "travel-and-leave/travel-and-leave-policies/travel-guide-2-book-travel.md",
        "travel-book-your-trip.md",
    ),
    (
        "travel-and-leave/travel-and-leave-policies/travel-guide-3-travel.md",
        "travel-on-the-road.md",
    ),
    (
        "travel-and-leave/travel-and-leave-policies/travel-guide-4-reimbursement.md",
        "travel-reimbursement.md",
    ),
    (
        "general-information-and-resources/employee-resources-policies/work-schedules.md",
        "work-schedules.md",
    ),
    (
        "general-information-and-resources/employee-resources-policies/transit-benefit.md",
        "transit-benefit.md",
    ),
    ("getting-started/index.md", "getting-started.md"),
    ("getting-started/equipment.md", "equipment.md"),
    ("hiring-staying-or-changing-jobs/leaving-tts.md", "leaving-tts.md"),
    ("hiring-staying-or-changing-jobs/promotions.md", "promotions.md"),
    ("hiring-staying-or-changing-jobs/term-extensions.md", "term-extensions.md"),
    ("about-us/code-of-conduct.md", "code-of-conduct.md"),
    (
        "general-information-and-resources/tech-policies/password-requirements.md",
        "password-requirements.md",
    ),
    (
        "general-information-and-resources/tech-policies/security-incidents.md",
        "security-incidents.md",
    ),
    ("tools/hrlinks.md", "hrlinks.md"),
]

_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_TITLE_LINE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_TEMPLATE_TAG = re.compile(r"\{%.*?%\}", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_LINK = re.compile(r"<a\s[^>]*>(.*?)</a>", re.DOTALL)
_HTML_DECORATION = re.compile(r"</?(?:img|br)\b[^>]*>")
_BLANK_RUN = re.compile(r"\n{3,}")


def page_title(front_matter: str, fallback: str) -> str:
    match = _TITLE_LINE.search(front_matter)
    if match is None:
        return fallback
    return match.group(1).strip("\"'")


def clean_page(raw: str, fallback_title: str) -> str:
    """Front matter becomes a ``# Title`` heading. Links keep their text only."""
    body = raw.replace("\r\n", "\n")
    title = fallback_title
    front = _FRONT_MATTER.match(body)
    if front is not None:
        title = page_title(front.group(1), fallback_title)
        body = body[front.end() :]
    body = _TEMPLATE_TAG.sub("", body)
    body = _IMAGE.sub("", body)
    body = _LINK.sub(r"\1", body)
    body = _HTML_LINK.sub(r"\1", body)
    body = _HTML_DECORATION.sub("", body)
    body = _BLANK_RUN.sub("\n\n", body).strip()
    return f"# {title}\n\n{body}\n"


def build(clone: Path, output_dir: Path) -> list[Path]:
    pages_dir = clone / PAGES
    if not pages_dir.is_dir():
        raise SystemExit(f"{pages_dir} not found. Pass the path to a clone of 18F/handbook.")
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for upstream, output_name in SELECTED_PAGES:
        source = pages_dir / upstream
        fallback = Path(output_name).stem.replace("-", " ").title()
        text = clean_page(source.read_text(encoding="utf-8"), fallback)
        destination = output_dir / output_name
        destination.write_text(text, encoding="utf-8", newline="\n")
        written.append(destination)
    return written


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_handbook.py <path to 18F/handbook clone>")
    written = build(Path(sys.argv[1]), Path("handbook"))
    total = sum(path.stat().st_size for path in written)
    print(f"Wrote {len(written)} pages ({total:,} bytes) to handbook/")


if __name__ == "__main__":
    main()
