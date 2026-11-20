// URL builders for version files, exports and concept assets, for both the owner view and share links.

/** Where the SPA is mounted ("/studio", from Vite's base). Router paths are relative to it; /api stays at the root. */
export const STUDIO_BASE = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "");

/** The full read-only share link for a token: https://<host>/studio/s/<token>. */
export function shareUrl(token: string, origin: string = location.origin): string {
  return `${origin}${STUDIO_BASE}/s/${token}`;
}

/** The sign-in page's "See a real build" target: an https URL or a same-site absolute path, else null (hidden). */
export function safeShareUrl(u: string | null | undefined): string | null {
  const t = (u || "").trim();
  if (/^https:\/\/[A-Za-z0-9.-]+(:\d+)?(\/[^\s"'<>\\]*)?$/.test(t)) return t;
  if (/^\/(?!\/)[^\s"'<>\\]*$/.test(t)) return t;
  return null;
}

export interface Ctx {
  pid: string;
  share?: string | null; // share token: read-only view
}

export function apiRoot(ctx: Ctx): string {
  return ctx.share ? `/api/share/${ctx.share}` : `/api/projects/${ctx.pid}`;
}

export function fileBase(ctx: Ctx, n: number): string {
  return `${apiRoot(ctx)}/v/${n}/files/`;
}

export function fileUrl(ctx: Ctx, n: number, path: string, download = false): string {
  const clean = path.replace(/^\.?\/+/, "");
  const enc = clean.split("/").map(encodeURIComponent).join("/");
  return `${fileBase(ctx, n)}${enc}${download ? "?download=1" : ""}`;
}

export function exportUrl(ctx: Ctx, n: number): string {
  return `${apiRoot(ctx)}/v/${n}/export`;
}

/** The server already rewrites URLs in a shared Project; this is a safety net for any that slipped through. */
export function rewriteShareUrl(url: string | null | undefined, share?: string | null): string | null {
  if (!url) return null;
  if (!share) return url;
  return url.replace(/^\/api\/projects\/[^/]+\//, `/api/share/${share}/`);
}

/** Resolve a path from a markdown document (e.g. BUILD_GUIDE.md) against the version file base. */
export function resolveDocUrl(ctx: Ctx, n: number, src: string, docDir = ""): string {
  if (/^(https?:|data:|\/)/.test(src)) return src;
  const parts = (docDir ? `${docDir}/${src}` : src).split("/");
  const out: string[] = [];
  for (const p of parts) {
    if (p === "..") out.pop();
    else if (p && p !== ".") out.push(p);
  }
  return fileUrl(ctx, n, out.join("/"));
}
