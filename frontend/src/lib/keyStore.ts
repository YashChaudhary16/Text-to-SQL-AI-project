// The visitor's optional Anthropic key, kept in sessionStorage (this tab only) and exposed
// as an external store for useSyncExternalStore. Falls back to memory if storage is blocked.

const STORAGE_KEY = "anthropic_api_key";
const listeners = new Set<() => void>();
let memory: string | null = null;

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function getKey(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return memory;
  }
}

export const getServerKey = (): string | null => null;

export function setKey(key: string | null): void {
  memory = key;
  try {
    if (key) sessionStorage.setItem(STORAGE_KEY, key);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage blocked: memory only */
  }
  listeners.forEach((l) => l());
}
