from collections.abc import Sequence
from pathlib import Path

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk
from atlas.schemas.eval import CaseScore, EvalReport, GoldenQuestion, GoldenQuestionSet
from atlas.services.citations import parse_citation_numbers
from atlas.services.rag import RagChatService
from atlas.services.readers import SUPPORTED_SUFFIXES

UNKNOWN_MARKERS = (
    "do not know",
    "don't know",
    "does not know",
    "doesn't know",
    "not in the indexed",
    "no information in the indexed",
    "cannot find that in the indexed",
)

EVAL_DATABASE_URL = "sqlite+aiosqlite:///./data/eval/atlas.db"
EVAL_CHROMA_PATH = "./data/eval/chroma"
EVAL_UPLOAD_DIR = "./data/eval/uploads"
EVAL_FIXTURES_DIR = Path("evals/fixtures")


def load_golden_questions(path: Path) -> list[GoldenQuestion]:
    if not path.is_file():
        raise FileNotFoundError(f"Golden question file is missing: {path}")
    payload = GoldenQuestionSet.model_validate_json(path.read_text(encoding="utf-8"))
    question_ids = [item.id for item in payload.questions]
    duplicates = _duplicate_ids(question_ids)
    if duplicates:
        joined = ", ".join(duplicates)
        raise ValueError(f"Golden question ids must be unique. Duplicates: {joined}")
    return payload.questions


def isolated_eval_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "database_url": EVAL_DATABASE_URL,
            "chroma_path": EVAL_CHROMA_PATH,
            "upload_dir": EVAL_UPLOAD_DIR,
        }
    )


async def run_eval_suite(
    rag: RagChatService,
    questions: Sequence[GoldenQuestion],
) -> list[CaseScore]:
    cases: list[CaseScore] = []
    for question in questions:
        chunks, answer = await rag.answer_once(question.question)
        cases.append(score_case(question, chunks, answer))
    return cases


def list_ingestible_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Document directory is missing: {directory}")
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not paths:
        raise FileNotFoundError(f"No ingestible documents in {directory}")
    return paths


def list_eval_ingest_paths(handbook_dir: Path) -> list[Path]:
    """Handbook pages plus eval-only fixtures (not shipped in the live Library)."""
    paths = list_ingestible_files(handbook_dir)
    if EVAL_FIXTURES_DIR.is_dir():
        paths.extend(list_ingestible_files(EVAL_FIXTURES_DIR))
    return sorted(paths, key=lambda path: (path.parent.name, path.name))


def score_case(
    question: GoldenQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
    answer: str,
) -> CaseScore:
    notes: list[str] = []
    retrieval_hit = _score_retrieval(question, retrieved_chunks, notes)
    generation_pass = _score_generation(
        question,
        answer,
        notes,
        source_count=len(retrieved_chunks),
    )
    if retrieval_hit is False:
        case_pass = False
    else:
        case_pass = generation_pass
    return CaseScore(
        question_id=question.id,
        question=question.question,
        answer=answer,
        retrieved_titles=_unique_titles(retrieved_chunks),
        retrieval_hit=retrieval_hit,
        generation_pass=generation_pass,
        case_pass=case_pass,
        notes=notes,
        first_match_rank=_first_match_rank(question, retrieved_chunks),
    )


def format_eval_report(report: EvalReport, *, heading: str = "Atlas RAG eval") -> str:
    hits, retrieval_scored = _retrieval_counts(report.cases)
    generation_passes = sum(1 for case in report.cases if case.generation_pass)
    case_passes = sum(1 for case in report.cases if case.case_pass)
    total = len(report.cases)
    lines = [
        heading,
        "=" * len(heading),
        f"Indexed documents: {report.indexed_document_count}",
        "",
        f"{'id':<24} {'retrieve':<10} {'generate':<10} pass",
    ]
    for case in report.cases:
        retrieve_label = _retrieve_label(case.retrieval_hit)
        generate_label = "PASS" if case.generation_pass else "FAIL"
        pass_label = "PASS" if case.case_pass else "FAIL"
        lines.append(
            f"{case.question_id:<24} {retrieve_label:<10} {generate_label:<10} {pass_label}"
        )
        if not case.case_pass:
            for note in case.notes:
                lines.append(f"  - {note}")
            preview = case.answer.replace("\n", " ").strip()
            if len(preview) > 240:
                preview = f"{preview[:237]}..."
            lines.append(f"  answer: {preview}")
            if case.retrieved_titles:
                titles = ", ".join(case.retrieved_titles)
                lines.append(f"  sources: {titles}")
    lines.extend(
        [
            "",
            f"Hit rate:       {_ratio(hits, retrieval_scored)}",
            f"Generation:     {_ratio(generation_passes, total)}",
            f"Cases passed:   {_ratio(case_passes, total)}",
        ]
    )
    return "\n".join(lines)


def format_rerank_comparison(with_rerank: EvalReport, without_rerank: EvalReport) -> str:
    """Side-by-side scores for the same index run with and without the re-ranker."""
    with_by_id = _cases_by_id(with_rerank.cases, "with re-ranker")
    without_by_id = _cases_by_id(without_rerank.cases, "without re-ranker")
    if set(with_by_id) != set(without_by_id):
        raise ValueError("Re-ranker comparison arms must score the same question ids.")

    with_hit, with_generation, with_cases = _score_ratios(with_rerank.cases)
    without_hit, without_generation, without_cases = _score_ratios(without_rerank.cases)
    paired_with, paired_without = _paired_match_ranks(with_by_id, without_by_id)
    earlier_with, earlier_without, same_rank, one_arm_only = _rank_shifts(
        with_by_id, without_by_id
    )
    changed = _changed_question_lines(with_by_id, without_by_id)

    lines = [
        "Re-ranker comparison",
        "====================",
        (
            "Same indexed documents and the same questions. "
            "With = live Ask (the re-ranker keeps the best 5 of the merged pile). "
            "Without = the hybrid merge truncated to the top 5, no model re-rank."
        ),
        "",
        f"{'':<24} {'with re-ranker':<18} without",
        f"{'Retrieval hit rate':<24} {with_hit:<18} {without_hit}",
        f"{'Generation':<24} {with_generation:<18} {without_generation}",
        f"{'Cases passed':<24} {with_cases:<18} {without_cases}",
        (
            f"{'Mean first-match rank':<24} "
            f"{_format_mean_rank(paired_with):<18} {_format_mean_rank(paired_without)}"
        ),
        (
            "Mean rank counts only questions where both arms found a matching passage. "
            "1 is the first card."
        ),
        "",
        f"Matching passage earlier with the re-ranker: {earlier_with}",
        f"Matching passage earlier without:            {earlier_without}",
        f"Same first-match rank:                       {same_rank}",
        f"Only one arm found the passage:              {one_arm_only}",
        "",
        "Questions that changed",
        "----------------------",
    ]
    if not changed:
        lines.append("No question changed retrieval, the answer score, or first-match rank.")
        return "\n".join(lines)
    lines.append(f"{'id':<36} {'retrieve':<16} {'generate':<16} first match")
    lines.append(f"{'':<36} {'with / without':<16} {'with / without':<16} with -> without")
    lines.extend(changed)
    return "\n".join(lines)


def _score_retrieval(
    question: GoldenQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
    notes: list[str],
) -> bool | None:
    if question.expect_unknown:
        return None
    combined_text = "\n".join(chunk.text for chunk in retrieved_chunks)
    titles = [chunk.document_title for chunk in retrieved_chunks]
    if not retrieved_chunks:
        notes.append("retrieval returned no chunks")
        return False
    missing_passages = [
        phrase
        for phrase in question.chunk_contains
        if not _contains(combined_text, phrase)
    ]
    missing_titles = [
        phrase
        for phrase in question.source_title_contains
        if not any(_contains(title, phrase) for title in titles)
    ]
    if missing_passages:
        joined = ", ".join(repr(item) for item in missing_passages)
        notes.append(f"retrieved text missing {joined}")
    if missing_titles:
        joined = ", ".join(repr(item) for item in missing_titles)
        notes.append(f"retrieved titles missing {joined}")
    return not missing_passages and not missing_titles


def _score_generation(
    question: GoldenQuestion,
    answer: str,
    notes: list[str],
    source_count: int,
) -> bool:
    failed = False
    if question.expect_unknown:
        if not _has_unknown_marker(answer):
            notes.append("expected an I-don't-know answer")
            failed = True
    else:
        for phrase in question.must_contain:
            if not _contains(answer, phrase):
                notes.append(f"answer missing {phrase!r}")
                failed = True
        if not parse_citation_numbers(answer, source_count):
            notes.append("answer has no in-range [n] citation")
            failed = True
    for phrase in question.must_not_contain:
        if _contains(answer, phrase):
            notes.append(f"answer contains forbidden {phrase!r}")
            failed = True
    return not failed


def _has_unknown_marker(answer: str) -> bool:
    return any(_contains(answer, marker) for marker in UNKNOWN_MARKERS)


def _normalize_eval_text(text: str) -> str:
    """Fold case and normalize punctuation the model often varies."""
    folded = text.casefold()
    for src, dst in (
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u2013", "-"),
        ("\u2014", "-"),
    ):
        folded = folded.replace(src, dst)
    return folded


def _contains(haystack: str, needle: str) -> bool:
    return _normalize_eval_text(needle) in _normalize_eval_text(haystack)


def _unique_titles(chunks: Sequence[RetrievedChunk]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.document_title in seen:
            continue
        seen.add(chunk.document_title)
        titles.append(chunk.document_title)
    return titles


def _duplicate_ids(question_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for question_id in question_ids:
        if question_id in seen and question_id not in duplicates:
            duplicates.append(question_id)
        seen.add(question_id)
    return duplicates


def _retrieval_counts(cases: Sequence[CaseScore]) -> tuple[int, int]:
    scored = [case for case in cases if case.retrieval_hit is not None]
    hits = sum(1 for case in scored if case.retrieval_hit)
    return hits, len(scored)


def _retrieve_label(retrieval_hit: bool | None) -> str:
    if retrieval_hit is None:
        return "n/a"
    return "HIT" if retrieval_hit else "MISS"


def _first_match_rank(
    question: GoldenQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
) -> int | None:
    if question.expect_unknown:
        return None
    for position, chunk in enumerate(retrieved_chunks, start=1):
        if any(_contains(chunk.text, phrase) for phrase in question.chunk_contains):
            return position
    return None


def _cases_by_id(cases: Sequence[CaseScore], arm_label: str) -> dict[str, CaseScore]:
    by_id: dict[str, CaseScore] = {}
    for case in cases:
        if case.question_id in by_id:
            raise ValueError(f"{arm_label} has duplicate question id {case.question_id!r}")
        by_id[case.question_id] = case
    return by_id


def _score_ratios(cases: Sequence[CaseScore]) -> tuple[str, str, str]:
    hits, retrieval_scored = _retrieval_counts(cases)
    generation_passes = sum(1 for case in cases if case.generation_pass)
    case_passes = sum(1 for case in cases if case.case_pass)
    total = len(cases)
    return (
        _ratio(hits, retrieval_scored),
        _ratio(generation_passes, total),
        _ratio(case_passes, total),
    )


def _paired_match_ranks(
    with_by_id: dict[str, CaseScore],
    without_by_id: dict[str, CaseScore],
) -> tuple[list[int], list[int]]:
    with_ranks: list[int] = []
    without_ranks: list[int] = []
    for question_id, with_case in with_by_id.items():
        without_case = without_by_id[question_id]
        if with_case.first_match_rank is None or without_case.first_match_rank is None:
            continue
        with_ranks.append(with_case.first_match_rank)
        without_ranks.append(without_case.first_match_rank)
    return with_ranks, without_ranks


def _rank_shifts(
    with_by_id: dict[str, CaseScore],
    without_by_id: dict[str, CaseScore],
) -> tuple[int, int, int, int]:
    earlier_with = 0
    earlier_without = 0
    same_rank = 0
    one_arm_only = 0
    for question_id, with_case in with_by_id.items():
        without_case = without_by_id[question_id]
        with_rank = with_case.first_match_rank
        without_rank = without_case.first_match_rank
        if with_rank is None and without_rank is None:
            continue
        if with_rank is None or without_rank is None:
            one_arm_only += 1
            continue
        if with_rank < without_rank:
            earlier_with += 1
        elif without_rank < with_rank:
            earlier_without += 1
        else:
            same_rank += 1
    return earlier_with, earlier_without, same_rank, one_arm_only


def _changed_question_lines(
    with_by_id: dict[str, CaseScore],
    without_by_id: dict[str, CaseScore],
) -> list[str]:
    lines: list[str] = []
    for question_id, with_case in with_by_id.items():
        without_case = without_by_id[question_id]
        if (
            with_case.retrieval_hit == without_case.retrieval_hit
            and with_case.generation_pass == without_case.generation_pass
            and with_case.first_match_rank == without_case.first_match_rank
        ):
            continue
        retrieve = (
            f"{_retrieve_label(with_case.retrieval_hit)} / "
            f"{_retrieve_label(without_case.retrieval_hit)}"
        )
        generate = (
            f"{'PASS' if with_case.generation_pass else 'FAIL'} / "
            f"{'PASS' if without_case.generation_pass else 'FAIL'}"
        )
        with_rank = _rank_label(with_case.first_match_rank)
        without_rank = _rank_label(without_case.first_match_rank)
        first_match = f"{with_rank} -> {without_rank}"
        lines.append(f"{question_id:<36} {retrieve:<16} {generate:<16} {first_match}")
    return lines


def _rank_label(rank: int | None) -> str:
    if rank is None:
        return "-"
    return str(rank)


def _format_mean_rank(ranks: Sequence[int]) -> str:
    if not ranks:
        return "n/a"
    mean = sum(ranks) / len(ranks)
    return f"{mean:.2f}  (n={len(ranks)})"


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    percent = 100.0 * numerator / denominator
    return f"{numerator}/{denominator}  ({percent:.1f}%)"
