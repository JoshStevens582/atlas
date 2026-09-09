import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  deleteDocument,
  fetchDocuments,
  fetchHealth,
  fetchThread,
  fetchThreads,
  streamChat,
  uploadDocument,
} from "./api";
import type {
  ChatMessage,
  HealthStatus,
  IndexedDocument,
  RetrievedChunk,
  ThreadSummary,
} from "./types";

function displayTitle(title: string): string {
  return title
    .replace(/^\d+\s+/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

const SUGGESTIONS = [
  {
    title: "What is the project codename?",
    detail: "Demo Note — answer should be Northstar.",
  },
  {
    title: "What are the demo support office hours?",
    detail: "Demo Note — Tuesday and Thursday, 10:00 to 13:00.",
  },
  {
    title: "What is the demo refund window?",
    detail: "Demo Note — 7 days, store credit, sample hardware.",
  },
];

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<RetrievedChunk[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [stage, setStage] = useState<"idle" | "search" | "generate">("idle");
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function refreshLists() {
    const [nextHealth, nextThreads, nextDocuments] = await Promise.all([
      fetchHealth(),
      fetchThreads(),
      fetchDocuments(),
    ]);
    setHealth(nextHealth);
    setThreads(nextThreads);
    setDocuments(nextDocuments);
  }

  useEffect(() => {
    refreshLists().catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  const selectedTitle = useMemo(
    () => threads.find((thread) => thread.id === threadId)?.title ?? "New chat",
    [threads, threadId],
  );

  async function openThread(id: string) {
    const detail = await fetchThread(id);
    setThreadId(detail.id);
    setMessages(detail.messages);
    setSources([]);
    setStage("idle");
  }

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || streaming) {
      return;
    }
    setError(null);
    setDraft("");
    setStreaming(true);
    setStage("search");
    setSources([]);
    setMessages((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: "user",
        content: trimmed,
        created_at: new Date().toISOString(),
      },
      {
        id: `stream-${Date.now()}`,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
      },
    ]);

    try {
      await streamChat(trimmed, threadId, (event) => {
        if (event.type === "thread") {
          setThreadId(event.thread.id);
          setThreads((current) => {
            const exists = current.some((thread) => thread.id === event.thread.id);
            return exists ? current : [event.thread, ...current];
          });
        }
        if (event.type === "sources") {
          setSources(event.sources);
          setStage("generate");
        }
        if (event.type === "token") {
          setMessages((current) => {
            const copy = [...current];
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant") {
              copy[copy.length - 1] = { ...last, content: last.content + event.text };
            }
            return copy;
          });
        }
        if (event.type === "error") {
          setError(event.detail);
        }
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Chat failed.");
    } finally {
      setStreaming(false);
      setStage("idle");
      await refreshLists().catch(() => undefined);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  async function onUpload(file: File | undefined) {
    if (!file) {
      return;
    }
    setError(null);
    try {
      await uploadDocument(file);
      await refreshLists();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    }
  }

  return (
    <div className="app">
      <aside className="panel sidebar">
        <div className="brand">
          <strong>ATLAS</strong>
          <span>Grounded RAG chat</span>
        </div>
        <div className="health">
          <span className={`pill ${health?.openai_configured ? "ok" : "warn"}`}>
            {health?.openai_configured ? "OpenAI ready" : "API key missing"}
          </span>
          <span className="pill ok">ChromaDB</span>
          <span className="pill">{health?.document_count ?? 0} docs</span>
          <span className="pill">{health?.chunk_count ?? 0} chunks</span>
        </div>
        <button className="new-chat" onClick={() => { setThreadId(null); setMessages([]); setSources([]); }}>
          New chat
        </button>
        <label className="upload" htmlFor="atlas-upload">
          Add document (.md / .txt / .pdf)
          <input
            id="atlas-upload"
            type="file"
            accept=".md,.txt,.pdf,text/plain,text/markdown,application/pdf"
            onChange={(event) => {
              void onUpload(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
        <div>
          <div className="section-label">Threads</div>
          <div className="stack">
            {threads.map((thread) => (
              <button
                key={thread.id}
                className={`row ${thread.id === threadId ? "active" : ""}`}
                onClick={() => void openThread(thread.id)}
              >
                {thread.title}
              </button>
            ))}
          </div>
        </div>
        <div className="corpus-block">
          <div className="section-label">Corpus</div>
          <div className="stack">
            {documents.map((document) => (
              <div className="row" key={document.id}>
                <span>
                  {displayTitle(document.title)}
                  <br />
                  <small>{document.chunk_count} chunks</small>
                </span>
                <button
                  className="pill"
                  onClick={() =>
                    void deleteDocument(document.id).then(refreshLists).catch((reason: Error) =>
                      setError(reason.message),
                    )
                  }
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      </aside>

      <main className="panel chat">
        <header className="chat-header">
          <h1>{selectedTitle}</h1>
          <p>Search the handbook first. Then generate. Citations stay visible.</p>
          <label className="upload header-upload" htmlFor="atlas-upload-main">
            Add a document to the corpus
            <input
              id="atlas-upload-main"
              type="file"
              accept=".md,.txt,.pdf,text/plain,text/markdown,application/pdf"
              onChange={(event) => {
                void onUpload(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </label>
          <div className="pipeline">
            <div className={`step ${stage === "search" ? "active" : ""}`}>
              <b>1 · Retrieve</b>
              Embed the question, search Chroma
            </div>
            <div className={`step ${stage === "generate" ? "active" : ""}`}>
              <b>2 · Generate</b>
              Stream the answer from cited chunks
            </div>
            <div className={`step ${sources.length ? "active" : ""}`}>
              <b>3 · Inspect</b>
              {sources.length ? `${sources.length} passages` : "Distances appear here"}
            </div>
          </div>
        </header>
        <section className="messages">
          {messages.length === 0 ? (
            <div className="empty">
              <h2>Ask the corpus.</h2>
              <p className="error" hidden={!error}>
                {error}
              </p>
              <div className="prompts">
                {SUGGESTIONS.map((item) => (
                  <button key={item.title} className="prompt" onClick={() => void send(item.title)}>
                    {item.title}
                    <span>{item.detail}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <article key={message.id} className={`bubble ${message.role}`}>
                {message.role === "assistant" ? (
                  <Markdown>{message.content || (streaming ? "Retrieving…" : "")}</Markdown>
                ) : (
                  message.content
                )}
              </article>
            ))
          )}
          <div ref={bottomRef} />
        </section>
        <form className="composer" onSubmit={onSubmit}>
          <textarea
            value={draft}
            placeholder="Ask a question grounded in the indexed documents…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(draft);
              }
            }}
          />
          <button className="send" disabled={streaming || !draft.trim()} type="submit">
            {streaming ? "…" : "Ask"}
          </button>
        </form>
        {error && messages.length > 0 ? <p className="error" style={{ padding: "0 16px 12px" }}>{error}</p> : null}
      </main>

      <aside className="panel sources">
        <div>
          <div className="section-label">Retrieval inspector</div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            This is the passage Chroma put in {"<context>"}. Demo Note is short, so
            one chunk is the whole current file — Northstar, office hours, 7-day
            refund. Lower d= is closer.
          </p>
        </div>
        <div className="stack">
          {sources.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>
              Ask a question. The matching Demo Note passage lands here.
            </p>
          ) : (
            sources.map((chunk) => (
              <article className="source-card" key={`${chunk.document_id}-${chunk.chunk_index}`}>
                <header>
                  <strong>{displayTitle(chunk.document_title)}</strong>
                  <span className="score">
                    chunk {chunk.chunk_index + 1} · d={chunk.distance.toFixed(3)}
                  </span>
                </header>
                <p>{chunk.text}</p>
              </article>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}
