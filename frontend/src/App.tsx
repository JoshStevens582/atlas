import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  deleteDocument,
  fetchDocuments,
  fetchHealth,
  fetchThread,
  fetchThreads,
  hasSession,
  forgotPassword,
  login,
  loginAsDemo,
  logout,
  resetPassword,
  resendVerification,
  signup,
  verifyEmail,
  streamChat,
  uploadDocument,
  waitForIngestJob,
} from "./api";
import type {
  AnswerCheck,
  AnswerVerdict,
  ChatMessage,
  HealthStatus,
  IndexedDocument,
  RetrievedChunk,
  ThreadSummary,
  ToolCall,
} from "./types";

const CHECK_LABELS: Record<AnswerVerdict, string> = {
  supported: "Answer backed by the sources",
  partly_supported: "Answer only partly backed by the sources",
  not_supported: "Answer not backed by the sources",
};

function displayTitle(title: string): string {
  return title.replace(/^\d+\s+/, "");
}

const MATCH_LABELS: Record<NonNullable<RetrievedChunk["match"]>, string> = {
  vector: "Found by meaning",
  lexical: "Found by keywords",
  both: "Found by meaning and keywords",
};

function describeSource(chunk: RetrievedChunk, partCount: number | undefined): string {
  const position = chunk.chunk_index + 1;
  const parts = [
    partCount
      ? `Part ${position} of ${partCount} in ${displayTitle(chunk.document_title)}`
      : `Part ${position} of ${displayTitle(chunk.document_title)}`,
  ];
  if (chunk.match) {
    parts.push(MATCH_LABELS[chunk.match]);
  }
  if (chunk.cited === true) {
    parts.push("Used in the answer");
  } else if (chunk.cited === false) {
    parts.push("Not used in the answer");
  }
  return parts.join(" · ");
}

function sourceDetail(chunk: RetrievedChunk): string {
  return `Technical detail: chunk ${chunk.chunk_index} (counting from 0) · meaning-search distance ${chunk.distance.toFixed(3)} (lower is closer)`;
}

const SUGGESTIONS = [
  {
    title: "How many annual leave hours can I carry over?",
    detail: "Handbook text. Answer is cited from the Leave page.",
  },
  {
    title: "I have 182 annual leave hours, 5 years of service and 25 pay periods left. How many hours would I lose?",
    detail: "A calculation. Calls the estimate_annual_leave tool, then cites the carry-over rule.",
  },
  {
    title: "Which day is Independence Day observed in 2026?",
    detail: "A date. Calls the get_federal_holidays tool.",
  },
];

function queryTokenFromLocation(name: "reset_token" | "verify_token"): string | null {
  return new URLSearchParams(window.location.search).get(name);
}

function stripQueryParam(name: string): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has(name)) {
    return;
  }
  url.searchParams.delete(name);
  const query = url.searchParams.toString();
  window.history.replaceState({}, "", query ? `${url.pathname}?${query}` : url.pathname);
}

export default function App() {
  const [signedIn, setSignedIn] = useState(() => {
    if (queryTokenFromLocation("reset_token") || queryTokenFromLocation("verify_token")) {
      logout();
      return false;
    }
    return hasSession();
  });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<
    "login" | "signup" | "forgot" | "reset" | "verify" | "pending_verify"
  >(() => {
    if (queryTokenFromLocation("verify_token")) {
      return "verify";
    }
    if (queryTokenFromLocation("reset_token")) {
      return "reset";
    }
    return "login";
  });
  const [email, setEmail] = useState("");
  const [resetToken, setResetToken] = useState(
    () => queryTokenFromLocation("reset_token") ?? "",
  );
  const [verifyToken, setVerifyToken] = useState(
    () => queryTokenFromLocation("verify_token") ?? "",
  );
  const [forgotNotice, setForgotNotice] = useState<string | null>(null);
  const [verifyAutoRunning, setVerifyAutoRunning] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<RetrievedChunk[]>([]);
  const [answerCheck, setAnswerCheck] = useState<AnswerCheck | null>(null);
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
    const params = new URLSearchParams(window.location.search);
    const resetFromUrl = params.get("reset_token");
    if (resetFromUrl) {
      logout();
      setResetToken(resetFromUrl);
      setAuthMode("reset");
      setSignedIn(false);
      setError(null);
      return;
    }
    const verifyFromUrl = params.get("verify_token")?.trim();
    if (!verifyFromUrl) {
      return;
    }
    logout();
    setVerifyToken(verifyFromUrl);
    setAuthMode("verify");
    setSignedIn(false);
    setError(null);

    let cancelled = false;
    setVerifyAutoRunning(true);

    void (async () => {
      try {
        await verifyEmail(verifyFromUrl);
        if (cancelled) {
          return;
        }
        stripQueryParam("verify_token");
        setVerifyToken("");
        setAuthMode("login");
        setForgotNotice("Email verified. You can sign in now.");
      } catch (reason) {
        if (cancelled) {
          return;
        }
        setError(reason instanceof Error ? reason.message : "Verification failed.");
      } finally {
        if (!cancelled) {
          setVerifyAutoRunning(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

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
    setAnswerCheck(null);
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
    setAnswerCheck(null);
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
        if (event.type === "check") {
          setAnswerCheck({ verdict: event.verdict, reason: event.reason });
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
      if (authMode === "signup") {
        const result = await signup(username.trim(), password, email);
        setPassword("");
        setEmail("");
        if (result.access_token) {
          setSignedIn(true);
          return;
        }
        setForgotNotice(
          result.message ??
            "Account created. Check your email for a verification link before signing in.",
        );
        if (result.dev_verify_token) {
          setVerifyToken(result.dev_verify_token);
          setAuthMode("verify");
        } else {
          setAuthMode("pending_verify");
        }
        return;
      }
      if (authMode === "verify") {
        await verifyEmail(verifyToken.trim());
        stripQueryParam("verify_token");
        setAuthMode("login");
        setVerifyToken("");
        setForgotNotice("Email verified. You can sign in now.");
        return;
      }
      if (authMode === "pending_verify") {
        const result = await resendVerification(username.trim());
        setForgotNotice(result.message);
        if (result.dev_verify_token) {
          setVerifyToken(result.dev_verify_token);
          setAuthMode("verify");
        }
        return;
      }
      if (authMode === "forgot") {
        const result = await forgotPassword(username.trim());
        setForgotNotice(result.message);
        if (result.dev_reset_token) {
          setResetToken(result.dev_reset_token);
          setAuthMode("reset");
        }
        return;
      }
      if (authMode === "reset") {
        await resetPassword(resetToken.trim(), password.trim());
        setAuthMode("login");
        setResetToken("");
        setPassword("");
        setForgotNotice("Password updated. Sign in with your new password.");
        return;
      }
      await login(username.trim(), password);
      setPassword("");
      setSignedIn(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed.");
    }
  }

  function authHeading(): string {
    if (authMode === "signup") {
      return "Create an account";
    }
    if (authMode === "forgot") {
      return "Reset your password";
    }
    if (authMode === "reset") {
      return "Choose a new password";
    }
    if (authMode === "verify") {
      return "Verify your email";
    }
    if (authMode === "pending_verify") {
      return "Verify your email to sign in";
    }
    return "Sign in to your threads";
  }

  async function onDemoLogin() {
    setError(null);
    setDemoLoading(true);
    try {
      await loginAsDemo();
      setSignedIn(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Demo login failed.");
    } finally {
      setDemoLoading(false);
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
    setAnswerCheck(null);
    setToolCalls([]);
  }

  async function onUpload(file: File | undefined) {
    if (!file) {
      return;
    }
    setError(null);
    try {
      const accepted = await uploadDocument(file);
      if ("job_id" in accepted) {
        const finished = await waitForIngestJob(accepted.job_id);
        if (finished.status === "failed") {
          throw new Error(finished.error ?? "Document indexing failed.");
        }
      }
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
            <span>{authHeading()}</span>
          </div>
          {authMode === "login" || authMode === "signup" ? (
            <>
              <button
                type="button"
                className="new-chat"
                onClick={() => void onDemoLogin()}
                disabled={demoLoading}
              >
                {demoLoading ? "Signing in..." : "Continue as demo user"}
              </button>
              <p className="login-copy">
                No signup needed — one click, browses as a real seeded account.
                Each user only sees their own threads.
              </p>
            </>
          ) : null}
          {authMode === "login" ||
          authMode === "signup" ||
          authMode === "forgot" ||
          authMode === "pending_verify" ? (
            <label>
              Username
              <input
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
          ) : null}
          {authMode === "verify" ? (
            <label>
              Verification token
              <input
                value={verifyToken}
                onChange={(event) => setVerifyToken(event.target.value)}
              />
            </label>
          ) : null}
          {authMode === "signup" ? (
            <label>
              Email (required — we send a verification link before you can sign in)
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
          ) : null}
          {authMode === "reset" ? (
            <label>
              Reset token
              <input
                value={resetToken}
                onChange={(event) => setResetToken(event.target.value)}
              />
            </label>
          ) : null}
          {authMode !== "forgot" && authMode !== "verify" && authMode !== "pending_verify" ? (
            <label>
              Password
              <input
                type="password"
                autoComplete={
                  authMode === "signup" || authMode === "reset" ? "new-password" : "current-password"
                }
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
          ) : null}
          {authMode === "signup" ? (
            <p className="login-copy">
              Passwords are bcrypt-hashed — 8 characters minimum. Check your inbox after signup to
              verify your email.
            </p>
          ) : null}
          {authMode === "forgot" ? (
            <p className="login-copy">
              Enter your username. If the account has an email on file, we send a reset link.
            </p>
          ) : null}
          {authMode === "pending_verify" ? (
            <p className="login-copy">
              Enter your username and we resend the verification link if the account still needs it.
            </p>
          ) : null}
          {authMode === "verify" ? (
            <p className="login-copy">
              {verifyAutoRunning
                ? "Verifying your email…"
                : "Open the link from your email, or paste the token here."}
            </p>
          ) : null}
          {forgotNotice ? <p className="login-copy">{forgotNotice}</p> : null}
          {error ? <p className="error">{error}</p> : null}
          <button
            className="send"
            disabled={
              authMode === "forgot" || authMode === "pending_verify"
                ? !username.trim()
                : authMode === "verify"
                  ? verifyAutoRunning || !verifyToken.trim()
                  : authMode === "reset"
                    ? !resetToken.trim() || !password
                    : authMode === "signup"
                      ? !username.trim() || !password || !email.trim().includes("@")
                      : !username.trim() || !password
            }
            type="submit"
          >
            {authMode === "signup"
              ? "Create account"
              : authMode === "forgot"
                ? "Send reset link"
                : authMode === "reset"
                  ? "Update password"
                  : authMode === "verify"
                    ? verifyAutoRunning
                      ? "Verifying…"
                      : "Verify email"
                    : authMode === "pending_verify"
                      ? "Resend verification"
                      : "Sign in"}
          </button>
          {authMode === "login" ? (
            <>
              <button
                type="button"
                className="new-chat"
                onClick={() => {
                  setError(null);
                  setForgotNotice(null);
                  setAuthMode("forgot");
                }}
              >
                Forgot password?
              </button>
              <button
                type="button"
                className="new-chat"
                onClick={() => {
                  setError(null);
                  setForgotNotice(null);
                  setAuthMode("pending_verify");
                }}
              >
                Resend verification email
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="new-chat"
            onClick={() => {
              setError(null);
              if (authMode === "login") {
                setAuthMode("signup");
                return;
              }
              if (authMode === "signup") {
                setAuthMode("login");
                return;
              }
              setForgotNotice(null);
              setAuthMode("login");
            }}
          >
            {authMode === "signup"
              ? "Have an account? Sign in"
              : authMode === "login"
                ? "New here? Create an account"
                : "Back to sign in"}
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
          <span
            className={`pill ${
              health === null ? "" : health.openai_configured ? "ok" : "warn"
            }`}
          >
            {health === null
              ? "Checking…"
              : health.openai_configured
                ? "OpenAI ready"
                : "API key missing"}
          </span>
          <span className="pill ok">ChromaDB</span>
          <span className="pill">{health?.document_count ?? 0} docs</span>
          <span className="pill">{health?.chunk_count ?? 0} chunks</span>
        </div>
        <button className="new-chat" onClick={() => { setThreadId(null); setMessages([]); setSources([]); setAnswerCheck(null); setToolCalls([]); }}>
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
                  {document.retrieval_check ? (
                    <>
                      <br />
                      <small
                        title="Questions written from this document's own passages. How many found their passage when searched."
                      >
                        Self-test: {document.retrieval_check.hits}/{document.retrieval_check.total} found
                      </small>
                    </>
                  ) : null}
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
          <p>Ask the TTS Handbook (US government, public domain). Two tools do the leave and holiday calculations. Sources used shows both.</p>
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
            The passages Atlas found for your question, best match first, plus
            any tool results. A [1] in the answer means that sentence came from
            card 1. This panel only shows them. It does not search.
          </p>
        </div>
        {answerCheck ? (
          <div className={`answer-check ${answerCheck.verdict}`}>
            <strong>{CHECK_LABELS[answerCheck.verdict]}</strong>
            <p>{answerCheck.reason}</p>
            <span>Second-model check of this answer against the sources. It can be wrong.</span>
          </div>
        ) : null}
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
              Ask a handbook question or a leave or holiday calculation. Hits and tool results land here.
            </p>
          ) : (
            sources.map((chunk, index) => {
              const citeN = chunk.cite_n && chunk.cite_n > 0 ? chunk.cite_n : index + 1;
              const unused = chunk.cited === false;
              return (
                <article
                  className={unused ? "source-card unused" : "source-card"}
                  key={`${chunk.document_id}-${chunk.chunk_index}`}
                >
                  <header>
                    <strong>
                      [{citeN}] {displayTitle(chunk.document_title)}
                    </strong>
                  </header>
                  <p className="source-meta" title={sourceDetail(chunk)}>
                    {describeSource(
                      chunk,
                      documents.find((item) => item.id === chunk.document_id)?.chunk_count,
                    )}
                  </p>
                  <p>{chunk.text}</p>
                </article>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
