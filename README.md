# Atlas

A recruiter-ready RAG chatbot: **retrieve first, then generate**, with the retrieval step visible in the UI.

Atlas is not a LangChain wrapper. It is a small FastAPI + React app that embeds documents with OpenAI, searches ChromaDB, quarantines retrieved text in XML tags, and streams the answer. A right-hand inspector shows the chunks and cosine distances used for the latest reply.

**Repo:** [github.com/JoshStevens582/atlas](https://github.com/JoshStevens582/atlas)

## Demo script (2 minutes)

1. Open the app and click **How does Atlas retrieve answers?**
2. Point at the inspector: those passages were searched *before* the model wrote a word.
3. Ask **What is the refund policy?** The vendor memo tries to jailbreak the model. Atlas should stay on the 14-day store-credit rule.
4. Upload one of your own `.md` / `.txt` / `.pdf` files and ask a question only that file can answer.

## Stack

| Layer | Choice | Why it is in the demo |
| :--- | :--- | :--- |
| API | FastAPI, async | Router → service → repository |
| Chat DB | SQLite via SQLAlchemy | Threads, messages, document *metadata* only |
| **Vector DB** | **ChromaDB** (cosine, persistent under `data/chroma`) | Chunk embeddings + nearest-neighbor search |
| Model | OpenAI `gpt-4o-mini` + `text-embedding-3-small` | Streaming Responses API |
| UI | React + TypeScript | SSE tokens, citations, retrieval inspector |

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

Open [http://localhost:5173](http://localhost:5173). Sample handbook files in `sample_docs/` are indexed on first start.

## Architecture

```text
Browser  --POST /api/chat/stream-->  FastAPI
                                      1. embed question
                                      2. query Chroma (top-k, distance cutoff)
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
