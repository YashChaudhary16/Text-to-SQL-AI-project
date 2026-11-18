"use client";

import type { Run, StepEvent } from "@/lib/types";

const INTENT_LABEL: Record<string, string> = {
  data_question: "Data question",
  schema_question: "Question about the data",
  out_of_scope: "Out of scope",
};

function ModelChip({ model }: { model: string | null | undefined }) {
  if (!model) return null;
  return (
    <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted">{model}</span>
  );
}

function Item({
  state,
  title,
  children,
}: {
  state: "done" | "active" | "failed";
  title: React.ReactNode;
  children?: React.ReactNode;
}) {
  const dot =
    state === "done" ? "bg-ok" : state === "failed" ? "bg-danger" : "bg-accent animate-pulse";
  return (
    <li className="relative pl-5">
      <span className={`absolute left-0 top-1.5 h-2 w-2 rounded-full ${dot}`} />
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="mt-1 text-xs text-muted">{children}</div>}
    </li>
  );
}

function renderStep(s: StepEvent, i: number) {
  switch (s.node) {
    case "understand":
      return (
        <Item key={i} state="done" title={<>Understood the request<ModelChip model={s.model} /></>}>
          <div>
            {INTENT_LABEL[s.intent] ?? s.intent}: <span className="text-text">“{s.question}”</span>
          </div>
          {s.assumptions.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {s.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          )}
        </Item>
      );
    case "clarify":
      return <Item key={i} state="done" title="Got your clarification" />;
    case "retrieve":
      return (
        <Item key={i} state="done" title={`Found ${s.tables.length} relevant tables`}>
          <div className="flex flex-wrap gap-1">
            {s.tables.map((t) => (
              <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px]">
                {t}
              </span>
            ))}
          </div>
        </Item>
      );
    case "write_sql":
      return <Item key={i} state="done" title={<>Wrote SQL<ModelChip model={s.model} /></>} />;
    case "execute":
      return (
        <Item key={i} state="failed" title={`Attempt ${s.attempt} failed, retrying with the error`}>
          <code className="break-words font-mono text-danger">{s.error}</code>
        </Item>
      );
  }
}

function pendingLabel(run: Run): string | null {
  if (run.status !== "running") return null;
  const last = run.steps.at(-1)?.node;
  if (!last || last === "clarify") return "Understanding your question…";
  if (last === "understand") return "Finding relevant tables…";
  if (last === "retrieve") return "Writing SQL…";
  if (last === "write_sql") return "Running the query…";
  if (last === "execute") return "Fixing the query…";
  return "Working…";
}

export function AgentTrace({ run }: { run: Run }) {
  const pending = pendingLabel(run);
  const calls = run.llmCalls ?? [];
  const tokens = calls.reduce((n, c) => n + c.input_tokens + c.output_tokens, 0);
  const done = run.status === "done" || run.status === "error";

  return (
    <details open={!done} className="group rounded-lg border border-border bg-surface px-3 py-2">
      <summary className="cursor-pointer list-none text-xs font-medium text-muted">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
        Agent steps
        {done && calls.length > 0 && (
          <span className="ml-2 font-normal">
            · {calls.length} model calls · {tokens.toLocaleString()} tokens
          </span>
        )}
      </summary>
      <ol className="mt-2 space-y-2 border-l border-border pl-3">
        {run.steps.map(renderStep)}
        {run.result && (
          <Item state="done" title={`Ran the query: ${run.result.row_count.toLocaleString()} rows`} />
        )}
        {pending && <Item state="active" title={pending} />}
      </ol>
      {done && calls.some((c) => c.fallbacks.length > 0) && (
        <p className="mt-2 text-[11px] text-muted">
          Some providers were busy, so the request fell back to the next model in the chain.
        </p>
      )}
    </details>
  );
}
