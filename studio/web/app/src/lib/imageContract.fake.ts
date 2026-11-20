// Test-only: an in-memory stand-in for the studio's image endpoints, written from _notes/image-contract.md, and an
// XMLHttpRequest look-alike that routes POST /api/projects/{pid}/uploads to it (MSW-style: the code under test makes
// its real network call). Not imported by the app.
import type { Upload } from "./types";

export const MB = 1024 * 1024;

export interface FakeProject {
  id: string;
  share: string | null;
  share_uploads: boolean;
  chat: any[];
}

export interface FakeReply {
  status: number;
  body: any;
}

const err = (status: number, code: string, message: string): FakeReply => ({ status, body: { error: { code, message } } });

/** The real type from the first bytes, never from the name (contract §1). */
export function sniff(b: Uint8Array): "jpeg" | "png" | "webp" | "heic" | null {
  const at = (i: number, s: string) => [...s].every((c, k) => b[i + k] === c.charCodeAt(0));
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "jpeg";
  if (b[0] === 0x89 && at(1, "PNG")) return "png";
  if (at(0, "RIFF") && at(8, "WEBP")) return "webp";
  if (at(4, "ftyp") && ["heic", "heix", "hevc", "mif1", "msf1", "heif"].some((x) => at(8, x))) return "heic";
  return null;
}

function pngSize(b: Uint8Array): [number, number] | null {
  if (b.length < 24) return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  return [dv.getUint32(16), dv.getUint32(20)];
}

export function sanitiseName(n: string): string {
  const base = (n || "image").split(/[\\/]/).pop() || "image";
  return base.replace(/[^A-Za-z0-9._ -]+/g, "_").slice(0, 80) || "image";
}

let seq = 0;
const newId = () => `u_${(++seq).toString(36).padStart(4, "0")}${Math.random().toString(36).slice(2, 8)}`.slice(0, 12).toLowerCase().padEnd(12, "0");

/** The server side of the contract, in memory. */
export class ImageContractFake {
  projects = new Map<string, FakeProject>();
  uploads = new Map<string, Upload & { pid: string }>();
  heicSupported = false; // pillow-heif on the server?
  calls: { method: string; path: string }[] = [];

  addProject(id: string, share: string | null = null): FakeProject {
    const p: FakeProject = { id, share, share_uploads: false, chat: [] };
    this.projects.set(id, p);
    return p;
  }

  /** POST /api/projects/{pid}/uploads, multipart field "file". */
  upload(pid: string, bytes: Uint8Array | null, name: string, authed = true): FakeReply {
    this.calls.push({ method: "POST", path: `/api/projects/${pid}/uploads` });
    if (!authed) return err(401, "unauthorized", "Sign in again.");
    if (!this.projects.has(pid)) return err(404, "not_found", "Project not found.");
    if (!bytes) return err(422, "invalid", 'Send the image as multipart field "file".');
    if (bytes.length > 15 * MB) return err(413, "too_large", "That image is larger than 15 MB. Please export a smaller JPEG.");
    const kind = sniff(bytes);
    if (!kind) return err(415, "unsupported", "That file is not a JPEG, PNG, WebP or HEIC image.");
    if (kind === "heic" && !this.heicSupported) return err(415, "unsupported", "HEIC is not supported yet, please export as JPEG");
    const mine = [...this.uploads.values()].filter((u) => u.pid === pid).length;
    if (mine >= 12) return err(409, "limit", "This project already has 12 images, the most it can hold.");
    let [w, h] = (kind === "png" ? pngSize(bytes) : null) || [1200, 1600];
    const s = Math.min(1, 2048 / Math.max(w, h));
    [w, h] = [Math.round(w * s), Math.round(h * s)];
    const id = newId();
    const up: Upload = {
      id,
      name: sanitiseName(name),
      mime: "image/jpeg",
      bytes: Math.max(1, Math.round(bytes.length * 0.4)),
      width: w,
      height: h,
      url: `/api/projects/${pid}/uploads/${id}`,
      thumb_url: `/api/projects/${pid}/uploads/${id}?thumb=1`,
    };
    this.uploads.set(id, { ...up, pid });
    return { status: 201, body: up };
  }

  /** GET /api/projects/{pid}/uploads/{id}[?thumb=1] and GET /api/share/{token}/uploads/{id}[?thumb=1]. */
  get(path: string, authed = true): FakeReply {
    this.calls.push({ method: "GET", path });
    const own = /^\/api\/projects\/([^/]+)\/uploads\/([^/?]+)(\?thumb=1)?$/.exec(path);
    if (own) {
      if (!authed) return err(401, "unauthorized", "Sign in again.");
      const u = this.uploads.get(own[2]);
      return u && u.pid === own[1] ? { status: 200, body: "jpeg-bytes" } : err(404, "not_found", "Not found.");
    }
    const shared = /^\/api\/share\/([^/]+)\/uploads\/([^/?]+)(\?thumb=1)?$/.exec(path);
    if (shared) {
      const p = [...this.projects.values()].find((x) => x.share === shared[1]);
      const u = this.uploads.get(shared[2]);
      // only images a chat message carries: one removed from the composer or never sent stays private
      const sent = !!p && p.chat.some((m) => (m.attachments || []).some((a: { id: string }) => a.id === shared[2]));
      if (!p || !u || u.pid !== p.id || !p.share_uploads || !sent) return err(404, "not_found", "Not found.");
      return { status: 200, body: "jpeg-bytes" };
    }
    return err(404, "not_found", "Not found.");
  }

  /** PATCH /api/projects/{pid} {"share_uploads": bool}. */
  patch(pid: string, body: { share_uploads?: unknown }): FakeReply {
    const p = this.projects.get(pid);
    if (!p) return err(404, "not_found", "Project not found.");
    if ("share_uploads" in body) {
      if (typeof body.share_uploads !== "boolean") return err(422, "invalid", "share_uploads must be true or false.");
      p.share_uploads = body.share_uploads;
    }
    return { status: 200, body: { id: p.id, share_uploads: p.share_uploads } };
  }

  /** POST /api/projects/{pid}/chat: attachments must be this project's, at most 4; text may be empty with an image. */
  chat(pid: string, body: { text?: string; attachments?: { id: string }[] }): FakeReply {
    const p = this.projects.get(pid);
    if (!p) return err(404, "not_found", "Project not found.");
    const ids = (body.attachments || []).map((a) => a?.id);
    if (ids.length > 4) return err(422, "invalid", "Up to 4 images per message.");
    for (const id of ids) {
      const u = this.uploads.get(id);
      if (!u || u.pid !== pid) return err(422, "invalid", `Unknown image ${id}.`);
    }
    const text = (body.text || "").trim();
    if (!text && !ids.length) return err(422, "invalid", "Say something or add an image.");
    const atts = ids.map((id) => {
      const u = this.uploads.get(id)!;
      return { id, name: u.name, mime: u.mime, width: u.width, height: u.height };
    });
    const msg = { id: `m_${p.chat.length + 1}`, role: "user", kind: "text", text: text || "Here is what I have in mind.", payload: null, ts: "2026-11-20T10:00:00Z", ...(atts.length ? { attachments: atts } : {}) };
    p.chat.push(msg);
    return { status: 200, body: msg };
  }
}

/** A stand-in for XMLHttpRequest that answers POST /api/projects/{pid}/uploads from an ImageContractFake, with upload
 * progress in `steps` events. `FakeXHR.fake` must be set; `FakeXHR.net` makes the next request fail like a dropped
 * connection; `FakeXHR.authed = false` answers 401. */
export class FakeXHR {
  static fake: ImageContractFake | null = null;
  static net = false;
  static authed = true;
  static steps = 3;
  static sent: { url: string; field: string | null; name: string | null; type: string | null; size: number; headers: Record<string, string> }[] = [];

  method = "";
  url = "";
  status = 0;
  responseText = "";
  withCredentials = false;
  headers: Record<string, string> = {};
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private aborted = false;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  abort() {
    if (this.aborted) return;
    this.aborted = true;
    queueMicrotask(() => this.onabort?.());
  }
  send(form: FormData) {
    void this.run(form);
  }
  private async run(form: FormData) {
    const file = form.get("file");
    const blob = file && typeof file !== "string" ? (file as File) : null;
    FakeXHR.sent.push({ url: this.url, field: blob ? "file" : null, name: blob?.name ?? null, type: blob?.type ?? null, size: blob?.size ?? 0, headers: { ...this.headers } });
    const total = blob?.size || 1;
    for (let i = 1; i <= FakeXHR.steps; i++) {
      await new Promise((r) => setTimeout(r, 0));
      if (this.aborted) return;
      this.upload.onprogress?.({ lengthComputable: true, loaded: Math.round((total * i) / (FakeXHR.steps + 1)), total });
    }
    await new Promise((r) => setTimeout(r, 0));
    if (this.aborted) return;
    if (FakeXHR.net) {
      FakeXHR.net = false;
      this.onerror?.();
      return;
    }
    const m = /^\/api\/projects\/([^/]+)\/uploads$/.exec(this.url);
    const fake = FakeXHR.fake!;
    const bytes = blob ? new Uint8Array(await blob.arrayBuffer()) : null;
    const r = m && this.method === "POST" ? fake.upload(decodeURIComponent(m[1]), bytes, blob?.name || "image", FakeXHR.authed) : { status: 404, body: { error: { code: "not_found", message: "Not found." } } };
    this.status = r.status;
    this.responseText = JSON.stringify(r.body);
    this.onload?.();
  }
}

// ---------------------------------------------------------------- tiny real image bytes for the fakes

/** The 8-byte PNG signature plus an IHDR chunk header with this size (enough for sniffing and the size). */
export function pngBytes(w = 1200, h = 1600, pad = 64): Uint8Array {
  const b = new Uint8Array(24 + pad);
  b.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  const dv = new DataView(b.buffer);
  dv.setUint32(16, w);
  dv.setUint32(20, h);
  return b;
}
export const jpegBytes = (n = 64) => Uint8Array.from([0xff, 0xd8, 0xff, 0xe0, ...new Array(Math.max(0, n - 4)).fill(1)]);
export const heicBytes = () => Uint8Array.from([0, 0, 0, 24, ...[..."ftypheic"].map((c) => c.charCodeAt(0)), 0, 0, 0, 0, ...new Array(16).fill(0)]);
export const gifBytes = () => Uint8Array.from([..."GIF89a"].map((c) => c.charCodeAt(0)).concat(new Array(16).fill(0)));
