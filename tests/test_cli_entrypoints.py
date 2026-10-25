from pathlib import Path
from typing import Any

import pytest
from openai import AsyncOpenAI

from atlas import eval_cli, ingest_worker
from atlas.config import Settings
from atlas.schemas.eval import CaseScore, EvalReport
from atlas.services.redis_client import RedisRequiredError


@pytest.mark.asyncio
async def test_eval_cli_exits_1_and_explains_when_the_openai_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(eval_cli, "load_settings", lambda: Settings(openai_api_key=""))

    exit_code = await eval_cli._async_main(Path("questions.json"), Path("docs"))

    assert exit_code == 1
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_eval_cli_compare_rerank_still_requires_the_openai_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(eval_cli, "load_settings", lambda: Settings(openai_api_key=""))

    exit_code = await eval_cli._async_main(
        Path("questions.json"),
        Path("docs"),
        compare_rerank=True,
    )

    assert exit_code == 1
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_compare_rerank_exit_code_follows_the_live_arm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    live_pass = _eval_report(case_pass=True)
    baseline_fail = _eval_report(case_pass=False)

    exit_code = eval_cli._print_rerank_comparison(live_pass, baseline_fail)

    assert exit_code == 0
    rendered = capsys.readouterr().out
    assert "Without re-ranker" in rendered
    assert "With re-ranker\n" not in rendered


def test_compare_rerank_exits_1_when_the_live_arm_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = eval_cli._print_rerank_comparison(
        _eval_report(case_pass=False),
        _eval_report(case_pass=True),
    )

    assert exit_code == 1
    assert "With re-ranker" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_score_rerank_arms_runs_the_live_path_then_the_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reranker_present: list[bool] = []

    async def record_arm(rag: Any, questions: list[object]) -> list[CaseScore]:
        reranker_present.append(rag._reranker is not None)
        assert questions == []
        return []

    monkeypatch.setattr(eval_cli, "run_eval_suite", record_arm)

    with_report, without_report = await eval_cli._score_rerank_arms(
        Settings(openai_api_key="test-key", rerank_model="test-rerank-model"),
        AsyncOpenAI(api_key="test-key"),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        [],
        7,
    )

    assert reranker_present == [True, False]
    assert with_report.indexed_document_count == 7
    assert without_report.indexed_document_count == 7
    assert with_report.cases == []
    assert without_report.cases == []


def _eval_report(*, case_pass: bool) -> EvalReport:
    return EvalReport(
        indexed_document_count=1,
        cases=[
            CaseScore(
                question_id="carryover",
                question="How much leave carries over?",
                answer="240 hours [1].",
                retrieved_titles=["Leave types"],
                retrieval_hit=case_pass,
                generation_pass=case_pass,
                case_pass=case_pass,
                notes=[] if case_pass else ["retrieved text missing '240'"],
                first_match_rank=1 if case_pass else None,
            )
        ],
    )


@pytest.mark.asyncio
async def test_standalone_ingest_worker_refuses_to_start_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_redis(_settings: Settings) -> Any:
        return None

    monkeypatch.setattr(ingest_worker, "load_settings", lambda: Settings())
    monkeypatch.setattr(ingest_worker, "connect_redis", no_redis)

    with pytest.raises(SystemExit, match="Redis is required"):
        await ingest_worker._run()


@pytest.mark.asyncio
async def test_standalone_ingest_worker_exits_when_redis_is_required_and_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def redis_down(_settings: Settings) -> Any:
        raise RedisRequiredError(
            "Redis is required but did not answer the startup ping."
        )

    monkeypatch.setattr(ingest_worker, "load_settings", lambda: Settings(redis_required=True))
    monkeypatch.setattr(ingest_worker, "connect_redis", redis_down)

    with pytest.raises(SystemExit, match="did not answer the startup ping"):
        await ingest_worker._run()
