import argparse
import asyncio
import sys
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings, load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.schemas.eval import EvalReport, GoldenQuestion
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService
from atlas.services.rag_eval import (
    format_eval_report,
    format_rerank_comparison,
    isolated_eval_settings,
    list_eval_ingest_paths,
    load_golden_questions,
    run_eval_suite,
)
from atlas.services.rerank import LlmReranker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Atlas golden-set RAG evals against the handbook."
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("evals/questions.json"),
        help="Golden question JSON (default: evals/questions.json)",
    )
    parser.add_argument(
        "--docs",
        type=Path,
        default=Path("handbook"),
        help="Directory to index for this run (default: handbook)",
    )
    parser.add_argument(
        "--compare-rerank",
        action="store_true",
        help=(
            "Score the same index twice: with the re-ranker and without it. "
            "The exit code follows the with-re-ranker arm."
        ),
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(_async_main(args.questions, args.docs, compare_rerank=args.compare_rerank))
    )


async def _async_main(
    questions_path: Path,
    docs_dir: Path,
    compare_rerank: bool = False,
) -> int:
    settings = load_settings()
    if not settings.openai_api_key:
        print(
            "OPENAI_API_KEY is not set. Add it to .env before running evals.",
            file=sys.stderr,
        )
        return 1

    eval_settings = isolated_eval_settings(settings)
    Path("data/eval").mkdir(parents=True, exist_ok=True)
    engine = create_engine(eval_settings)
    await init_database(engine)
    session_factory = create_session_factory(engine)
    openai_client = AsyncOpenAI(api_key=eval_settings.openai_api_key)
    embeddings = EmbeddingClient(openai_client, eval_settings.openai_embedding_model)
    chunk_store = ChromaChunkStore(eval_settings.chroma_path)
    ingest = IngestService(eval_settings, session_factory, chunk_store, embeddings)
    outcome: EvalReport | tuple[EvalReport, EvalReport] | None = None
    try:
        questions = load_golden_questions(questions_path)
        indexed = await ingest.reset_and_ingest_paths(list_eval_ingest_paths(docs_dir))
        if compare_rerank:
            outcome = await _score_rerank_arms(
                eval_settings,
                openai_client,
                session_factory,
                chunk_store,
                embeddings,
                questions,
                len(indexed),
            )
        else:
            reranker = (
                LlmReranker(openai_client, eval_settings.rerank_model)
                if eval_settings.rerank_enabled
                else None
            )
            rag = _chat_service(
                eval_settings,
                openai_client,
                session_factory,
                chunk_store,
                embeddings,
                reranker,
            )
            cases = await run_eval_suite(rag, questions)
            outcome = EvalReport(indexed_document_count=len(indexed), cases=cases)
    finally:
        await engine.dispose()

    if isinstance(outcome, tuple):
        return _print_rerank_comparison(outcome[0], outcome[1])
    if outcome is None:
        raise RuntimeError("Eval finished without a report.")
    print(format_eval_report(outcome))
    if any(not case.case_pass for case in outcome.cases):
        return 1
    return 0


async def _score_rerank_arms(
    settings: Settings,
    openai_client: AsyncOpenAI,
    session_factory: async_sessionmaker[AsyncSession],
    chunk_store: ChromaChunkStore,
    embeddings: EmbeddingClient,
    questions: list[GoldenQuestion],
    indexed_document_count: int,
) -> tuple[EvalReport, EvalReport]:
    """One index, two passes. The with-re-ranker pass is the live Ask path."""
    with_rag = _chat_service(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
        LlmReranker(openai_client, settings.rerank_model),
    )
    without_rag = _chat_service(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
        None,
    )
    with_cases = await run_eval_suite(with_rag, questions)
    without_cases = await run_eval_suite(without_rag, questions)
    return (
        EvalReport(indexed_document_count=indexed_document_count, cases=with_cases),
        EvalReport(indexed_document_count=indexed_document_count, cases=without_cases),
    )


def _chat_service(
    settings: Settings,
    openai_client: AsyncOpenAI,
    session_factory: async_sessionmaker[AsyncSession],
    chunk_store: ChromaChunkStore,
    embeddings: EmbeddingClient,
    reranker: LlmReranker | None,
) -> RagChatService:
    return RagChatService(
        settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
        reranker=reranker,
    )


def _print_rerank_comparison(with_rerank: EvalReport, without_rerank: EvalReport) -> int:
    print(format_rerank_comparison(with_rerank, without_rerank))
    if any(not case.case_pass for case in without_rerank.cases):
        print()
        print(format_eval_report(without_rerank, heading="Without re-ranker"))
    if any(not case.case_pass for case in with_rerank.cases):
        print()
        print(format_eval_report(with_rerank, heading="With re-ranker"))
        return 1
    return 0


if __name__ == "__main__":
    main()
