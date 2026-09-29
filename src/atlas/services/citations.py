import re

from atlas.schemas.chat import RetrievedChunk

_CITE = re.compile(r"\[(\d+)\]")


def assign_cite_numbers(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Give each retrieved chunk a 1-based [n] that matches <context>."""
    return [
        chunk.model_copy(update={"cite_n": index, "cited": None})
        for index, chunk in enumerate(chunks, start=1)
    ]


def parse_citation_numbers(answer: str, source_count: int) -> list[int]:
    """Numbers written as [1] in the answer. Fake or out-of-range numbers drop."""
    if source_count < 1 or not answer.strip():
        return []
    found: list[int] = []
    seen: set[int] = set()
    for match in _CITE.finditer(answer):
        number = int(match.group(1))
        if number < 1 or number > source_count or number in seen:
            continue
        seen.add(number)
        found.append(number)
    return found


def mark_cited_chunks(
    chunks: list[RetrievedChunk],
    answer: str,
) -> list[RetrievedChunk]:
    """Mark which retrieved chunks the answer actually cited."""
    cited = set(parse_citation_numbers(answer, len(chunks)))
    marked: list[RetrievedChunk] = []
    for index, chunk in enumerate(chunks, start=1):
        cite_n = chunk.cite_n if chunk.cite_n > 0 else index
        marked.append(
            chunk.model_copy(update={"cite_n": cite_n, "cited": cite_n in cited})
        )
    return marked
