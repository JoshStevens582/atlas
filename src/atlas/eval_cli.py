import argparse
import asyncio
import sys
from pathlib import Path

from openai import AsyncOpenAI

from atlas.config import load_settings
from atlas.db.session import create_engine, create_session_factory, init_database
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.schemas.eval import EvalReport
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService
from atlas.services.rag_eval import (
    format_eval_report,
    isolated_eval_settings,
    list_ingestible_files,
    load_golden_questions,
    run_eval_suite,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Atlas golden-set RAG evals against sample_docs."
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
        default=Path("sample_docs"),
        help="Directory to index for this run (default: sample_docs)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_async_main(args.questions, args.docs)))


async def _async_main(questions_path: Path, docs_dir: Path) -> int:
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
    rag = RagChatService(
        eval_settings,
        openai_client,
        session_factory,
        chunk_store,
        embeddings,
    )
    try:
        questions = load_golden_questions(questions_path)
        indexed = await ingest.reset_and_ingest_paths(list_ingestible_files(docs_dir))
        cases = await run_eval_suite(rag, questions)
        report = EvalReport(indexed_document_count=len(indexed), cases=cases)
    finally:
        await engine.dispose()

    print(format_eval_report(report))
    if any(not case.case_pass for case in report.cases):
        return 1
    return 0


if __name__ == "__main__":
    main()
