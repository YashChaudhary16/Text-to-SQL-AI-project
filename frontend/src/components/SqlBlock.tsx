"use client";

import { useState } from "react";

export function SqlBlock({ sql, defaultOpen = false }: { sql: string; defaultOpen?: boolean }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <details open={defaultOpen} className="group rounded-lg border border-border bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-xs font-medium text-muted">
        <span>
          <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
          SQL
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            copy();
          }}
          className="rounded px-2 py-0.5 text-accent hover:bg-accent-soft"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </summary>
      <pre className="overflow-x-auto border-t border-border bg-code px-3 py-2 font-mono text-xs leading-relaxed">
        {sql}
      </pre>
    </details>
  );
}
