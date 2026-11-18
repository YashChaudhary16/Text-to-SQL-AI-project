import type { SchemaResponse, ServerEvent } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {}

/**
 * POST a chat message and invoke `onEvent` for each Server-Sent Event.
 * EventSource only supports GET, so this parses the stream from fetch() by hand.
 */
export async function streamChat(
  body: { message: string; thread_id?: string | null },
  onEvent: (e: ServerEvent) => void,
  opts: { apiKey?: string | null; signal?: AbortSignal } = {},
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.apiKey) headers["X-Anthropic-Key"] = opts.apiKey;

  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    let detail = `Request failed (${res.status})`;
    try {
      const json = await res.json();
      if (typeof json?.detail === "string") detail = json.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail);
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const parsed = parseBlock(block);
      if (parsed) onEvent(parsed);
    }
  }
}

function parseBlock(block: string): ServerEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // keep-alive comment
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) } as ServerEvent;
  } catch {
    return null;
  }
}

export async function fetchSchema(signal?: AbortSignal): Promise<SchemaResponse> {
  const res = await fetch(`${API_URL}/api/schema`, { signal });
  if (!res.ok) throw new ApiError(`Couldn't load the schema (${res.status})`);
  return res.json();
}
