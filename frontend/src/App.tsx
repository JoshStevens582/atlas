import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  clearUserMfa,
  completeMfaLogin,
  confirmMfaSetup,
  deleteDocument,
  disableMfa,
  regenerateRecoveryCodes,
  disableUser,
  enableUser,
  fetchAdminUsers,
  fetchDocuments,
  fetchHealth,
  fetchMe,
  fetchThread,
  fetchThreads,
  forgotPassword,
  hasSession,
  login,
  loginAsDemo,
  logout,
  resendVerification,
  startSessionMaintenance,
  resetPassword,
  setUserRole,
  signup,
  startMfaSetup,
  streamChat,
  uploadDocument,
  verifyEmail,
  waitForIngestJob,
  type LoginResult,
  type MfaSetup,
} from "./api";
import { copyRecoveryCodes, downloadRecoveryCodes } from "./mfaRecoveryCodes";
import type {
  AdminUser,
  AnswerCheck,
  AnswerVerdict,
  ChatMessage,
  HealthStatus,
  IndexedDocument,
  RetrievedChunk,
  ThreadSummary,
  ToolCall,
  UserRole,
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
    title: "How many days of annual leave can I carry over?",
    detail: "Staff handbook — cited from the leave page.",
  },
  {
    title: "What is the standard patient session fee?",
    detail: "Should answer R650 from the fees page.",
  },
  {
    title: "Can Counter book appointments for patients?",
    detail: "Reception and bookings — Counter answers only.",
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
    "login" | "signup" | "forgot" | "reset" | "verify" | "pending_verify" | "mfa"
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
  const [role, setRole] = useState<UserRole>("user");
  const [signedInUsername, setSignedInUsername] = useState("");
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [securityOpen, setSecurityOpen] = useState(false);
  const [mfaEnabled, setMfaEnabled] = useState(false);
  const [mfaToken, setMfaToken] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPassword, setMfaPassword] = useState("");
  const [mfaActionCode, setMfaActionCode] = useState("");
  const [mfaSetup, setMfaSetup] = useState<MfaSetup | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [recoveryCodesAck, setRecoveryCodesAck] = useState(false);
  const [recoveryCodesExported, setRecoveryCodesExported] = useState(false);
  const [securityNotice, setSecurityNotice] = useState<string | null>(null);
  const [mfaBusy, setMfaBusy] = useState(false);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
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

  const isAdmin = role === "admin";

  useEffect(() => {
    if (!signedIn) {
      return;
    }
    return startSessionMaintenance(() => {
      setSignedIn(false);
    });
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn) {
      return;
    }
    void fetchMe()
      .then((me) => {
        setRole(me.role);
        setSignedInUsername(me.username);
        setMfaEnabled(me.mfa_enabled);
      })
      .catch((reason: Error) => {
        if (reason.message === "Not authenticated." || reason.message === "This account is disabled.") {
          logout();
          setSignedIn(false);
          return;
        }
        setError(reason.message);
      });
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
      if (authMode === "mfa") {
        await completeMfaLogin(mfaToken, mfaCode.trim());
        setMfaCode("");
        setMfaToken("");
        setAuthMode("login");
        setSignedIn(true);
        return;
      }
      finishPasswordStep(await login(username.trim(), password));
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
    if (authMode === "mfa") {
      return "Authenticator code";
    }
    return "Sign in to your threads";
  }

  function finishPasswordStep(result: LoginResult): void {
    setPassword("");
    if (result.username) {
      setUsername(result.username);
    }
    if (result.mfaRequired) {
      if (!result.mfaToken) {
        throw new Error("Authenticator code required, but no sign-in step was issued.");
      }
      setMfaToken(result.mfaToken);
      setMfaCode("");
      setAuthMode("mfa");
      return;
    }
    setSignedIn(true);
  }

  async function onDemoLogin() {
    setError(null);
    setDemoLoading(true);
    try {
      finishPasswordStep(await loginAsDemo());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Demo login failed.");
    } finally {
      setDemoLoading(false);
    }
  }

  function onLogout() {
    logout();
    setSignedIn(false);
    setAuthMode("login");
    setMfaToken("");
    setMfaCode("");
    setRole("user");
    setSignedInUsername("");
    setPeopleOpen(false);
    setSecurityOpen(false);
    setMfaEnabled(false);
    setMfaSetup(null);
    setRecoveryCodes(null);
    setMfaPassword("");
    setMfaActionCode("");
    setSecurityNotice(null);
    setAdminUsers([]);
    setThreadId(null);
    setMessages([]);
    setThreads([]);
    setDocuments([]);
    setSources([]);
    setAnswerCheck(null);
    setToolCalls([]);
  }

  async function refreshAdminUsers() {
    const nextUsers = await fetchAdminUsers();
    setAdminUsers(nextUsers);
  }

  async function onOpenPeople() {
    setError(null);
    setPeopleOpen(true);
    try {
      await refreshAdminUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load people.");
    }
  }

  async function onChangeRole(username: string, nextRole: UserRole) {
    setError(null);
    try {
      await setUserRole(username, nextRole);
      await refreshAdminUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not change role.");
    }
  }

  async function onToggleDisabled(person: AdminUser) {
    setError(null);
    try {
      if (person.disabled) {
        await enableUser(person.username);
      } else {
        await disableUser(person.username);
      }
      await refreshAdminUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update account.");
    }
  }

  function onOpenSecurity() {
    setError(null);
    setPeopleOpen(false);
    setSecurityOpen(true);
  }

  async function onStartMfa() {
    setError(null);
    setSecurityNotice(null);
    setMfaBusy(true);
    try {
      const setup = await startMfaSetup(mfaPassword);
      setMfaSetup(setup);
      setMfaPassword("");
      setMfaActionCode("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start authenticator setup.");
    } finally {
      setMfaBusy(false);
    }
  }

  function showRecoveryCodes(codes: string[]) {
    setRecoveryCodes(codes);
    setRecoveryCodesAck(false);
    setRecoveryCodesExported(false);
  }

  function dismissRecoveryCodes() {
    setRecoveryCodes(null);
    setRecoveryCodesAck(false);
    setRecoveryCodesExported(false);
  }

  async function onConfirmMfa() {
    setError(null);
    setMfaBusy(true);
    try {
      const codes = await confirmMfaSetup(mfaActionCode.trim());
      showRecoveryCodes(codes);
      setMfaSetup(null);
      setMfaActionCode("");
      setMfaEnabled(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That code did not match.");
    } finally {
      setMfaBusy(false);
    }
  }

  async function onRegenerateRecoveryCodes() {
    setError(null);
    setSecurityNotice(null);
    setMfaBusy(true);
    try {
      const codes = await regenerateRecoveryCodes(mfaPassword, mfaActionCode.trim());
      showRecoveryCodes(codes);
      setMfaPassword("");
      setMfaActionCode("");
      setSecurityNotice("New recovery codes issued. Old unused codes no longer work.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not generate new recovery codes.",
      );
    } finally {
      setMfaBusy(false);
    }
  }

  async function onCopyRecoveryCodes() {
    if (!recoveryCodes) {
      return;
    }
    setError(null);
    try {
      await copyRecoveryCodes(recoveryCodes);
      setRecoveryCodesExported(true);
    } catch {
      setError("Could not copy to the clipboard. Use Download instead.");
    }
  }

  function onDownloadRecoveryCodes() {
    if (!recoveryCodes) {
      return;
    }
    downloadRecoveryCodes(recoveryCodes, signedInUsername || "account");
    setRecoveryCodesExported(true);
  }

  async function onDisableMfa() {
    setError(null);
    setSecurityNotice(null);
    setMfaBusy(true);
    try {
      const message = await disableMfa(mfaPassword, mfaActionCode.trim());
      setMfaEnabled(false);
      setMfaPassword("");
      setMfaActionCode("");
      setSecurityNotice(message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not turn authenticator off.");
    } finally {
      setMfaBusy(false);
    }
  }

  async function onClearUserMfa(person: AdminUser) {
    setError(null);
    try {
      await clearUserMfa(person.username);
      await refreshAdminUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not turn authenticator off.");
    }
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
          {authMode === "mfa" ? (
            <label>
              6-digit code from Google or Microsoft Authenticator
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="e.g. 482910"
                value={mfaCode}
                onChange={(event) => setMfaCode(event.target.value)}
              />
            </label>
          ) : null}
          {authMode !== "forgot" &&
          authMode !== "verify" &&
          authMode !== "pending_verify" &&
          authMode !== "mfa" ? (
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
          {authMode === "mfa" ? (
            <p className="login-copy">
              Password matched for {username.trim() || "this account"}. This step is{" "}
              <strong>not</strong> your eight backup codes from setup — open the{" "}
              <strong>Authenticator app</strong>, tap Atlas, and type the <strong>6 digits</strong>{" "}
              shown there (they change every ~30 seconds). Only if you lost that app, paste one saved
              backup code instead (with or without the hyphen). Locked out? An admin clears
              authenticator on People.
            </p>
          ) : null}
          {forgotNotice ? <p className="login-copy">{forgotNotice}</p> : null}
          {error ? <p className="error">{error}</p> : null}
          <button
            className="send"
            disabled={
              authMode === "mfa"
                ? mfaBusy || !mfaCode.trim()
                : authMode === "forgot" || authMode === "pending_verify"
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
                    : authMode === "mfa"
                      ? "Confirm code"
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
          <span>Cape Town Physio · staff handbook</span>
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
        <button className="new-chat" onClick={() => { setPeopleOpen(false); setSecurityOpen(false); setThreadId(null); setMessages([]); setSources([]); setAnswerCheck(null); setToolCalls([]); }}>
          New chat
        </button>
        <button className="new-chat" onClick={onLogout} type="button">
          Sign out
        </button>
        <p className="suite-links">
          <a href="https://counter.customchatandautomation.co.za" target="_blank" rel="noreferrer">
            Counter
          </a>
          {" · "}
          <a href="https://agent.customchatandautomation.co.za" target="_blank" rel="noreferrer">
            Clerk
          </a>
        </p>
        <button
          className={`new-chat ${securityOpen ? "active" : ""}`}
          onClick={onOpenSecurity}
          type="button"
        >
          Security
        </button>
        {isAdmin ? (
          <button
            className={`new-chat ${peopleOpen ? "active" : ""}`}
            onClick={() => {
              setSecurityOpen(false);
              void onOpenPeople();
            }}
            type="button"
          >
            People
          </button>
        ) : null}
        {isAdmin ? (
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
        ) : null}
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
                {isAdmin ? (
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
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </aside>

      <main className="panel chat">
        {peopleOpen && isAdmin ? (
          <section className="people-panel">
            <header className="chat-header">
              <h1>People</h1>
              <p>
                Admins can promote, demote, or disable accounts. Threads stay private —
                this list is accounts, not chats. Signed in as {signedInUsername}.
              </p>
              <button className="new-chat" type="button" onClick={() => setPeopleOpen(false)}>
                Back to chat
              </button>
            </header>
            {error ? <p className="error" style={{ padding: "0 16px" }}>{error}</p> : null}
            <div className="people-list">
              {adminUsers.map((person) => (
                <article className="people-row" key={person.username}>
                  <div>
                    <strong>{person.username}</strong>
                    <span>
                      {person.role}
                      {person.disabled ? " · disabled" : ""}
                      {person.email ? ` · ${person.email}` : ""}
                      {person.email && !person.email_verified ? " (unverified)" : ""}
                      {person.mfa_enabled ? " · authenticator" : ""}
                    </span>
                  </div>
                  <div className="people-actions">
                    {person.username !== signedInUsername ? (
                      <>
                        <button
                          className="pill"
                          type="button"
                          onClick={() =>
                            void onChangeRole(
                              person.username,
                              person.role === "admin" ? "user" : "admin",
                            )
                          }
                        >
                          {person.role === "admin" ? "Make user" : "Make admin"}
                        </button>
                        <button
                          className="pill"
                          type="button"
                          onClick={() => void onToggleDisabled(person)}
                        >
                          {person.disabled ? "Enable" : "Disable"}
                        </button>
                        {person.mfa_enabled ? (
                          <button
                            className="pill"
                            type="button"
                            onClick={() => void onClearUserMfa(person)}
                          >
                            Clear authenticator
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <span className="pill ok">you</span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        ) : securityOpen ? (
          <section className="people-panel">
            <header className="chat-header">
              <h1>Security</h1>
              <p>
                Password is the first check. An authenticator app is the second. Demo sign-in stays
                one click until you turn this on for that account.
              </p>
              <button className="new-chat" type="button" onClick={() => setSecurityOpen(false)}>
                Back to chat
              </button>
            </header>
            {error ? <p className="error" style={{ padding: "0 16px" }}>{error}</p> : null}
            {securityNotice ? <p className="login-copy" style={{ padding: "0 16px" }}>{securityNotice}</p> : null}
            <div className="people-list">
              {recoveryCodes ? (
                <div className="mfa-setup">
                  <p className="mfa-scan-warning">
                    Atlas does not store these for you. Copy or download them now. Without your
                    phone app <em>and</em> without these codes, you cannot sign in until an admin
                    clears authenticator on your account.
                  </p>
                  <p className="login-copy">
                    Each recovery code works once if you lose the authenticator app. Atlas will not
                    show this list again.
                  </p>
                  <ul className="recovery-codes">
                    {recoveryCodes.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                  <div className="recovery-actions">
                    <button className="pill" type="button" onClick={() => void onCopyRecoveryCodes()}>
                      Copy all
                    </button>
                    <button className="pill" type="button" onClick={onDownloadRecoveryCodes}>
                      Download .txt
                    </button>
                  </div>
                  <label className="recovery-ack">
                    <input
                      type="checkbox"
                      checked={recoveryCodesAck}
                      onChange={(event) => setRecoveryCodesAck(event.target.checked)}
                    />
                    <span>
                      I copied or downloaded these codes and stored them somewhere safe (password
                      manager, printout, or secure note).
                    </span>
                  </label>
                  <button
                    className="send"
                    type="button"
                    disabled={!recoveryCodesAck || !recoveryCodesExported}
                    onClick={dismissRecoveryCodes}
                  >
                    Continue
                  </button>
                  {!recoveryCodesExported ? (
                    <p className="login-copy">Use Copy all or Download before you continue.</p>
                  ) : null}
                </div>
              ) : mfaSetup ? (
                <div className="mfa-setup">
                  <p className="mfa-scan-warning">
                    On your phone, install <strong>Google Authenticator</strong> or{" "}
                    <strong>Microsoft Authenticator</strong>. Open that app, choose{" "}
                    <strong>Scan QR code</strong>, and point it at this screen. Do not scan with
                    Samsung Notes, the camera roll, or a generic QR app — those save a link and will
                    not give you login codes.
                  </p>
                  <img className="mfa-qr" src={mfaSetup.qr_data_uri} alt="Authenticator QR code" />
                  <p className="login-copy">
                    Or in the authenticator app choose enter setup key manually and paste this
                    secret. Then enter the 6-digit code from the app below.
                  </p>
                  <p className="mfa-secret">{mfaSetup.secret}</p>
                  <button
                    className="pill"
                    type="button"
                    onClick={() => void navigator.clipboard.writeText(mfaSetup.secret)}
                  >
                    Copy secret
                  </button>
                  <label>
                    Code from the app
                    <input
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      value={mfaActionCode}
                      onChange={(event) => setMfaActionCode(event.target.value)}
                    />
                  </label>
                  <button
                    className="send"
                    type="button"
                    disabled={mfaBusy || mfaActionCode.trim().length < 6}
                    onClick={() => void onConfirmMfa()}
                  >
                    Turn on authenticator
                  </button>
                </div>
              ) : mfaEnabled ? (
                <div className="mfa-setup">
                  <p className="login-copy">
                    Authenticator is on. Every sign-in needs your password plus a 6-digit code from
                    the app on your phone (or a recovery code you saved).
                  </p>
                  <label>
                    Password
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={mfaPassword}
                      onChange={(event) => setMfaPassword(event.target.value)}
                    />
                  </label>
                  <label>
                    Authenticator or recovery code
                    <input
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      value={mfaActionCode}
                      onChange={(event) => setMfaActionCode(event.target.value)}
                    />
                  </label>
                  <button
                    className="pill"
                    type="button"
                    disabled={mfaBusy || !mfaPassword || mfaActionCode.trim().length < 6}
                    onClick={() => void onRegenerateRecoveryCodes()}
                  >
                    Generate new recovery codes
                  </button>
                  <p className="login-copy">
                    New backup codes need the 6-digit app code (not a recovery code). Turning off
                    accepts either.
                  </p>
                  <button
                    className="send"
                    type="button"
                    disabled={mfaBusy || !mfaPassword || mfaActionCode.trim().length < 6}
                    onClick={() => void onDisableMfa()}
                  >
                    Turn off authenticator
                  </button>
                </div>
              ) : (
                <div className="mfa-setup">
                  <p className="login-copy">
                    Enter your password to start. A stolen session cannot turn this on by itself.
                  </p>
                  <label>
                    Password
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={mfaPassword}
                      onChange={(event) => setMfaPassword(event.target.value)}
                    />
                  </label>
                  <button
                    className="send"
                    type="button"
                    disabled={mfaBusy || !mfaPassword}
                    onClick={() => void onStartMfa()}
                  >
                    Set up authenticator
                  </button>
                </div>
              )}
            </div>
          </section>
        ) : (
          <>
        <header className="chat-header">
          <h1>{selectedTitle}</h1>
          <p>Ask the Cape Town Physio staff handbook. Answers cite the page they came from.</p>
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
          </>
        )}
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
              Ask a handbook question. Retrieved passages land here.
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
