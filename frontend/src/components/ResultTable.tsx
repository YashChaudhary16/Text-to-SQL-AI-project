"use client";

import type { QueryResult } from "@/lib/types";

const humanize = (col: string) => col.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v)
      ? v.toLocaleString()
      : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return String(v);
}

function toCsv(r: QueryResult): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [r.columns.map(esc).join(","), ...r.rows.map((row) => row.map(esc).join(","))].join("\n");
}

export function ResultTable({ result }: { result: QueryResult }) {
  const numeric = result.columns.map((_, i) => result.rows.some((r) => typeof r[i] === "number"));

  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(result)], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "results.csv" });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs text-muted">
        <span>
          {result.row_count.toLocaleString()} row{result.row_count === 1 ? "" : "s"}
          {result.truncated && " (truncated)"} · {result.elapsed_ms.toLocaleString()} ms
        </span>
        <button onClick={download} className="rounded px-2 py-1 font-medium text-accent hover:bg-accent-soft">
          Download CSV
        </button>
      </div>
      {result.row_count === 0 ? (
        <p className="px-3 py-6 text-center text-sm text-muted">No rows matched.</p>
      ) : (
        <div className="max-h-96 overflow-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 bg-surface-2">
              <tr>
                {result.columns.map((c, i) => (
                  <th
                    key={c}
                    className={`whitespace-nowrap px-3 py-2 font-medium ${numeric[i] ? "text-right" : "text-left"}`}
                  >
                    {humanize(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, ri) => (
                <tr key={ri} className="border-t border-border">
                  {row.map((v, ci) => (
                    <td
                      key={ci}
                      className={`whitespace-nowrap px-3 py-1.5 ${numeric[ci] ? "text-right tabular-nums" : ""}`}
                    >
                      {formatCell(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
