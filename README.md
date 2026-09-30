# Atlas

[![CI](https://github.com/JoshStevens582/atlas/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/JoshStevens582/atlas/actions/workflows/ci.yml)

A recruiter-ready RAG chatbot: **retrieve first, then generate**, with the retrieval step visible in the UI.

Atlas is not a LangChain wrapper. It is a small FastAPI + React app that embeds documents with OpenAI, searches ChromaDB, quarantines retrieved text in XML tags, and streams the answer. A right-hand **Sources used** panel shows the chunks and cosine distances retrieve found. After the model writes, `[1]` in the answer marks which of those cards it used. Fake numbers are dropped.

**Live demo:** [102-203-81-249.sslip.io](https://102-203-81-249.sslip.io)  
**Repo:** [github.com/JoshStevens582/atlas](https://github.com/JoshStevens582/atlas)

![Atlas answering "What is the project codename?" with the answer "Northstar [1]" and the matching passage in the Sources used panel](docs/atlas-screenshot.png)

## Demo script (2 minutes)

The Library is 21 pages of the **TTS Handbook** (leave, overtime, travel, work schedules, security and more), the real employee handbook of a US government digital team. It is public domain, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Each page shows a `Self-test: n/n found` score.

1. Click **Continue as demo user**, then click **How many annual leave hours can I carry over?**
2. The answer is **240**, with `[1]` in the sentence. Point at **Sources used**: that passage was found *before* the model wrote a word. The card says which passage `[1]` is, and `in answer` marks that the model used it. The **Answer backed by the sources** card is a second model checking the answer against those passages.
3. Click **I have 182 annual leave hours, 5 years of service and 25 pay periods left. How many hours would I lose?** Arithmetic is not something to trust a language model with, so the model calls `estimate_annual_leave`, Atlas runs it, and a tool card shows 332 projected hours and **92** lost to the 240 cap. The rule itself still comes from the handbook.
4. Click **Which day is Independence Day observed in 2026?** The model calls `get_federal_holidays`. The tool card shows Saturday 4 July, observed Friday 3 July.
5. Upload one of your own `.md` / `.txt` / `.pdf` files and ask a question only that file can answer. The Library scores the new file with the same self-test. With a longer file, the cards are labelled `vector`, `lexical`, or `both`, and the re-ranker puts the most useful passage first.

## Stack

| Layer | Choice | Why it is in the demo |
| :--- | :--- | :--- |
| API | FastAPI, async | Router → service → repository |
| Chat DB | SQLite via SQLAlchemy | Threads, messages, document *metadata* only |
| **Vector DB** | **ChromaDB** (cosine, persistent under `data/chroma`) | Chunk embeddings + nearest-neighbor search |
| **Hybrid retrieve** | Chroma vectors + **BM25** keywords, fused with RRF | Rare words / ids that cosine can miss |
| **Re-ranker** | `gpt-4o-mini` reads the question and each merged chunk, keeps the best 5 | Puts the most useful paragraph first; falls back to the merged order if the call fails |
| Model | OpenAI `gpt-4o-mini` + `text-embedding-3-small` | Streaming Responses API |
| UI | React + TypeScript | SSE tokens, citations, Sources used panel |

SQLite is **not** the vector database. ChromaDB is. Atlas embeds with OpenAI and passes those vectors into Chroma explicitly (no local ONNX embedder).

## Run locally

You need Python 3.12+, Node 20+, and `OPENAI_API_KEY` in the environment (or a `.env` file in this folder). Copy `.env.example` and fill in your key. Never commit `.env`.

You also need `ATLAS_AUTH_SECRET` set to a real random value — the app refuses to start with the shipped default or a secret under 32 bytes, since anyone who has read the source knows the default and could forge session JWTs with it:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the output in `.env` as `ATLAS_AUTH_SECRET=...`.

```powershell
uv sync --group dev
uv run uvicorn atlas.main:app --reload --host 127.0.0.1 --port 8787 --app-dir src
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Click **Continue as demo user** for a one-click look (no signup), sign in with `alice` / `atlas-alice` or `bob` / `atlas-bob`, or create your own account — signup is real: passwords are bcrypt-hashed and stored in SQLite, not a hardcoded list. Threads are per user. On first start the pages in `handbook/` are indexed (without the self-test, so boot stays quick).
To wipe everything and re-index the handbook with self-test scores (stop the API first, Chroma and SQLite are not meant for two writers):

```powershell
uv run python -m atlas.reset_library
```

On the server: `docker compose stop api`, then `docker compose run --rm api python -m atlas.reset_library`, then `docker compose up -d api`. It takes a few minutes and a few cents of OpenAI calls.

To rebuild `handbook/` from upstream, see `scripts/build_handbook.py`.

### Redis (Library upload queue)

Uploads validate, save the file, then **enqueue** ingest on Redis (`LPUSH` / `BRPOP`). The API returns **202** immediately. A **worker** (Python process) then runs ingest: it chunks the file, calls the OpenAI **embedding** model for vectors, and writes Chroma + SQLite. The embedding model only returns numbers; chunking and saves are your code. Job status: `GET /api/documents/jobs/{job_id}` (`pending` → `running` → `done` / `failed`).

1. Start Redis locally (default `redis://127.0.0.1:6379/0`), e.g. Docker:

```powershell
docker run -d --name atlas-redis -p 6379:6379 redis:7
```

2. Optional `.env`: `REDIS_URL=redis://127.0.0.1:6379/0`

With Redis up, the API runs an **embedded worker** by default so one `uvicorn` is enough. For a separate process (production shape):

```powershell
# .env: INGEST_WORKER_EMBEDDED=false
uv run python -m atlas.ingest_worker
```

If Redis is down, Atlas falls back to **synchronous** ingest (same as before) and logs a warning. App rate limits are also skipped until Redis is back.

### Rate limits (cost control)

Users share **your** OpenAI key. Atlas caps abuse in Redis (fail **closed** if Redis is down while limits are on):

| Cap | Default |
| :--- | :--- |
| Ask / minute / user | 10 |
| Ask / day / user | 40 |
| Ask / day / **whole app** | 200 |
| Upload / minute / user | 5 |
| Upload / day / user | 15 |
| Login / minute / IP | 10 |
| Login / day / IP | 100 |
| Signup / minute / IP | 5 |
| Signup / day / IP | 20 |
| Demo / minute / IP | 20 |
| Demo / day / IP | 200 |

Over limit → **429**. No Redis while `RATE_LIMIT_ENABLED=true` → **503** (won’t run uncapped). Local without Redis: `RATE_LIMIT_ENABLED=false`. Auth routes (`/login`, `/signup`, `/demo`) are capped by **client IP** because there is no logged-in user yet.

**Also set a hard spend limit in the OpenAI dashboard** (Settings → Billing / Limits). That is the last stop if something bypasses the app.

### Answer check (works on any uploaded document)

The golden-set eval only covers the handbook pages. For documents users upload there is no answer key, so every handbook Ask gets a second, separate model call after the answer streams: it reads the question, the answer and the retrieved chunks and returns `supported`, `partly_supported` or `not_supported` with a one-line reason. The Sources panel shows it as a `check` stream event. It is a model judging a model, so it can be wrong, and the UI says so. It is skipped for tool answers and the "not in the handbook" reply, and if the call fails the Ask still succeeds with no verdict. The verdict is cached with the answer. Turn it off with `ANSWER_CHECK_ENABLED=false`; the model is `ANSWER_CHECK_MODEL` (default `gpt-4o-mini`).

### Upload self-test

Right after a document is indexed, Atlas samples up to six passages spread across it, has a model write one question per passage, then runs the real `retrieve` on each question. The Library shows `Self-test: 5/6 found` — how many questions got their own passage back. A low score means search is struggling with that document (scanned layout, tables, very repetitive text). It is a rough smoke test, not a guarantee: the questions are model-written and a question can fit more than one passage. It is skipped for documents with no passage over 200 characters, and if a model call fails the upload still succeeds with no score. Settings: `RETRIEVAL_CHECK_ENABLED`, `RETRIEVAL_CHECK_MODEL`, `RETRIEVAL_CHECK_SAMPLES`. The separate CLI ingest worker does not run it; the embedded worker and the no-Redis upload path do.

### Ask answer cache

With Redis up, handbook-style Asks cache the finished answer (key = model + instructions + question + retrieved chunks + history). Same Ask again with the same retrieve context → skip the **generate** OpenAI call (retrieve and re-rank still run). Asks that used a tool are not cached. TTL default 1 hour (`ANSWER_CACHE_TTL_SECONDS`).

## Deploy with Docker

One server, three containers: **web** (Caddy serves the built React app and forwards `/api`), **api** (FastAPI plus the ingest worker) and **redis**. Only the web container is exposed; the API and Redis are not reachable from outside.

```bash
cp .env.example .env     # set OPENAI_API_KEY and ATLAS_AUTH_SECRET
docker compose up -d --build
```

- Without `SITE_ADDRESS` it serves plain HTTP on port 80.
- With `SITE_ADDRESS=your.domain` in `.env`, Caddy gets and renews a free HTTPS certificate and redirects HTTP to HTTPS. DNS must point at the server and ports 80 and 443 must be open.
- Data (SQLite, Chroma, uploads) lives in the `atlas-data` volume and survives rebuilds and restarts.
- CI builds and starts this stack on every PR and checks it through the front door.

## Architecture

```text
Browser  --POST /api/chat/stream-->  FastAPI
                                      1. embed question
                                      2. query Chroma (vectors) + BM25 (keywords), fuse with RRF
                                      3. re-rank the merged chunks, keep the best 5
                                      4. wrap hits in <context>, question in <user_query>
                                      5. stream tokens from OpenAI as SSE
                                      6. save the turn in SQLite
```

Chunking packs short paragraphs, then uses a sliding window (`chunk_size=900`, `overlap=150`) so sentences on a boundary still appear in one chunk.

## Quality checks

```powershell
uv run ruff check .
uv run mypy .
uv run pytest --cov=atlas --cov-branch
```

Golden-set RAG eval (needs `OPENAI_API_KEY`; writes to `data/eval/` so it does not touch the demo index):

```powershell
uv run python -m atlas.eval_cli
```

## License

[MIT](LICENSE)
