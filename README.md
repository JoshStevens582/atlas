# Atlas

A recruiter-ready RAG chatbot: **retrieve first, then generate**, with the retrieval step visible in the UI.

Atlas is not a LangChain wrapper. It is a small FastAPI + React app that embeds documents with OpenAI, searches ChromaDB, quarantines retrieved text in XML tags, and streams the answer. A right-hand **Sources used** panel shows the chunks and cosine distances used for the latest reply.

**Repo:** [github.com/JoshStevens582/atlas](https://github.com/JoshStevens582/atlas)

## Demo script (2 minutes)

1. Open the app and click **How does Atlas retrieve answers?**
2. Point at **Sources used**: those passages were searched *before* the model wrote a word.
3. Ask **What is the refund policy?** The vendor memo tries to jailbreak the model. Atlas should stay on the 14-day store-credit rule.
4. Ask **Where is support ticket T-104?** That status is not in the handbook. The model should call `get_support_ticket`; Atlas runs it; **Sources used** shows the tool result.
5. Upload one of your own `.md` / `.txt` / `.pdf` files and ask a question only that file can answer.

## Stack

| Layer | Choice | Why it is in the demo |
| :--- | :--- | :--- |
| API | FastAPI, async | Router → service → repository |
| Chat DB | SQLite via SQLAlchemy | Threads, messages, document *metadata* only |
| **Vector DB** | **ChromaDB** (cosine, persistent under `data/chroma`) | Chunk embeddings + nearest-neighbor search |
| **Hybrid retrieve** | Chroma vectors + **BM25** keywords, fused with RRF | Rare words / ids that cosine can miss |
| Model | OpenAI `gpt-4o-mini` + `text-embedding-3-small` | Streaming Responses API |
| UI | React + TypeScript | SSE tokens, citations, Sources used panel |

SQLite is **not** the vector database. ChromaDB is. Atlas embeds with OpenAI and passes those vectors into Chroma explicitly (no local ONNX embedder).

## Run locally

You need Python 3.12+, Node 20+, and `OPENAI_API_KEY` in the environment (or a `.env` file in this folder). Copy `.env.example` and fill in your key. Never commit `.env`.

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

Open [http://localhost:5173](http://localhost:5173). Click **Continue as demo user** for a one-click look (no signup), sign in with `alice` / `atlas-alice` or `bob` / `atlas-bob`, or create your own account — signup is real: passwords are bcrypt-hashed and stored in SQLite, not a hardcoded list. Threads are per user. Sample handbook files in `sample_docs/` are indexed on first start.

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

### Ask answer cache

With Redis up, handbook-style Asks cache the finished answer (key = model + instructions + question + retrieved chunks + history). Same Ask again with the same retrieve context → skip the **generate** OpenAI call (retrieve still runs). Ticket/tool Asks are not cached. TTL default 1 hour (`ANSWER_CACHE_TTL_SECONDS`).

## Architecture

```text
Browser  --POST /api/chat/stream-->  FastAPI
                                      1. embed question
                                      2. query Chroma (vectors) + BM25 (keywords), fuse with RRF
                                      3. wrap hits in <context>, question in <user_query>
                                      4. stream tokens from OpenAI as SSE
                                      5. save the turn in SQLite
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
