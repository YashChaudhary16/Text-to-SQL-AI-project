// Mirrors the SSE events emitted by backend/src/sqlagent/api/app.py.

export type StepEvent =
  | { node: "understand"; intent: string; question: string; assumptions: string[]; model: string | null }
  | { node: "clarify" }
  | { node: "retrieve"; tables: string[] }
  | { node: "write_sql"; sql: string; model: string | null }
  | { node: "execute"; error: string; attempt: number }
  // Not a graph node: a provider was busy/unavailable and the next model in the chain was tried.
  | ({ node: "notice" } & Notice);

export type Notice =
  | { type: "fallback"; role: string; model: string; reason: string }
  | { type: "waiting"; role: string; seconds: number };

export interface QueryResult {
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface Clarification {
  question: string;
  options: string[];
}

export interface LlmCall {
  role: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  fallbacks: string[];
}

export type ServerEvent =
  | { event: "thread"; data: { thread_id: string } }
  | { event: "step"; data: StepEvent }
  | { event: "notice"; data: Notice }
  | { event: "clarification"; data: Clarification }
  | { event: "result"; data: QueryResult }
  | { event: "answer"; data: { text: string; llm_calls: LlmCall[] } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

export type RunStatus = "running" | "waiting" | "done" | "error";

/** One user question and everything the agents did to answer it. */
export interface Run {
  id: string;
  question: string;
  steps: StepEvent[];
  clarifications: { question: string; options: string[]; answer?: string }[];
  result?: QueryResult;
  answer?: string;
  llmCalls?: LlmCall[];
  error?: string;
  status: RunStatus;
}

export interface SchemaColumn {
  name: string;
  type: string;
  description: string;
}

export interface SchemaTable {
  name: string;
  business_name: string;
  kind: "fact" | "dimension";
  description: string;
  row_count: number;
  columns: SchemaColumn[];
}

export interface SchemaResponse {
  domain: string;
  coverage: Record<string, { date_column: string; min_date: string; max_date: string }>;
  tables: SchemaTable[];
}
