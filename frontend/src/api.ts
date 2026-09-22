import type {
  HealthStatus,
  IndexedDocument,
  IngestJob,
  StreamEvent,
  ThreadDetail,
  ThreadSummary,
} from "./types";

const TOKEN_KEY = "atlas_access_token";

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    throw new Error("Not authenticated.");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export function hasSession(): boolean {
  return Boolean(sessionStorage.getItem(TOKEN_KEY));
}

export function logout(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

async function loginRequest(path: string, body?: unknown): Promise<string> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await readJson<{ access_token: string; username: string }>(response);
  sessionStorage.setItem(TOKEN_KEY, parsed.access_token);
  return parsed.username;
}

export function login(username: string, password: string): Promise<string> {
  return loginRequest("/api/auth/login", { username, password });
}

export function signup(username: string, password: string): Promise<string> {
  return loginRequest("/api/auth/signup", { username, password });
}

/** One click, no typing: logs in as the seeded demo account. */
export function loginAsDemo(): Promise<string> {
  return loginRequest("/api/auth/demo");
}

export function fetchHealth(): Promise<HealthStatus> {
  return fetch("/api/health").then((response) => readJson<HealthStatus>(response));
}

export function fetchThreads(): Promise<ThreadSummary[]> {
  return fetch("/api/threads", { headers: authHeaders() }).then((response) =>
    readJson<ThreadSummary[]>(response),
  );
}

export function fetchThread(threadId: string): Promise<ThreadDetail> {
  return fetch(`/api/threads/${threadId}`, { headers: authHeaders() }).then((response) =>
    readJson<ThreadDetail>(response),
  );
}

export function fetchDocuments(): Promise<IndexedDocument[]> {
  return fetch("/api/documents", { headers: authHeaders() }).then((response) =>
    readJson<IndexedDocument[]>(response),
  );
}

export async function uploadDocument(file: File): Promise<IngestJob | IndexedDocument> {
  const data = new FormData();
  data.append("file", file);
  const response = await fetch("/api/documents/upload", {
    method: "POST",
    headers: authHeaders(),
    body: data,
  });
  if (response.status === 202) {
    return readJson<IngestJob>(response);
  }
  return readJson<IndexedDocument>(response);
}

export async function fetchIngestJob(jobId: string): Promise<IngestJob> {
  return fetch(`/api/documents/jobs/${jobId}`, { headers: authHeaders() }).then((response) =>
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
  const response = await fetch(`/api/documents/${documentId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await readJson<{ status: string }>(response);
}

export async function streamChat(
  message: string,
  threadId: string | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, thread_id: threadId }),
  });
  if (response.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
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
