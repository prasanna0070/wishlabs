import { useEffect, useRef, useState } from "react";
import { api, fetchFile } from "./api";
import { apiRoot, fileUrl, type Ctx } from "./urls";
import type { FileEntry } from "./types";

// ---------------------------------------------------------------- file list (with sizes) per version
const filesCache = new Map<string, Promise<FileEntry[] | null>>();

/** A version's files with sizes and hashes (GET /v/{n}, or manifest.json on share links). Finished versions are cached. */
export function versionFiles(ctx: Ctx, n: number): Promise<FileEntry[] | null> {
  const key = `${apiRoot(ctx)}/v/${n}`;
  const hit = filesCache.get(key);
  if (hit) return hit;
  const fromManifest = () => fetchFile(fileUrl(ctx, n, "manifest.json")).then((m: any) => ({ files: m?.files, done: true }));
  const p: Promise<FileEntry[] | null> = (
    ctx.share ? fromManifest() : api.versionDetail(ctx.pid, n).then((d) => (d.files?.length ? { files: d.files, done: d.state === "done" } : fromManifest().catch(() => ({ files: [], done: false }))))
  )
    .then(({ files, done }) => {
      if (!done) filesCache.delete(key);
      return Array.isArray(files) && files.length ? (files as FileEntry[]) : null;
    })
    .catch(() => {
      filesCache.delete(key);
      return null;
    });
  filesCache.set(key, p);
  return p;
}

/** undefined while loading, null when the version lists no files. */
export function useVersionFiles(ctx: Ctx | null, n: number | null | undefined): FileEntry[] | null | undefined {
  const [files, setFiles] = useState<FileEntry[] | null | undefined>(undefined);
  useEffect(() => {
    if (!ctx || n == null) {
      setFiles(null);
      return;
    }
    let alive = true;
    setFiles(undefined);
    versionFiles(ctx, n).then((f) => alive && setFiles(f));
    return () => {
      alive = false;
    };
  }, [ctx?.pid, ctx?.share, n]);
  return files;
}

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

// ---------------------------------------------------------------- file index per version
// One listing per version (GET /v/{n}, or manifest.json on share links), so optional files that a version lacks are
// never requested (no 404 noise). Only finished versions are cached; a running version's listing changes.
const indexCache = new Map<string, Promise<Set<string> | null>>();

export function fileIndex(ctx: Ctx, n: number): Promise<Set<string> | null> {
  const key = `${apiRoot(ctx)}/v/${n}`;
  const hit = indexCache.get(key);
  if (hit) return hit;
  const p: Promise<Set<string> | null> = (
    ctx.share
      ? fetchFile(fileUrl(ctx, n, "manifest.json")).then((m: any) => ({ files: m?.files, done: true }))
      : api.versionDetail(ctx.pid, n).then((d) => ({ files: d.files, done: d.state === "done" }))
  )
    .then(({ files, done }) => {
      if (!done) indexCache.delete(key);
      return Array.isArray(files) && files.length ? new Set(files.map((f: any) => f.path)) : null;
    })
    .catch(() => {
      indexCache.delete(key);
      return null;
    });
  indexCache.set(key, p);
  return p;
}

/** The set of file paths in a version: undefined while loading, null when unknown (then files are just fetched). */
export function useFileIndex(ctx: Ctx | null, n: number | null | undefined): Set<string> | null | undefined {
  const [idx, setIdx] = useState<Set<string> | null | undefined>(undefined);
  useEffect(() => {
    if (!ctx || n == null) {
      setIdx(null);
      return;
    }
    let alive = true;
    setIdx(undefined);
    fileIndex(ctx, n).then((s) => alive && setIdx(s));
    return () => {
      alive = false;
    };
  }, [ctx?.pid, ctx?.share, n]);
  return idx;
}

/** True/false when the index knows, null when it does not (callers then just try the file). */
export function hasFile(idx: Set<string> | null | undefined, path: string): boolean | null {
  if (idx === undefined) return null;
  if (idx === null) return null;
  return idx.has(path);
}

/** Fetch one version file (cached). `n` null or `path` null means "nothing to load". Skips files the version lacks. */
export function useVersionFile<T = any>(ctx: Ctx | null, n: number | null | undefined, path: string | null, as: "json" | "text" = "json"): Loaded<T> {
  const idx = useFileIndex(path ? ctx : null, n);
  const known = path ? hasFile(idx, path) : null;
  const waiting = !!(ctx && n != null && path && idx === undefined);
  const url = ctx && n != null && path && !waiting && known !== false ? fileUrl(ctx, n, path) : null;
  const r = useUrl<T>(url, as);
  if (waiting) return { data: null, error: null, loading: true };
  if (known === false) return { data: null, error: "not_found", loading: false };
  return r;
}

export function useUrl<T = any>(url: string | null, as: "json" | "text" = "json", noCache = false): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null, loading: !!url });
  useEffect(() => {
    if (!url) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let alive = true;
    setState((s) => ({ data: s.data && (s as any)._url === url ? s.data : null, error: null, loading: true }));
    fetchFile(url, as, noCache).then(
      (data) => alive && setState({ data, error: null, loading: false, ...({ _url: url } as any) }),
      (e) => alive && setState({ data: null, error: e?.status === 404 ? "not_found" : e?.message || String(e), loading: false }),
    );
    return () => {
      alive = false;
    };
  }, [url, as, noCache]);
  return state;
}

/** Re-render every `ms` while `on`. Returns Date.now(). */
export function useNow(ms: number, on = true): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms, on]);
  return now;
}

export function useOnClickOutside(ref: React.RefObject<HTMLElement>, fn: () => void, on = true) {
  const cb = useRef(fn);
  cb.current = fn;
  useEffect(() => {
    if (!on) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) cb.current();
    };
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") cb.current();
    };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("mousedown", h);
      document.removeEventListener("keydown", k);
    };
  }, [ref, on]);
}
