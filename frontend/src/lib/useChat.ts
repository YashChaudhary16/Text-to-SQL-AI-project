"use client";

import { useCallback, useRef, useState } from "react";

import { streamChat } from "./api";
import type { Run, ServerEvent } from "./types";

/**
 * Conversation state. A "run" is one question; if the agents pause to ask a clarifying
 * question, the user's reply resumes the same run on the same backend thread.
 */
export function useChat(getApiKey: () => string | null) {
  const [runs, setRuns] = useState<Run[]>([]);
  const threadId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const busy = runs.at(-1)?.status === "running";

  const update = useCallback((id: string, fn: (r: Run) => Run) => {
    setRuns((prev) => prev.map((r) => (r.id === id ? fn(r) : r)));
  }, []);

  const send = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || busy) return;

      const last = runs.at(-1);
      let runId: string;
      if (last?.status === "waiting") {
        runId = last.id;
        update(runId, (r) => ({
          ...r,
          status: "running",
          clarifications: r.clarifications.map((c, i) =>
            i === r.clarifications.length - 1 ? { ...c, answer: text } : c,
          ),
        }));
      } else {
        runId = crypto.randomUUID();
        setRuns((prev) => [...prev, { id: runId, question: text, steps: [], clarifications: [], status: "running" }]);
      }

      const handle = (e: ServerEvent) => {
        switch (e.event) {
          case "thread":
            threadId.current = e.data.thread_id;
            break;
          case "step":
            update(runId, (r) => ({ ...r, steps: [...r.steps, e.data] }));
            break;
          case "clarification":
            update(runId, (r) => ({
              ...r,
              status: "waiting",
              clarifications: [...r.clarifications, { question: e.data.question, options: e.data.options }],
            }));
            break;
          case "result":
            update(runId, (r) => ({ ...r, result: e.data }));
            break;
          case "answer":
            update(runId, (r) => ({ ...r, answer: e.data.text, llmCalls: e.data.llm_calls, status: "done" }));
            break;
          case "error":
            update(runId, (r) => ({ ...r, error: e.data.message, status: "error" }));
            break;
          case "done":
            update(runId, (r) => (r.status === "running" ? { ...r, status: "done" } : r));
            break;
        }
      };

      abort.current = new AbortController();
      try {
        await streamChat({ message: text, thread_id: threadId.current }, handle, {
          apiKey: getApiKey(),
          signal: abort.current.signal,
        });
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          update(runId, (r) => ({ ...r, status: "error", error: "Stopped." }));
        } else {
          update(runId, (r) => ({
            ...r,
            status: "error",
            error: (err as Error).message || "Couldn't reach the server.",
          }));
        }
      }
    },
    [busy, runs, update, getApiKey],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  const reset = useCallback(() => {
    abort.current?.abort();
    threadId.current = null;
    setRuns([]);
  }, []);

  return { runs, busy, send, stop, reset };
}
