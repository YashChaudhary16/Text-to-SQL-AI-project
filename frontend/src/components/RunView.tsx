"use client";

import type { Run } from "@/lib/types";

import { AgentTrace } from "./AgentTrace";
import { ResultTable } from "./ResultTable";
import { SqlBlock } from "./SqlBlock";

export function RunView({
  run,
  isLatest,
  onAnswer,
}: {
  run: Run;
  isLatest: boolean;
  onAnswer: (text: string) => void;
}) {
  const sql = run.result?.sql ?? [...run.steps].reverse().find((s) => s.node === "write_sql")?.sql;
  const pendingClar = run.status === "waiting" ? run.clarifications.at(-1) : undefined;

  return (
    <article className="space-y-3">
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-accent-fg">
          {run.question}
        </p>
      </div>

      {run.clarifications.map((c, i) =>
        c.answer ? (
          <div key={i} className="space-y-2">
            <p className="max-w-[85%] text-sm">{c.question}</p>
            <div className="flex justify-end">
              <p className="rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-accent-fg">{c.answer}</p>
            </div>
          </div>
        ) : null,
      )}

      <AgentTrace run={run} />

      {pendingClar && (
        <div className="rounded-lg border border-accent/40 bg-accent-soft p-3">
          <p className="text-sm font-medium">{pendingClar.question}</p>
          {isLatest && pendingClar.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {pendingClar.options.map((o) => (
                <button
                  key={o}
                  onClick={() => onAnswer(o)}
                  className="rounded-full border border-accent/50 bg-surface px-3 py-1 text-sm hover:bg-accent hover:text-accent-fg"
                >
                  {o}
                </button>
              ))}
            </div>
          )}
          <p className="mt-2 text-xs text-muted">Pick an option or type your own answer below.</p>
        </div>
      )}

      {sql && run.result && <SqlBlock sql={sql} />}
      {run.result && <ResultTable result={run.result} />}

      {run.answer && <p className="whitespace-pre-line text-sm leading-relaxed">{run.answer}</p>}
      {run.error && (
        <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">
          {run.error}
        </p>
      )}
    </article>
  );
}
