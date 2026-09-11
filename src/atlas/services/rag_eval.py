from collections.abc import Sequence
from pathlib import Path

from atlas.config import Settings
from atlas.schemas.chat import RetrievedChunk
from atlas.schemas.eval import CaseScore, EvalReport, GoldenQuestion, GoldenQuestionSet
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


def score_case(
    question: GoldenQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
    answer: str,
) -> CaseScore:
    notes: list[str] = []
    retrieval_hit = _score_retrieval(question, retrieved_chunks, notes)
    generation_pass = _score_generation(question, answer, notes)
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
    )


def format_eval_report(report: EvalReport) -> str:
    hits, retrieval_scored = _retrieval_counts(report.cases)
    generation_passes = sum(1 for case in report.cases if case.generation_pass)
    case_passes = sum(1 for case in report.cases if case.case_pass)
    total = len(report.cases)
    lines = [
        "Atlas RAG eval",
        "==============",
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
    for phrase in question.must_not_contain:
        if _contains(answer, phrase):
            notes.append(f"answer contains forbidden {phrase!r}")
            failed = True
    return not failed


def _has_unknown_marker(answer: str) -> bool:
    return any(_contains(answer, marker) for marker in UNKNOWN_MARKERS)


def _contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


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


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    percent = 100.0 * numerator / denominator
    return f"{numerator}/{denominator}  ({percent:.1f}%)"
