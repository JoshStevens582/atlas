export type HealthStatus = {
  status: string;
  openai_configured: boolean;
  vector_store: string;
  document_count: number;
  chunk_count: number;
};

export type ThreadSummary = {
  id: string;
  title: string;
  created_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
};

export type ThreadDetail = ThreadSummary & {
  messages: ChatMessage[];
};

export type IndexedDocument = {
  id: string;
  title: string;
  original_filename: string;
  chunk_count: number;
  created_at: string;
};

export type IngestJob = {
  job_id: string;
  status: "pending" | "running" | "done" | "failed";
  title: string;
  original_filename: string;
  document_id: string | null;
  chunk_count: number | null;
  error: string | null;
};

export type RetrievedChunk = {
  document_id: string;
  document_title: string;
  chunk_index: number;
  text: string;
  distance: number;
  match?: "vector" | "lexical" | "both";
  cite_n?: number;
  cited?: boolean | null;
};

export type StreamEvent =
  | { type: "thread"; thread: ThreadSummary }
  | { type: "sources"; sources: RetrievedChunk[] }
  | { type: "tool"; name: string; arguments: Record<string, unknown>; result: string }
  | { type: "token"; text: string }
  | { type: "done"; answer: string }
  | { type: "error"; detail: string };

export type ToolCall = {
  name: string;
  arguments: Record<string, unknown>;
  result: string;
};
