# Golden-set RAG eval

Atlas scores itself against `evals/questions.json` on the Cape Town Physio staff handbook (`handbook/`) plus eval-only fixtures under `evals/fixtures/` (not in the live Library). Each case checks retrieval (right passage and title), generation (`must_contain`, optional `expected_answer`), and—for answerable questions—an in-range citation `[n]` on a retrieved chunk.

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

## Re-ranker comparison (production step A10)

The live Ask path re-ranks the merged hybrid pile and keeps the best 5 cards. Turning the re-ranker off does not search a different index: it keeps the top 5 from that same merge and skips the model call.

```bash
uv run python -m atlas.eval_cli --compare-rerank
```

The command indexes the handbook once, then scores every golden question twice. The exit code follows the **with re-ranker** arm, which is what live Ask does. The without arm is the measurement. CI stays on the single with-re-ranker run so a pull request does not pay for both passes.

The report compares retrieval hit rate, generation (answer) score, and how early the first matching passage sits. Rank 1 is the first card. Mean rank uses only questions where both arms found a matching passage, so a miss does not pretend to be a rank.

### Measured 2026-10-25

Laptop run, exit code 0, about three minutes. Same golden set (handbook plus the eval-only injection fixture). Models are the laptop eval settings (`docs/models.md`).

| | With re-ranker | Without |
| :--- | :--- | :--- |
| Retrieval hit rate | 21/21 (100%) | 21/21 (100%) |
| Generation | 23/23 (100%) | 23/23 (100%) |
| Cases passed | 23/23 (100%) | 23/23 (100%) |
| Mean first-match rank | 1.00 (n=21) | 1.38 (n=21) |

The two unknown questions are in the 23 generation cases and are left out of the 21 retrieval scores.

Six questions had the matching passage on an earlier card with the re-ranker. None were earlier without it. Fifteen were the same rank. Both arms found the passage every time.

| Question | First match with → without |
| :--- | :--- |
| `annual-accrual-middle-tier` | 1 → 4 |
| `bereavement-sick-leave` | 1 → 2 |
| `military-leave-days` | 1 → 2 |
| `security-incident-deadline` | 1 → 2 |
| `vltp-donated-hours-vague` | 1 → 2 |
| `travel-amended-voucher-vague` | 1 → 2 |

On this set the re-ranker does not change the pass mark. The right passage was already inside the five cards either way, and every answer still passed. It does change order: with the re-ranker, that passage was card 1 on all 21 scored questions. Re-run the command if the golden set or the prompt changes; these numbers are that one run.
