// A page opened before a deploy asks for code files that the new deploy no longer serves (file names carry a content
// hash), so the next lazily loaded tab fails with "Failed to fetch dynamically imported module". The cure is one
// reload, which fetches the new index.html and its files. Guarded so a real outage cannot cause a reload loop.

const KEY = "gs.chunk-reload";
const WINDOW_MS = 60_000;

/** True for the errors browsers raise when a lazily imported file cannot be loaded. */
export function isChunkError(e: unknown): boolean {
  const msg = String((e as any)?.message ?? e ?? "");
  const name = String((e as any)?.name ?? "");
  return (
    /Failed to fetch dynamically imported module/i.test(msg) || // Chrome, Edge
    /Importing a module script failed/i.test(msg) || // Safari
    /error loading dynamically imported module/i.test(msg) || // Firefox
    /Unable to preload CSS/i.test(msg) || // Vite's CSS preload
    name === "ChunkLoadError"
  );
}

/** Reloads the page unless it already reloaded for this reason within the last minute. True when it reloads. */
export function reloadOnce(now: number = Date.now(), storage: Pick<Storage, "getItem" | "setItem"> | null = safeStorage(), reload: () => void = () => window.location.reload()): boolean {
  try {
    const last = Number(storage?.getItem(KEY) || 0);
    if (last && now - last < WINDOW_MS) return false;
    storage?.setItem(KEY, String(now));
  } catch {
    /* no storage: still reload once; without storage the guard is this page load only */
  }
  reload();
  return true;
}

function safeStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
