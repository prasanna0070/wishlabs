// Start from an image (_notes/image-contract.md): which files the composers take, image URLs for the owner and the
// share views, the chat body, and the per-message upload queue (the call itself is api.uploadImage).
// Everything except shrinkImage is pure and unit-tested in uploads.test.ts.
import type { UploadOpts } from "./api";
import type { Attachment, ChatMessage, Project, Upload } from "./types";
import { apiRoot, type Ctx } from "./urls";

export const IMAGE_MAX_BYTES = 15 * 1024 * 1024;
export const IMAGES_PER_MESSAGE = 4;
export const IMAGES_PER_PROJECT = 12;
/** What the server stores (and the agent reads) for a message that is only images (contract §3). */
export const IMAGE_ONLY_TEXT = "Here is what I have in mind.";

/** <input accept>: images (a phone then offers the camera and the photo library) plus the text files we read. */
export const IMAGE_ACCEPT = "image/*,.heic,.heif";
export const TEXT_ACCEPT = ".txt,.md,.markdown,.json,.csv,.ini,.yaml,.yml,.toml,text/*";
export const FILE_ACCEPT = `${IMAGE_ACCEPT},${TEXT_ACCEPT}`;

const IMAGE_MIME = new Set(["image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"]);
const IMAGE_EXT = /\.(jpe?g|png|webp|heic|heif)$/i;
const OTHER_IMAGE_EXT = /\.(gif|bmp|tiff?|svg|avif|ico|psd)$/i;

export interface FileLike {
  name: string;
  type: string;
  size: number;
}

/** Any image the user may mean to attach, supported or not (unsupported ones get a friendly reason). */
export function looksLikeImage(f: Pick<FileLike, "name" | "type">): boolean {
  return (f.type || "").toLowerCase().startsWith("image/") || IMAGE_EXT.test(f.name || "") || OTHER_IMAGE_EXT.test(f.name || "");
}

/** JPEG, PNG, WebP or HEIC/HEIF, by type or (when the browser gives no type, as for HEIC on Windows) by extension. */
export function isSupportedImage(f: Pick<FileLike, "name" | "type">): boolean {
  const t = (f.type || "").toLowerCase();
  if (IMAGE_MIME.has(t)) return true;
  return (!t || t === "application/octet-stream") && IMAGE_EXT.test(f.name || "");
}

function kindWord(f: Pick<FileLike, "name" | "type">): string {
  const ext = /\.([a-z0-9]+)$/i.exec(f.name || "")?.[1];
  const sub = /^image\/([a-z0-9.+-]+)/i.exec(f.type || "")?.[1];
  return (ext || sub || "This").replace(/^svg\+xml$/i, "svg").toUpperCase();
}

/** Why this image cannot be attached, or null when it can. */
export function imageProblem(f: FileLike): string | null {
  const name = f.name || "The image";
  if (!isSupportedImage(f)) return `${name}: ${kindWord(f)} images are not supported. Use a JPEG, PNG, WebP or HEIC image (a screenshot works too).`;
  if (!f.size) return `${name} is empty.`;
  if (f.size > IMAGE_MAX_BYTES) return `${name} is larger than 15 MB. Export a smaller JPEG or take a screenshot of it.`;
  return null;
}

/** Split picked, dropped or pasted files into images to upload, text files to read, and friendly errors.
 * `room` is how many more images this message can take; `projectFull` says the project's limit is what binds. */
export function sortFiles<F extends FileLike>(list: ArrayLike<F>, room: number, projectFull = false): { images: F[]; texts: F[]; errors: string[] } {
  const images: F[] = [];
  const texts: F[] = [];
  const errors: string[] = [];
  const over: string[] = [];
  for (const f of Array.from(list)) {
    if (!looksLikeImage(f)) {
      texts.push(f);
      continue;
    }
    const why = imageProblem(f);
    if (why) errors.push(why);
    else if (images.length >= room) over.push(f.name || "image");
    else images.push(f);
  }
  if (over.length) {
    const what = `${over.join(", ")} ${over.length === 1 ? "was" : "were"} not added.`;
    errors.push(projectFull ? `A project can hold up to ${IMAGES_PER_PROJECT} images: ${what}` : `Up to ${IMAGES_PER_MESSAGE} images per message: ${what}`);
  }
  return { images, texts, errors };
}

type ClipItem = { kind: string; type: string; getAsFile: () => File | null };
export interface ClipLike {
  files?: ArrayLike<File> | null;
  items?: ArrayLike<ClipItem> | null;
  types?: ArrayLike<string> | readonly string[] | null;
}

const pad = (n: number) => String(n).padStart(2, "0");
const EXT_OF: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif", "image/gif": "gif" };

/** A pasted screenshot is called "image.png" (or nothing): give it a name the user can tell apart. */
export function pastedName(f: { name?: string; type?: string }, now: Date = new Date()): string {
  const name = (f.name || "").trim();
  if (name && !/^image\.(png|jpe?g|gif|webp|heic|heif|tiff?|bmp)$/i.test(name)) return name;
  const ext = EXT_OF[(f.type || "").toLowerCase()] || /\.([a-z0-9]+)$/i.exec(name)?.[1] || "png";
  return `pasted-image-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.${ext}`;
}

function renamed(f: File, name: string): File {
  if (f.name === name) return f;
  try {
    return new File([f], name, { type: f.type, lastModified: f.lastModified });
  } catch {
    return f; // very old browsers: keep the generic name
  }
}

/** The images in a paste (Cmd+V of a screenshot, "Copy image" in a browser or in ChatGPT). Files first; the item list
 * only when the browser put the image there alone. Generic names become "pasted-image-HHMMSS.png". */
export function clipboardImages(dt: ClipLike | null | undefined, now: Date = new Date()): File[] {
  if (!dt) return [];
  let found = Array.from(dt.files || []).filter((f) => looksLikeImage(f));
  if (!found.length) {
    found = [];
    for (const it of Array.from(dt.items || [])) {
      if (it.kind !== "file" || !looksLikeImage({ name: "", type: it.type })) continue;
      const f = it.getAsFile();
      if (f) found.push(f);
    }
  }
  return found.map((f) => renamed(f, pastedName(f, now)));
}

/** True when the paste also carries plain text (then the text is pasted as usual, next to the image). */
export function clipboardHasText(dt: ClipLike | null | undefined): boolean {
  return Array.from(dt?.types || []).includes("text/plain");
}

/** True when a drag carries files (not text or a link being dragged inside the page). */
export function dragHasFiles(dt: { types?: ArrayLike<string> | readonly string[] | null } | null | undefined): boolean {
  return Array.from(dt?.types || []).includes("Files");
}

/** The chat body for typed words, read text files and uploaded images. Images alone are a valid message (the server
 * then stores IMAGE_ONLY_TEXT). Null when there is nothing to send. */
export function chatBody(text: string, textFiles: string[], images: { id: string }[]): { text: string; attachments?: { id: string }[] } | null {
  const words = [text.trim(), ...textFiles.map((t) => t.trim())].filter(Boolean).join("\n\n");
  const ids = images.filter((u) => u && u.id).map((u) => ({ id: u.id }));
  if (!words && !ids.length) return null;
  return ids.length ? { text: words, attachments: ids } : { text: words };
}

/** URL of an uploaded image (or its 384 px thumbnail) for this view: the owner's API, or the share link's. */
export function uploadUrl(ctx: Ctx, id: string, thumb = false): string {
  return `${apiRoot(ctx)}/uploads/${encodeURIComponent(id)}${thumb ? "?thumb=1" : ""}`;
}

/** The images on a stored message (top-level `attachments`, as the contract says; `payload.attachments` tolerated). */
export function messageAttachments(m: Pick<ChatMessage, "attachments" | "payload"> | null | undefined): Attachment[] {
  const a = m?.attachments ?? m?.payload?.attachments;
  return Array.isArray(a) ? a.filter((x): x is Attachment => !!x && typeof x.id === "string" && !!x.id) : [];
}

/** Every image the user sent in this project, oldest first, each once. */
export function projectImages(p: Pick<Project, "chat"> | null | undefined): Attachment[] {
  const seen = new Set<string>();
  const out: Attachment[] = [];
  for (const m of p?.chat || []) {
    if (m.role !== "user") continue;
    for (const a of messageAttachments(m)) {
      if (seen.has(a.id)) continue;
      seen.add(a.id);
      out.push(a);
    }
  }
  return out;
}

/** Does this project start from (or contain) an image? Drives the share setting and the card. */
export function hasImages(p: Pick<Project, "chat" | "image_brief"> | null | undefined): boolean {
  return projectImages(p).length > 0 || !!p?.image_brief;
}

/** May this view load the raw images? The owner always; a share link only when the owner turned share_uploads on
 * (a missing flag means off: contract Change 1). */
export function imagesShown(ctx: Ctx | null | undefined, p: Pick<Project, "share_uploads"> | null | undefined): "yes" | "no" {
  if (!ctx?.share) return "yes";
  return p?.share_uploads === true ? "yes" : "no";
}

/** How many images a message had on a share link that hides them (`attachments_hidden`, contract Change 1). */
export function hiddenImages(m: Pick<ChatMessage, "attachments_hidden"> | null | undefined): number {
  const n = Number(m?.attachments_hidden);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** Images this project already holds on the server, not counting the ones in the composer now (12 a project). */
export function storedImageCount(p: Pick<Project, "chat" | "uploads"> | null | undefined, inComposer: Iterable<string> = []): number {
  const skip = new Set(inComposer);
  const sent = projectImages(p).filter((a) => !skip.has(a.id)).length;
  const all = Array.isArray(p?.uploads) ? p!.uploads.filter((u) => u && !skip.has(u.id)).length : 0;
  return Math.max(sent, all);
}

/** Short alt text from a file name: "acme-concept_sheet.PNG" -> "acme concept sheet". */
export function altFromName(name: string | null | undefined, fallback = "Your image"): string {
  const base = (name || "").replace(/\.[a-z0-9]{2,5}$/i, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return base || fallback;
}

/** Big photos are shrunk in the browser before they go up (long edge 2048 px, JPEG): the server does the same, and
 * venue Wi-Fi uploads at about 100 KB/s. A small file with many pixels (a flat 0.2 MB PNG can be 50 MP) is shrunk too,
 * so the server never decodes it at full size. Small images, HEIC (browsers cannot draw it) and any failure keep the
 * original. */
export async function shrinkImage(file: File, maxEdge = 2048, minBytes = 1_200_000): Promise<Blob> {
  try {
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) return file;
    if (typeof createImageBitmap !== "function" || typeof document === "undefined") return file;
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
    const manyPixels = bmp.width * bmp.height > maxEdge * maxEdge;
    if (file.size < minBytes && !manyPixels) {
      bmp.close?.();
      return file;
    }
    const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * scale));
    const h = Math.max(1, Math.round(bmp.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const g = canvas.getContext("2d");
    if (!g) return file;
    g.fillStyle = "#ffffff"; // transparent PNG sketches stay on white, not black
    g.fillRect(0, 0, w, h);
    g.drawImage(bmp, 0, 0, w, h);
    bmp.close?.();
    const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", 0.9));
    return blob && (manyPixels || blob.size < file.size) ? blob : file;
  } catch {
    return file;
  }
}

// ---------------------------------------------------------------- the composer's images (one message)

export interface DraftImage {
  key: string;
  name: string;
  file: File | null; // null for an image restored from an unsent message (already uploaded)
  preview: string | null; // a local object URL, or the server thumbnail once known
  state: "waiting" | "uploading" | "done" | "error";
  progress: number; // 0..1
  upload?: Upload;
  error?: string;
}

export interface QueueDeps {
  upload: (pid: string, file: Blob, name: string, opts: UploadOpts) => Promise<Upload>;
  /** An uploaded image the user removed before sending: DELETE it on the server (contract Change 1). */
  discard?: (pid: string, id: string) => void;
  prepare?: (f: File) => Promise<Blob>;
  preview?: (f: File) => string | null;
  revoke?: (url: string) => void;
}

let keySeq = 0;

/** The images of the message being written. Uploads one at a time (the venue uplink is slow) as soon as the project
 * is known: at once in a project's composer, after "Start" on the home page. Plain class, so it is unit-tested. */
export class ImageQueue {
  items: DraftImage[] = [];
  private pid: string | null = null;
  private active: { key: string; abort: AbortController } | null = null;
  private listeners = new Set<() => void>();
  private waiters: { resolve: (u: Upload[]) => void; reject: (e: Error) => void }[] = [];

  constructor(private deps: QueueDeps) {}

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  };
  getItems = () => this.items;

  private emit() {
    this.items = [...this.items];
    this.listeners.forEach((f) => f());
  }
  private patch(key: string, p: Partial<DraftImage>) {
    const i = this.items.findIndex((x) => x.key === key);
    if (i < 0) return;
    this.items[i] = { ...this.items[i], ...p };
    this.emit();
  }

  get count(): number {
    return this.items.length;
  }
  /** Something is still going up (or waiting for the project). */
  get busy(): boolean {
    return this.items.some((x) => x.state === "uploading" || x.state === "waiting");
  }
  get failed(): DraftImage[] {
    return this.items.filter((x) => x.state === "error");
  }
  uploads(): Upload[] {
    return this.items.filter((x) => x.state === "done" && x.upload).map((x) => x.upload!);
  }

  /** Add checked image files (see sortFiles). */
  add(files: File[]) {
    for (const f of files) {
      this.items.push({ key: `img${++keySeq}`, name: f.name, file: f, preview: this.deps.preview?.(f) ?? null, state: "waiting", progress: 0 });
    }
    this.emit();
    void this.pump();
  }

  /** Put back images that were already uploaded (a message the studio did not take). */
  restore(list: (Partial<Upload> & { id: string; thumb?: string | null })[]) {
    for (const u of list) {
      if (this.items.some((x) => x.upload?.id === u.id)) continue;
      const up = { name: "image", mime: "image/jpeg", bytes: 0, width: 0, height: 0, url: "", thumb_url: "", ...u } as Upload;
      this.items.push({ key: `img${++keySeq}`, name: up.name, file: null, preview: u.thumb ?? up.thumb_url ?? null, state: "done", progress: 1, upload: up });
    }
    this.emit();
  }

  remove(key: string) {
    const it = this.items.find((x) => x.key === key);
    if (!it) return;
    if (this.active?.key === key) {
      this.active.abort.abort();
      this.active = null;
    }
    if (it.preview && it.file) this.deps.revoke?.(it.preview);
    if (it.state === "done" && it.upload && this.pid) this.deps.discard?.(this.pid, it.upload.id);
    this.items = this.items.filter((x) => x.key !== key);
    this.emit();
    void this.pump();
  }

  /** Try a failed image again. */
  retry(key: string) {
    const it = this.items.find((x) => x.key === key);
    if (!it || it.state !== "error" || !it.file) return;
    this.patch(key, { state: "waiting", progress: 0, error: undefined });
    void this.pump();
  }

  clear() {
    this.active?.abort.abort();
    this.active = null;
    for (const it of this.items) if (it.preview && it.file) this.deps.revoke?.(it.preview);
    this.items = [];
    this.emit();
    this.settle();
  }

  setProject(pid: string | null) {
    this.pid = pid;
    void this.pump();
  }

  /** Resolves with every upload once nothing is waiting; rejects with the first failure (the image stays, marked). */
  whenSettled(): Promise<Upload[]> {
    return new Promise((resolve, reject) => {
      this.waiters.push({ resolve, reject });
      this.settle();
    });
  }

  private settle() {
    if (!this.waiters.length) return;
    if (this.items.some((x) => x.state === "uploading" || (x.state === "waiting" && this.pid))) return;
    if (this.items.some((x) => x.state === "waiting")) return; // no project yet
    const ws = this.waiters.splice(0);
    const bad = this.failed[0];
    for (const w of ws) bad ? w.reject(new Error(bad.error || `${bad.name} did not upload.`)) : w.resolve(this.uploads());
  }

  private async pump(): Promise<void> {
    if (!this.pid || this.active) return this.settle();
    const next = this.items.find((x) => x.state === "waiting");
    if (!next || !next.file) return this.settle();
    const pid = this.pid;
    const abort = new AbortController();
    this.active = { key: next.key, abort };
    this.patch(next.key, { state: "uploading", progress: 0, error: undefined });
    try {
      const body = this.deps.prepare ? await this.deps.prepare(next.file) : next.file;
      if (abort.signal.aborted) return;
      const up = await this.deps.upload(pid, body, next.name, { signal: abort.signal, onProgress: (f) => this.patch(next.key, { progress: f }) });
      if (abort.signal.aborted) return;
      this.patch(next.key, { state: "done", progress: 1, upload: up });
    } catch (e: any) {
      if (abort.signal.aborted) return;
      this.patch(next.key, { state: "error", error: e?.message || `${next.name} did not upload.` });
    } finally {
      if (this.active?.key === next.key) this.active = null;
    }
    return this.pump();
  }

  dispose() {
    this.active?.abort.abort();
    this.active = null;
    for (const it of this.items) if (it.preview && it.file) this.deps.revoke?.(it.preview);
    this.listeners.clear();
  }
}

/** How many more images this message can take (4 a message, 12 a project), and which limit binds. */
export function imageRoom(inMessage: number, inProject: number): { room: number; projectFull: boolean } {
  const msg = IMAGES_PER_MESSAGE - inMessage;
  const proj = IMAGES_PER_PROJECT - inProject - inMessage;
  return { room: Math.max(0, Math.min(msg, proj)), projectFull: proj < msg };
}

// ---------------------------------------------------------------- the first message, carried from Home to the project

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const pendingKey = (pid: string) => `gs.pending.${pid}`;
const pendingAttKey = (pid: string) => `gs.pending.att.${pid}`;

/** Home keeps the first message for the project page, which sends it (`gs.pending.<pid>` holds the words as before;
 * `gs.pending.att.<pid>` the uploaded images). False when storage is unavailable. */
export function savePending(pid: string, text: string, atts: Attachment[], store: StorageLike | null = safeSession()): boolean {
  if (!store) return false;
  try {
    store.setItem(pendingKey(pid), text);
    if (atts.length) store.setItem(pendingAttKey(pid), JSON.stringify(atts.map(({ id, name, mime, width, height }) => ({ id, name, mime, width, height }))));
    else store.removeItem(pendingAttKey(pid));
    return true;
  } catch {
    return false;
  }
}

/** The first message Home left for this project (and forget it), as a chat body; null when there is none. */
export function takePending(pid: string, store: StorageLike | null = safeSession()): { text: string; attachments?: { id: string }[] } | null {
  if (!store) return null;
  let text: string | null = null;
  let atts: Attachment[] = [];
  try {
    text = store.getItem(pendingKey(pid));
    const raw = store.getItem(pendingAttKey(pid));
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) atts = parsed.filter((a) => a && typeof a.id === "string");
    }
    store.removeItem(pendingKey(pid));
    store.removeItem(pendingAttKey(pid));
  } catch {
    /* unreadable: nothing pending */
  }
  return chatBody(text || "", [], atts);
}

function safeSession(): StorageLike | null {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}
