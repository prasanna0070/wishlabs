// A wish typed on the landing page arrives as /studio/?wish=<text>. It pre-fills the new-project composer (never
// sends it), survives the sign-in redirect through sessionStorage, and is capped at WISH_MAX characters.

export const WISH_MAX = 500;
const KEY = "gs.wish";

/** Trim, drop control characters (keeping line breaks and tabs) and cap at WISH_MAX characters. */
export function cleanWish(raw: string | null | undefined): string {
  if (!raw) return "";
  const text = String(raw)
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .trim();
  const chars = Array.from(text); // code points, so an emoji is never cut in half
  return chars.length > WISH_MAX ? chars.slice(0, WISH_MAX).join("").trimEnd() : text;
}

/** The wish in a query string ("?wish=..."), cleaned; "" when there is none. */
export function wishFromSearch(search: string): string {
  try {
    return cleanWish(new URLSearchParams(search).get("wish"));
  } catch {
    return "";
  }
}

/** The query string without its wish parameter ("" or "?a=b"). */
export function searchWithoutWish(search: string): string {
  const q = new URLSearchParams(search);
  q.delete("wish");
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Keep the wish from the address bar for after sign-in. Call once at start-up. */
export function rememberWish(search: string): string {
  const w = wishFromSearch(search);
  if (w) {
    try {
      sessionStorage.setItem(KEY, w);
    } catch {
      /* private mode: the router state still carries it through sign-in */
    }
  }
  return w;
}

/** The remembered wish, if any (it stays stored until takeWish). */
export function storedWish(): string {
  try {
    return cleanWish(sessionStorage.getItem(KEY));
  } catch {
    return "";
  }
}

/** Read the wish for the composer (address bar first, then storage) and forget the stored copy. */
export function takeWish(search: string): string {
  const w = wishFromSearch(search) || storedWish();
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  return w;
}
