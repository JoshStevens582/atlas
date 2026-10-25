# Golden-set RAG eval

Atlas scores itself against `evals/questions.json` on the TTS Handbook (`handbook/`) plus eval-only fixtures under `evals/fixtures/` (not in the live Library). Each case checks retrieval (right passage and title), generation (`must_contain`, optional `expected_answer`), and—for answerable questions—an in-range citation `[n]` on a retrieved chunk.

## Run locally

Needs a real `OPENAI_API_KEY` in `.env` or the environment. The run writes to `data/eval/` only; it does not touch the demo SQLite index or `./data/chroma`.

```bash
uv run python -m atlas.eval_cli
```

Exit code `0` only if every case passes. The CLI prints a per-question report and summary hit rates.

## CI (production step A9)

The **`rag-eval`** job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the same command on every push to `master` and on every pull request. A failing question blocks merge.

**One-time setup:** add a repository secret named `OPENAI_API_KEY` in GitHub (**Settings → Secrets and variables → Actions**). Use the same key you use for local evals. If the secret is missing, the job fails with `OPENAI_API_KEY is not set`.

On failure, CI uploads `eval-report.txt` as a workflow artifact so you can read which case broke without re-running locally.

**Branch protection:** if `master` requires status checks, add **`rag-eval`** alongside `backend`, `frontend`, and `docker`.

## When to change the golden set

- New handbook behaviour → update or add rows in `evals/questions.json`.
- Prompt or retrieval changes → run `uv run python -m atlas.eval_cli` before opening a PR; CI will run the full suite again.

See also `docs/models.md` for which OpenAI models the eval uses (chat, re-rank, embeddings).
