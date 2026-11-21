// Thin fetch client for /api (same origin, cookie auth). Any 401 flips the auth store to "show login".
import { useAuth } from "../store/auth";
import type { Mode, Project, ProjectSummary, Upload, Version, VersionDetail, GuestInfo } from "./types";
import type { TurnView } from "./working";

export class ApiError extends Error {
  status: number;
  code: string;
  details: any;
  constructor(status: number, code: string, message: string, details?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
  get errors(): string[] {
    const e = this.details?.errors;
    return Array.isArray(e) ? e.map(String) : [];
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const e = body?.error || {};
  return new ApiError(res.status, e.code || "error", e.message || `${res.status} ${res.statusText}`, e.details);
}

export async function request<T = any>(method: string, url: string, body?: unknown, opts: RequestInit = {}): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", ...opts };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (res.status === 401 && !url.startsWith("/api/share/") && url !== "/api/login") {
    useAuth.getState().setAuthed(false);
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  health: () => request("GET", "/api/health"),
  me: () => request<{ authenticated: boolean; expires: string }>("GET", "/api/me"),
  /** Start-up probe: always 200 ({authenticated: false} when signed out), so no 401 lands in the console. */
  session: () =>
    request<{ authenticated: boolean; expires: string | null; share_url?: string | null; voice?: boolean; kind?: "team" | "guest"; guest?: GuestInfo }>(
      "GET",
      "/api/session",
    ),
  login: (key: string) => request<{ ok: boolean }>("POST", "/api/login", { key }),
  logout: () => request("POST", "/api/logout"),

  projects: () => request<{ projects: ProjectSummary[] }>("GET", "/api/projects"),
  createProject: (body: { name?: string; mode?: Mode }) => request<Project>("POST", "/api/projects", body),
  project: (pid: string) => request<Project>("GET", `/api/projects/${pid}`),
  /** Is a chat turn running, and what is it doing (step, steps, thought summaries)? For a reload or a dropped stream. */
  turnStatus: (pid: string, signal?: AbortSignal) => request<TurnView>("GET", `/api/projects/${pid}/turn`, undefined, signal ? { signal } : {}),
  patchProject: (pid: string, body: { name?: string; mode?: Mode; share_uploads?: boolean }) => request<Project>("PATCH", `/api/projects/${pid}`, body),
  /** Let share links show the uploaded images (off by default; the image brief is shared either way). */
  setShareUploads: (pid: string, on: boolean) => request<Project>("PATCH", `/api/projects/${pid}`, { share_uploads: on }),
  /** DELETE an upload no message carries (a composer image removed before sending); 409 once a message has it. */
  deleteUpload: (pid: string, id: string) => request<{ ok: boolean }>("DELETE", `/api/projects/${pid}/uploads/${encodeURIComponent(id)}`),
  /** POST /api/projects/{pid}/uploads, with upload progress (see uploadImage below). */
  uploadImage: (pid: string, file: Blob, name: string, opts?: UploadOpts) => uploadImage(pid, file, name, opts),
  deleteProject: (pid: string) => request("DELETE", `/api/projects/${pid}`),
  checklist: (pid: string, body: { assembly?: Record<string, boolean>; wiring?: Record<string, boolean> }) =>
    request<{ checklist: any }>("PATCH", `/api/projects/${pid}/checklist`, body),
  putBrief: (pid: string, brief: any) => request<Project>("PUT", `/api/projects/${pid}/brief`, { brief }),

  versions: (pid: string) => request<{ versions: Version[]; current_version: number }>("GET", `/api/projects/${pid}/versions`),
  versionDetail: (pid: string, n: number) => request<VersionDetail>("GET", `/api/projects/${pid}/v/${n}`),
  undo: (pid: string) => request<Project>("POST", `/api/projects/${pid}/undo`),
  redo: (pid: string) => request<Project>("POST", `/api/projects/${pid}/redo`),
  setCurrent: (pid: string, n: number) => request<Project>("PUT", `/api/projects/${pid}/current`, { n }),
  restore: (pid: string, n: number) => request<Version>("POST", `/api/projects/${pid}/versions/${n}/restore`),
  /** BLK-4: rerun only the firmware stage of version n with stories_audio=true; answers with the new version. */
  storyAudio: (pid: string, n: number) => request<any>("POST", `/api/projects/${pid}/versions/${n}/story-audio`),

  tweak: (pid: string, body: any) => request<any>("POST", `/api/projects/${pid}/tweak`, body),
  preview: (pid: string, body: any) => request<any>("POST", `/api/projects/${pid}/preview`, body),
  orderRequest: (pid: string, body: { pincode: string; quantity: number; note?: string; version?: number }) =>
    request<any>("POST", `/api/projects/${pid}/order-request`, body),
  share: (pid: string) => request<{ token: string; url: string }>("POST", `/api/projects/${pid}/share`),
  unshare: (pid: string) => request("DELETE", `/api/projects/${pid}/share`),
  shared: (token: string) => request<Project>("GET", `/api/share/${token}`),
};

// File and asset fetches with a small in-memory cache keyed by URL. Files of a `done` version are immutable.
const cache = new Map<string, Promise<any>>();

export function fetchFile(url: string, as: "json" | "text" | "blob" = "json", noCache = false): Promise<any> {
  const key = `${as}:${url}`;
  if (!noCache && cache.has(key)) return cache.get(key)!;
  const p = fetch(url, { credentials: "same-origin" }).then(async (r) => {
    if (r.status === 401) useAuth.getState().setAuthed(false);
    if (!r.ok) throw new ApiError(r.status, r.status === 404 ? "not_found" : "error", `${r.status} ${url}`);
    return as === "json" ? r.json() : as === "text" ? r.text() : r.blob();
  });
  if (!noCache) {
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return p;
}

export function clearFileCache(prefix?: string) {
  for (const k of [...cache.keys()]) if (!prefix || k.includes(prefix)) cache.delete(k);
}

// ---------------------------------------------------------------- image uploads (_notes/image-contract.md §1)

function friendlyUploadError(status: number, serverMessage: string | undefined, name: string): string {
  const said = (serverMessage || "").trim();
  if (said && !/^\d{3}\b/.test(said)) return said.toLowerCase().includes(name.toLowerCase()) ? said : `${name}: ${said}`;
  switch (status) {
    case 0:
      return `${name} did not upload: the connection dropped. Check the network and try again.`;
    case 409:
    case 413:
      return `${name} could not be added: an image can be up to 15 MB, a message can carry 4 images and a project 12.`;
    case 415:
      return `${name}: the studio cannot read this image. Export it as a JPEG or PNG and try again.`;
    case 422:
      return `${name} could not be read as an image. Export it as a JPEG or PNG and try again.`;
    case 429:
      return `${name}: too many uploads at once. Wait a moment and try again.`;
    default:
      return `${name} did not upload (${status}). Try again.`;
  }
}

export interface UploadOpts {
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

/** POST /api/projects/{pid}/uploads (multipart, field "file"), through XMLHttpRequest because fetch reports no upload
 * progress. Rejects with an ApiError whose message is ready to show: the server's own sentence when it sent one
 * (prefixed with the file name), else a plain one for the status. */
export function uploadImage(pid: string, file: Blob, name: string, opts: UploadOpts = {}): Promise<Upload> {
  return new Promise((resolve, reject) => {
    if (opts.signal?.aborted) {
      reject(new ApiError(0, "aborted", "Upload cancelled."));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/${encodeURIComponent(pid)}/uploads`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) opts.onProgress?.(Math.min(0.99, e.loaded / e.total));
    };
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText || "null");
      } catch {
        /* not JSON (a proxy's error page) */
      }
      if (xhr.status === 401) useAuth.getState().setAuthed(false);
      if (xhr.status >= 200 && xhr.status < 300 && body && typeof body.id === "string") {
        opts.onProgress?.(1);
        resolve(body as Upload);
        return;
      }
      const e = body?.error || {};
      reject(new ApiError(xhr.status, e.code || "error", friendlyUploadError(xhr.status, e.message, name), e.details));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", friendlyUploadError(0, undefined, name)));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload cancelled."));
    opts.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    const form = new FormData();
    form.append("file", file, name);
    xhr.send(form);
  });
}
