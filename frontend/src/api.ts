import type {
  HealthStatus,
  IndexedDocument,
  StreamEvent,
  ThreadDetail,
  ThreadSummary,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthStatus> {
  return fetch("/api/health").then((response) => readJson<HealthStatus>(response));
}

export function fetchThreads(): Promise<ThreadSummary[]> {
  return fetch("/api/threads").then((response) => readJson<ThreadSummary[]>(response));
}

export function fetchThread(threadId: string): Promise<ThreadDetail> {
  return fetch(`/api/threads/${threadId}`).then((response) =>
    readJson<ThreadDetail>(response),
  );
}

export function fetchDocuments(): Promise<IndexedDocument[]> {
  return fetch("/api/documents").then((response) => readJson<IndexedDocument[]>(response));
}

export async function uploadDocument(file: File): Promise<IndexedDocument> {
  const data = new FormData();
  data.append("file", file);
  const response = await fetch("/api/documents/upload", { method: "POST", body: data });
  return readJson<IndexedDocument>(response);
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await fetch(`/api/documents/${documentId}`, { method: "DELETE" });
  await readJson<{ status: string }>(response);
}

export async function streamChat(
  message: string,
  threadId: string | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  });
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
