import type {
  AdminUser,
  HealthStatus,
  IndexedDocument,
  IngestJob,
  Me,
  StreamEvent,
  ThreadDetail,
  ThreadSummary,
  UserRole,
} from "./types";

const TOKEN_KEY = "atlas_access_token";
const REFRESH_KEY = "atlas_refresh_token";

/** Refresh the access JWT this many seconds before it expires. */
const REFRESH_LEAD_SECONDS = 120;

let refreshInFlight: Promise<boolean> | null = null;
let proactiveRefreshTimer: ReturnType<typeof setTimeout> | null = null;
let onSessionLost: (() => void) | null = null;

function clearProactiveRefreshTimer(): void {
  if (proactiveRefreshTimer !== null) {
    clearTimeout(proactiveRefreshTimer);
    proactiveRefreshTimer = null;
  }
}

function accessTokenExpiresAtMs(accessToken: string): number | null {
  const parts = accessToken.split(".");
  if (parts.length !== 3) {
    return null;
  }
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64)) as { exp?: unknown };
    if (typeof payload.exp === "number") {
      return payload.exp * 1000;
    }
  } catch {
    return null;
  }
  return null;
}

function accessTokenNeedsRefresh(accessToken: string): boolean {
  const expMs = accessTokenExpiresAtMs(accessToken);
  if (expMs === null) {
    return true;
  }
  return expMs - Date.now() <= REFRESH_LEAD_SECONDS * 1000;
}

function scheduleProactiveRefresh(): void {
  clearProactiveRefreshTimer();
  const accessToken = sessionStorage.getItem(TOKEN_KEY);
  if (!accessToken || !sessionStorage.getItem(REFRESH_KEY)) {
    return;
  }
  const expMs = accessTokenExpiresAtMs(accessToken);
  if (expMs === null) {
    return;
  }
  const delayMs = Math.max(expMs - Date.now() - REFRESH_LEAD_SECONDS * 1000, 5_000);
  proactiveRefreshTimer = setTimeout(() => {
    void rotateRefreshCredential(true);
  }, delayMs);
}

function failLocalSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
  clearProactiveRefreshTimer();
  onSessionLost?.();
}

async function rotateRefreshCredential(scheduleNext: boolean): Promise<boolean> {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  const rawRefresh = sessionStorage.getItem(REFRESH_KEY);
  if (!rawRefresh) {
    return false;
  }
  refreshInFlight = (async () => {
    try {
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rawRefresh }),
      });
      if (!response.ok) {
        return false;
      }
      const parsed = (await response.json()) as {
        access_token?: string | null;
        refresh_token?: string | null;
      };
      if (!parsed.access_token) {
        return false;
      }
      storeSessionTokens(parsed.access_token, parsed.refresh_token ?? rawRefresh);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  const ok = await refreshInFlight;
  if (!ok) {
    failLocalSession();
    return false;
  }
  if (scheduleNext) {
    scheduleProactiveRefresh();
  }
  return true;
}

async function ensureFreshAccessToken(): Promise<boolean> {
  const accessToken = sessionStorage.getItem(TOKEN_KEY);
  if (!accessToken) {
    return false;
  }
  if (!accessTokenNeedsRefresh(accessToken)) {
    return true;
  }
  return rotateRefreshCredential(true);
}

async function authorizedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (sessionStorage.getItem(TOKEN_KEY)) {
    const fresh = await ensureFreshAccessToken();
    if (!fresh) {
      return new Response(JSON.stringify({ detail: "Not authenticated." }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
  }
  let response = await fetch(input, {
    ...init,
    headers: authHeaders(init?.headers),
  });
  if (response.status === 401 && sessionStorage.getItem(REFRESH_KEY)) {
    const refreshed = await rotateRefreshCredential(true);
    if (refreshed) {
      response = await fetch(input, {
        ...init,
        headers: authHeaders(init?.headers),
      });
    }
  }
  return response;
}

function storeSessionTokens(accessToken: string, refreshToken: string | null | undefined): void {
  sessionStorage.setItem(TOKEN_KEY, accessToken);
  if (refreshToken) {
    sessionStorage.setItem(REFRESH_KEY, refreshToken);
  } else {
    sessionStorage.removeItem(REFRESH_KEY);
  }
  scheduleProactiveRefresh();
}

/** Call while signed in; clears the timer on cleanup. */
export function startSessionMaintenance(onLost: () => void): () => void {
  onSessionLost = onLost;
  scheduleProactiveRefresh();
  void ensureFreshAccessToken();
  return () => {
    onSessionLost = null;
    clearProactiveRefreshTimer();
  };
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const loc =
            "loc" in item && Array.isArray(item.loc) ? item.loc.filter(Boolean).join(".") : "";
          const msg = String((item as { msg: unknown }).msg);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .join(" ");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return "Request failed.";
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    failLocalSession();
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const message =
      body?.detail !== undefined && body?.detail !== null
        ? formatApiDetail(body.detail)
        : "Not authenticated.";
    throw new Error(message);
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const message =
      body?.detail !== undefined && body?.detail !== null
        ? formatApiDetail(body.detail)
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export function hasSession(): boolean {
  return Boolean(sessionStorage.getItem(TOKEN_KEY));
}

export function logout(): void {
  const refresh = sessionStorage.getItem(REFRESH_KEY);
  clearProactiveRefreshTimer();
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
  if (refresh) {
    void fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
  }
}

export type LoginResult = {
  username: string;
  mfaRequired: boolean;
  mfaToken: string | null;
};

export type MfaSetup = {
  secret: string;
  otpauth_uri: string;
  qr_data_uri: string;
};

async function loginRequest(path: string, body?: unknown): Promise<LoginResult> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await readJson<{
    access_token: string | null;
    refresh_token?: string | null;
    username: string;
    mfa_required?: boolean;
    mfa_token?: string | null;
  }>(response);
  if (parsed.mfa_required) {
    if (!parsed.mfa_token) {
      throw new Error("Authenticator code required, but no sign-in step was issued.");
    }
    return {
      username: parsed.username,
      mfaRequired: true,
      mfaToken: parsed.mfa_token,
    };
  }
  if (!parsed.access_token) {
    throw new Error("Sign-in did not return a session.");
  }
  storeSessionTokens(parsed.access_token, parsed.refresh_token);
  return { username: parsed.username, mfaRequired: false, mfaToken: null };
}

export function login(username: string, password: string): Promise<LoginResult> {
  return loginRequest("/api/auth/login", { username, password });
}

export type SignupResult = {
  username: string;
  message?: string | null;
  access_token?: string | null;
  dev_verify_token?: string | null;
};

export async function signup(
  username: string,
  password: string,
  email: string,
): Promise<SignupResult> {
  const response = await fetch("/api/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, email: email.trim() }),
  });
  return readJson<SignupResult>(response);
}

export async function verifyEmail(token: string): Promise<string> {
  const response = await fetch("/api/auth/verify-email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const parsed = await readJson<{ message: string }>(response);
  return parsed.message;
}

export async function resendVerification(username: string): Promise<{
  message: string;
  dev_verify_token?: string | null;
}> {
  const response = await fetch("/api/auth/resend-verification", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  return readJson<{ message: string; dev_verify_token?: string | null }>(response);
}

export async function forgotPassword(username: string): Promise<{
  message: string;
  dev_reset_token?: string | null;
}> {
  const response = await fetch("/api/auth/forgot-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  return readJson<{ message: string; dev_reset_token?: string | null }>(response);
}

export async function resetPassword(token: string, password: string): Promise<string> {
  const response = await fetch("/api/auth/reset-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
  const parsed = await readJson<{ message: string }>(response);
  return parsed.message;
}

/** One click, no typing: logs in as the seeded demo account. */
export function loginAsDemo(): Promise<LoginResult> {
  return loginRequest("/api/auth/demo");
}

export async function completeMfaLogin(mfaToken: string, code: string): Promise<string> {
  const response = await fetch("/api/auth/login/mfa", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mfa_token: mfaToken, code }),
  });
  const parsed = await readJson<{
    access_token: string | null;
    refresh_token?: string | null;
    username: string;
  }>(response);
  if (!parsed.access_token) {
    throw new Error("Sign-in did not return a session.");
  }
  storeSessionTokens(parsed.access_token, parsed.refresh_token);
  return parsed.username;
}

export async function startMfaSetup(password: string): Promise<MfaSetup> {
  const response = await authorizedFetch("/api/auth/mfa/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  return readJson<MfaSetup>(response);
}

export async function confirmMfaSetup(code: string): Promise<string[]> {
  const response = await authorizedFetch("/api/auth/mfa/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const parsed = await readJson<{ recovery_codes: string[] }>(response);
  return parsed.recovery_codes;
}

export async function disableMfa(password: string, code: string): Promise<string> {
  const response = await authorizedFetch("/api/auth/mfa/disable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password, code }),
  });
  const parsed = await readJson<{ message: string }>(response);
  return parsed.message;
}

export async function regenerateRecoveryCodes(
  password: string,
  code: string,
): Promise<string[]> {
  const response = await authorizedFetch("/api/auth/mfa/recovery-codes/regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password, code }),
  });
  const parsed = await readJson<{ recovery_codes: string[] }>(response);
  return parsed.recovery_codes;
}

export function clearUserMfa(username: string): Promise<AdminUser> {
  return authorizedFetch(`/api/admin/users/${encodeURIComponent(username)}/mfa/reset`, {
    method: "POST",
  }).then((response) => readJson<AdminUser>(response));
}

export function fetchMe(): Promise<Me> {
  return authorizedFetch("/api/auth/me").then((response) => readJson<Me>(response));
}

export function fetchAdminUsers(): Promise<AdminUser[]> {
  return authorizedFetch("/api/admin/users").then((response) =>
    readJson<AdminUser[]>(response),
  );
}

export function setUserRole(username: string, role: UserRole): Promise<AdminUser> {
  return authorizedFetch(`/api/admin/users/${encodeURIComponent(username)}/role`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  }).then((response) => readJson<AdminUser>(response));
}

export function disableUser(username: string): Promise<AdminUser> {
  return authorizedFetch(`/api/admin/users/${encodeURIComponent(username)}/disable`, {
    method: "POST",
  }).then((response) => readJson<AdminUser>(response));
}

export function enableUser(username: string): Promise<AdminUser> {
  return authorizedFetch(`/api/admin/users/${encodeURIComponent(username)}/enable`, {
    method: "POST",
  }).then((response) => readJson<AdminUser>(response));
}

export function fetchHealth(): Promise<HealthStatus> {
  return fetch("/api/health").then((response) => readJson<HealthStatus>(response));
}

export function fetchThreads(): Promise<ThreadSummary[]> {
  return authorizedFetch("/api/threads").then((response) =>
    readJson<ThreadSummary[]>(response),
  );
}

export function fetchThread(threadId: string): Promise<ThreadDetail> {
  return authorizedFetch(`/api/threads/${threadId}`).then((response) =>
    readJson<ThreadDetail>(response),
  );
}

export function fetchDocuments(): Promise<IndexedDocument[]> {
  return authorizedFetch("/api/documents").then((response) =>
    readJson<IndexedDocument[]>(response),
  );
}

export async function uploadDocument(file: File): Promise<IngestJob | IndexedDocument> {
  const data = new FormData();
  data.append("file", file);
  const response = await authorizedFetch("/api/documents/upload", {
    method: "POST",
    body: data,
  });
  if (response.status === 202) {
    return readJson<IngestJob>(response);
  }
  return readJson<IndexedDocument>(response);
}

export async function fetchIngestJob(jobId: string): Promise<IngestJob> {
  return authorizedFetch(`/api/documents/jobs/${jobId}`).then((response) =>
    readJson<IngestJob>(response),
  );
}

export async function waitForIngestJob(
  jobId: string,
  {
    intervalMs = 400,
    timeoutMs = 120_000,
  }: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<IngestJob> {
  const started = Date.now();
  for (;;) {
    const job = await fetchIngestJob(jobId);
    if (job.status === "done" || job.status === "failed") {
      return job;
    }
    if (Date.now() - started > timeoutMs) {
      throw new Error("Timed out waiting for document indexing.");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await authorizedFetch(`/api/documents/${documentId}`, {
    method: "DELETE",
  });
  await readJson<{ status: string }>(response);
}

export async function streamChat(
  message: string,
  threadId: string | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await authorizedFetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  });
  if (response.status === 401) {
    failLocalSession();
    throw new Error("Not authenticated.");
  }
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Chat failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .find((entry) => entry.startsWith("data: "));
      if (!line) {
        continue;
      }
      const event = JSON.parse(line.slice(6)) as StreamEvent;
      onEvent(event);
    }
  }
}
