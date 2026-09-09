# Atlas architecture

Atlas is a FastAPI backend plus a React chat UI. The backend is layered so HTTP, business rules, and storage stay separate.

## Layers

- **Routers** validate JSON and stream server-sent events.
- **Services** chunk text, embed with OpenAI, retrieve from Chroma, and assemble the prompt.
- **Repositories** talk to SQLite (threads, messages, document records) and Chroma (chunk vectors).

The React app never sees the OpenAI key. Browsers POST to `/api/chat/stream`. FastAPI streams tokens back as SSE.

## What a recruiter can inspect

The right-hand inspector shows the retrieved chunks, cosine distances, and source titles for the latest answer. That is the retrieve half of RAG made visible. The chat pane is the generate half.

Chat memory is the last 12 messages in the thread. Older turns are not dumped into Chroma.
