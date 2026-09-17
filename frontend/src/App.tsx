import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  deleteDocument,
  fetchDocuments,
  fetchHealth,
  fetchThread,
  fetchThreads,
  hasSession,
  login,
  logout,
  streamChat,
  uploadDocument,
} from "./api";
import type {
  ChatMessage,
  HealthStatus,
  IndexedDocument,
  RetrievedChunk,
  ThreadSummary,
  ToolCall,
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
    title: "List all support tickets",
    detail: "Not in the handbook — should call get_support_ticket with no id.",
  },
];

export default function App() {
  const [signedIn, setSignedIn] = useState(hasSession());
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<RetrievedChunk[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [stage, setStage] = useState<"idle" | "search" | "tool" | "generate">("idle");
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
    if (!signedIn) {
      return;
    }
    refreshLists().catch((reason: Error) => {
      if (reason.message === "Not authenticated.") {
        setSignedIn(false);
        return;
      }
      setError(reason.message);
    });
  }, [signedIn]);

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
    setToolCalls([]);
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
    setToolCalls([]);
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
        if (event.type === "tool") {
          setToolCalls((current) => [
            ...current,
            {
              name: event.name,
              arguments: event.arguments,
              result: event.result,
            },
          ]);
          setStage("tool");
        }
        if (event.type === "token") {
          setStage("generate");
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
      const message = reason instanceof Error ? reason.message : "Chat failed.";
      if (message === "Not authenticated.") {
        setSignedIn(false);
      }
      setError(message);
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

  async function onLogin(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await login(username.trim(), password);
      setPassword("");
      setSignedIn(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Login failed.");
    }
  }

  function onLogout() {
    logout();
    setSignedIn(false);
    setThreadId(null);
    setMessages([]);
    setThreads([]);
    setDocuments([]);
    setSources([]);
    setToolCalls([]);
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

  if (!signedIn) {
    return (
      <div className="login-screen">
        <form className="panel login-card" onSubmit={(event) => void onLogin(event)}>
          <div className="brand">
            <strong>ATLAS</strong>
            <span>Sign in to your threads</span>
          </div>
          <p className="login-copy">
            Demo logins: alice / atlas-alice or bob / atlas-bob. Each user only sees their own threads.
          </p>
          <label>
            Username
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button className="send" disabled={!username.trim() || !password} type="submit">
            Sign in
          </button>
        </form>
      </div>
    );
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
        <button className="new-chat" onClick={() => { setThreadId(null); setMessages([]); setSources([]); setToolCalls([]); }}>
          New chat
        </button>
        <button className="new-chat" onClick={onLogout} type="button">
          Sign out
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
        <div className="library-block">
          <div className="section-label">Library</div>
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
          <p>Retrieve from the handbook. Tools for live tickets. Sources used shows both.</p>
          <div className="pipeline">
            <div className={`step ${stage === "search" ? "active" : ""}`}>
              <b>1 · Retrieve</b>
              Embed the question, search Chroma
            </div>
            <div className={`step ${stage === "tool" ? "active" : ""}`}>
              <b>2 · Tool</b>
              {toolCalls.length
                ? `${toolCalls.length} function call${toolCalls.length === 1 ? "" : "s"}`
                : "Model may ask Atlas to run a function"}
            </div>
            <div className={`step ${stage === "generate" ? "active" : ""}`}>
              <b>3 · Generate</b>
              Stream the answer
            </div>
          </div>
        </header>
        <section className="messages">
          {messages.length === 0 ? (
            <div className="empty">
              <h2>Ask the library.</h2>
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
          <div className="section-label">Sources used</div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            Chunks retrieve already found, plus any tool JSON. This panel only
            displays. It does not search.
          </p>
        </div>
        <div className="stack">
          {toolCalls.map((call, index) => (
            <article className="source-card tool-card" key={`${call.name}-${index}`}>
              <header>
                <strong>{call.name}</strong>
                <span className="score">tool</span>
              </header>
              <p>
                args {JSON.stringify(call.arguments)}
                <br />
                result {call.result}
              </p>
            </article>
          ))}
          {sources.length === 0 && toolCalls.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>
              Ask a handbook question or ticket T-104. Hits and tool results land here.
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
