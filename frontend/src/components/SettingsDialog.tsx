"use client";

import { useEffect, useRef, useState } from "react";

export function SettingsDialog({
  open,
  onClose,
  apiKey,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  apiKey: string | null;
  onSave: (key: string | null) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState(apiKey ?? "");

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setDraft(apiKey ?? "");
      d.showModal();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open, apiKey]);

  const valid = draft === "" || draft.startsWith("sk-ant-");

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(92vw,28rem)] rounded-xl border border-border bg-surface p-0 text-text"
    >
      <form
        method="dialog"
        onSubmit={() => onSave(draft.trim() || null)}
        className="space-y-3 p-5"
      >
        <h2 className="text-base font-semibold">Use your own Claude key (optional)</h2>
        <p className="text-sm text-muted">
          By default, questions are answered by free open-source models. Add an Anthropic API key to route
          your questions through Claude instead: more accurate on hard questions, billed to your key.
        </p>
        <input
          type="password"
          autoComplete="off"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="sk-ant-…"
          className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm outline-none focus:border-accent"
        />
        {!valid && <p className="text-xs text-danger">Anthropic keys start with “sk-ant-”.</p>}
        <p className="text-xs text-muted">
          Kept only in this browser tab (sessionStorage) and sent with each request. The server never stores or
          logs it.
        </p>
        <div className="flex justify-end gap-2 pt-1">
          {apiKey && (
            <button
              type="button"
              onClick={() => {
                onSave(null);
                onClose();
              }}
              className="mr-auto rounded-md px-3 py-1.5 text-sm text-danger hover:bg-danger-soft"
            >
              Remove key
            </button>
          )}
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm hover:bg-surface-2">
            Cancel
          </button>
          <button
            type="submit"
            disabled={!valid}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </form>
    </dialog>
  );
}
