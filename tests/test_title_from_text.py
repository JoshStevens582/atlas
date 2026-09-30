import pytest

from atlas.services.readers import title_from_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Leave types\n\nBody.", "Leave types"),
        ("\n\n  # Work schedules  \nBody.", "Work schedules"),
        ("No heading here.\n# Later heading", "fallback"),
        ("## Second level only\n", "fallback"),
        ("# \nBody.", "fallback"),
        ("#NoSpace\n", "fallback"),
        ("", "fallback"),
        ("   \n\n", "fallback"),
    ],
)
def test_title_from_text_reads_only_a_leading_first_level_heading(
    text: str, expected: str
) -> None:
    assert title_from_text(text, "fallback") == expected
