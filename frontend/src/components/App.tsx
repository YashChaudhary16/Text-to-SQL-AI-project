"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { getKey, getServerKey, setKey, subscribe } from "@/lib/keyStore";
import { useChat } from "@/lib/useChat";

import { RunView } from "./RunView";
import { SchemaExplorer } from "./SchemaExplorer";
import { SettingsDialog } from "./SettingsDialog";

const EXAMPLES = [
  "What were the top 10 product categories by revenue in 2002?",
  "Who are our best customers?",
  "Compare monthly web vs store revenue in 2001",
  "Which return reasons cost us the most in refunds?",
  "What do we know about our customers' demographics?",
];

export function App() {
  // sessionStorage is browser-only; the server snapshot (null) keeps hydration consistent.
  const apiKey = useSyncExternalStore(subscribe, getKey, getServerKey);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const { runs, busy, send, stop, reset } = useChat(getKey);
  const waiting = runs.at(-1)?.status === "waiting";

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [runs]);

  const submit = (text: string) => {
    if (!text.trim() || busy) return;
    send(text);
    setInput("");
  };

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* Explorer: sidebar on desktop, drawer on mobile */}
      <aside
        className={`fixed inset-y-0 left-0 z-30 w-80 max-w-[85vw] border-r border-border bg-surface transition-transform md:static md:translate-x-0 ${
          explorerOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <SchemaExplorer />
      </aside>
      {explorerOpen && (
        <button
          aria-label="Close data explorer"
          className="fixed inset-0 z-20 bg-black/40 md:hidden"
          onClick={() => setExplorerOpen(false)}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2.5">
          <button
            onClick={() => setExplorerOpen(true)}
            className="rounded-md border border-border px-2 py-1 text-xs md:hidden"
          >
            Data
          </button>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold">Ask the Warehouse</h1>
            <p className="truncate text-xs text-muted">Plain-English questions over a 24-table retail warehouse</p>
          </div>
          <div className="ml-auto flex items-center gap-1">
            {runs.length > 0 && (
              <button onClick={reset} className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-2">
                New chat
              </button>
            )}
            <button
              onClick={() => setSettingsOpen(true)}
              className="rounded-md border border-border px-2 py-1 text-xs hover:bg-surface-2"
            >
              {apiKey ? "Claude key ✓" : "Free models"}
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-8 px-4 py-6">
            {runs.length === 0 ? (
              <section className="pt-8 text-center sm:pt-16">
                <h2 className="text-2xl font-semibold tracking-tight">What do you want to know?</h2>
                <p className="mx-auto mt-2 max-w-lg text-sm text-muted">
                  A team of AI agents works out what you mean, asks if something&apos;s ambiguous, finds the
                  right tables among 425 columns, then writes and runs the SQL for you.
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map((q) => (
                    <button
                      key={q}
                      onClick={() => submit(q)}
                      className="rounded-full border border-border bg-surface px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </section>
            ) : (
              runs.map((r, i) => (
                <RunView key={r.id} run={r} isLatest={i === runs.length - 1} onAnswer={submit} />
              ))
            )}
            <div ref={bottom} />
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(input);
          }}
          className="border-t border-border bg-surface px-4 py-3"
        >
          <div className="mx-auto flex max-w-3xl gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={waiting ? "Answer the question above…" : "Ask a question about sales, customers, products…"}
              maxLength={2000}
              className="min-w-0 flex-1 rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
            />
            {busy ? (
              <button type="button" onClick={stop} className="rounded-lg border border-border px-4 text-sm">
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg disabled:opacity-40"
              >
                Ask
              </button>
            )}
          </div>
        </form>
      </main>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        apiKey={apiKey}
        onSave={setKey}
      />
    </div>
  );
}
