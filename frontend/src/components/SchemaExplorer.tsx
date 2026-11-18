"use client";

import { useEffect, useMemo, useState } from "react";

import { fetchSchema } from "@/lib/api";
import type { SchemaResponse, SchemaTable } from "@/lib/types";

function TableRow({ table, query }: { table: SchemaTable; query: string }) {
  const cols = query
    ? table.columns.filter((c) => `${c.name} ${c.description}`.toLowerCase().includes(query))
    : table.columns;
  return (
    <details open={Boolean(query) && cols.length > 0} className="group">
      <summary className="flex cursor-pointer list-none items-baseline justify-between gap-2 rounded px-2 py-1.5 hover:bg-surface-2">
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{table.business_name}</span>
          <span className="block truncate font-mono text-[11px] text-muted">{table.name}</span>
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-muted">{table.row_count.toLocaleString()}</span>
      </summary>
      <div className="mb-2 ml-2 border-l border-border pl-3">
        <p className="py-1 text-xs text-muted">{table.description}</p>
        <ul className="space-y-1">
          {cols.map((c) => (
            <li key={c.name} className="text-xs">
              <span className="font-mono">{c.name}</span>
              <span className="ml-1 text-[10px] text-muted">{c.type}</span>
              <p className="text-muted">{c.description}</p>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

export function SchemaExplorer() {
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    fetchSchema(ctrl.signal)
      .then(setSchema)
      .catch((e) => e.name !== "AbortError" && setError(e.message));
    return () => ctrl.abort();
  }, []);

  const q = query.trim().toLowerCase();
  const groups = useMemo(() => {
    if (!schema) return [];
    const match = (t: SchemaTable) =>
      !q ||
      `${t.name} ${t.business_name} ${t.description}`.toLowerCase().includes(q) ||
      t.columns.some((c) => `${c.name} ${c.description}`.toLowerCase().includes(q));
    const tables = schema.tables.filter(match);
    return [
      { label: "Facts: events and measures", tables: tables.filter((t) => t.kind === "fact") },
      { label: "Dimensions: who, what, where, when", tables: tables.filter((t) => t.kind === "dimension") },
    ];
  }, [schema, q]);

  const coverage = schema?.coverage.store_sales;
  const totalRows = schema?.tables.reduce((n, t) => n + t.row_count, 0) ?? 0;
  const totalCols = schema?.tables.reduce((n, t) => n + t.columns.length, 0) ?? 0;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <h2 className="text-sm font-semibold">Data explorer</h2>
        {schema && (
          <p className="mt-0.5 text-xs text-muted">
            {schema.tables.length} tables · {totalCols} columns · {(totalRows / 1e6).toFixed(1)}M rows
            {coverage && ` · sales ${coverage.min_date.slice(0, 4)}–${coverage.max_date.slice(0, 4)}`}
          </p>
        )}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tables and columns…"
          className="mt-2 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {error && <p className="p-2 text-sm text-danger">{error}</p>}
        {!schema && !error && <p className="p-2 text-sm text-muted">Loading…</p>}
        {groups.map(
          (g) =>
            g.tables.length > 0 && (
              <section key={g.label} className="mb-3">
                <h3 className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {g.label}
                </h3>
                {g.tables.map((t) => (
                  <TableRow key={t.name} table={t} query={q} />
                ))}
              </section>
            ),
        )}
      </div>
    </div>
  );
}
