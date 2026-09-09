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

export type RetrievedChunk = {
  document_id: string;
  document_title: string;
  chunk_index: number;
  text: string;
  distance: number;
};

export type StreamEvent =
  | { type: "thread"; thread: ThreadSummary }
  | { type: "sources"; sources: RetrievedChunk[] }
  | { type: "token"; text: string }
  | { type: "done"; answer: string }
  | { type: "error"; detail: string };
