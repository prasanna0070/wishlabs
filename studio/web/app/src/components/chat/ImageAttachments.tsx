// Images in the composers and in the chat (start from an image, _notes/image-contract.md): the upload queue hook,
// thumbnails with progress and remove buttons, a sent message's images, and a keyboard-friendly lightbox.
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight, ExternalLink, ImageOff, Image as ImageIcon, RotateCw, X } from "lucide-react";
import { api } from "../../lib/api";
import { altFromName, clipboardImages, dragHasFiles, ImageQueue, shrinkImage, uploadUrl, type DraftImage } from "../../lib/uploads";
import type { Attachment } from "../../lib/types";
import type { Ctx } from "../../lib/urls";
import "./images.css";

/** One message's images: uploads start as soon as `pid` is known (null on the home page until Start). */
export function useImageQueue(pid: string | null) {
  const q = useMemo(
    () =>
      new ImageQueue({
        upload: (p, file, name, opts) => api.uploadImage(p, file, name, opts),
        discard: (p, id) => void api.deleteUpload(p, id).catch(() => {}), // frees the slot (12 a project)
        prepare: (f) => shrinkImage(f),
        preview: (f) => (typeof URL !== "undefined" && URL.createObjectURL ? URL.createObjectURL(f) : null),
        revoke: (u) => {
          if (u.startsWith("blob:")) URL.revokeObjectURL(u);
        },
      }),
    [],
  );
  const items = useSyncExternalStore(q.subscribe, q.getItems, q.getItems);
  useEffect(() => {
    q.setProject(pid);
  }, [q, pid]);
  useEffect(() => () => q.dispose(), [q]);
  return { q, items };
}

/** Files dropped anywhere on the page go to this composer, instead of the browser opening the image in place of the
 * studio. A drop the composer itself handled (preventDefault) is left alone. `onDrag` lights up the drop target. */
export function useWindowFileDrop(onFiles: (files: File[]) => void, enabled = true, onDrag?: (on: boolean) => void) {
  const cb = useRef({ onFiles, onDrag });
  cb.current = { onFiles, onDrag };
  useEffect(() => {
    if (!enabled || typeof window === "undefined" || typeof window.addEventListener !== "function") return;
    let depth = 0;
    const lit = (on: boolean) => cb.current.onDrag?.(on);
    const enter = (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer)) return;
      depth += 1;
      lit(true);
    };
    const over = (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer)) return;
      e.preventDefault(); // without this the drop opens the file in the tab
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const leave = (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) lit(false);
    };
    const drop = (e: DragEvent) => {
      depth = 0;
      lit(false);
      const files = e.dataTransfer?.files;
      if (!files?.length || e.defaultPrevented) return;
      e.preventDefault();
      cb.current.onFiles(Array.from(files));
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [enabled]);
}

/** Cmd+V of an image while the focus is not in a text field (on a button, or nowhere) also attaches it. Text fields
 * handle their own pastes (the composer's onPaste), so this never adds an image twice. */
export function useWindowPaste(onFiles: (files: File[]) => void, enabled = true) {
  const cb = useRef(onFiles);
  cb.current = onFiles;
  useEffect(() => {
    if (!enabled || typeof window === "undefined" || typeof window.addEventListener !== "function") return;
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.defaultPrevented || (t && (t.closest?.("input, textarea, [contenteditable=''], [contenteditable='true']") || t.isContentEditable))) return;
      const pics = clipboardImages(e.clipboardData);
      if (!pics.length) return;
      e.preventDefault();
      cb.current(pics);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [enabled]);
}

/** The composer's images: thumbnail, upload progress, a retry for a failed one, and a remove button. */
export function DraftImages({ items, onRemove, onRetry }: { items: DraftImage[]; onRemove: (key: string) => void; onRetry: (key: string) => void }) {
  if (!items.length) return null;
  return (
    <ul className="img-drafts" aria-label="Images to send">
      {items.map((it) => (
        <DraftThumb key={it.key} it={it} onRemove={onRemove} onRetry={onRetry} />
      ))}
    </ul>
  );
}

function DraftThumb({ it, onRemove, onRetry }: { it: DraftImage; onRemove: (key: string) => void; onRetry: (key: string) => void }) {
  const [broken, setBroken] = useState(false); // HEIC: most browsers cannot draw it before the server converts it
  const src = broken ? it.upload?.thumb_url || null : it.preview || it.upload?.thumb_url || null;
  const pct = Math.round((it.progress || 0) * 100);
  const label = it.state === "error" ? `${it.name}: ${it.error || "did not upload"}` : it.state === "uploading" ? `${it.name}, uploading ${pct}%` : it.name;
  return (
    <li className={`img-draft ${it.state}`} title={label}>
      {src ? (
        <img src={src} alt={altFromName(it.name)} onError={() => setBroken(true)} draggable={false} />
      ) : (
        <span className="img-draft-ph" aria-label={altFromName(it.name)} role="img">
          <ImageIcon size={18} aria-hidden="true" />
          <span>{(/\.([a-z0-9]+)$/i.exec(it.name)?.[1] || "img").toUpperCase()}</span>
        </span>
      )}
      {it.state === "uploading" ? (
        <span className="img-draft-bar" role="progressbar" aria-label={`Uploading ${it.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <span style={{ width: `${Math.max(4, pct)}%` }} />
        </span>
      ) : null}
      {it.state === "error" ? (
        <button type="button" className="img-draft-retry" onClick={() => onRetry(it.key)} aria-label={`Try uploading ${it.name} again`} title={it.error || "Try again"}>
          <RotateCw size={15} />
        </button>
      ) : null}
      <button type="button" className="img-draft-x" onClick={() => onRemove(it.key)} aria-label={`Remove ${it.name}`} title={`Remove ${it.name}`}>
        <X size={12} />
      </button>
    </li>
  );
}

/** A thumbnail that turns into a "not shared" tile when the server will not give it (share link, private image). */
function Thumb({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <span className="msg-img-off" role="img" aria-label={`${alt} (not available)`}>
        <ImageOff size={18} aria-hidden="true" />
      </span>
    );
  return <img src={src} alt={alt} loading="lazy" decoding="async" onError={() => setFailed(true)} draggable={false} />;
}

/** The images of a sent message: thumbnails that open full size. On a share link they show only when the owner
 * allowed it (`shown`); otherwise one quiet line says the images stay private (`hidden`: the count the share JSON
 * gives instead of the attachments). */
export function MessageImages({ atts, ctx, shown, hidden = 0 }: { atts: Attachment[]; ctx: Ctx | null; shown: "yes" | "no"; hidden?: number }) {
  const [open, setOpen] = useState<number | null>(null);
  const n = atts.length || hidden;
  if (!n) return null;
  if (shown === "no" || !atts.length || !ctx)
    return (
      <div className="msg-imgs-private">
        <ImageOff size={14} aria-hidden="true" /> {n === 1 ? "The image is" : `${n} images are`} not shared on this link.
      </div>
    );
  const list = atts.map((a) => ({ id: a.id, alt: altFromName(a.name), name: a.name || "image", src: uploadUrl(ctx, a.id), thumb: uploadUrl(ctx, a.id, true), width: a.width, height: a.height }));
  return (
    <>
      <div className={`msg-imgs n${Math.min(list.length, 4)}`}>
        {list.map((a, i) => (
          <button key={a.id} type="button" className="msg-img" onClick={() => setOpen(i)} aria-label={`Open image ${a.alt}`} title={a.name}>
            <Thumb src={a.thumb} alt={a.alt} />
          </button>
        ))}
      </div>
      {open != null ? <ImageLightbox images={list} start={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

export interface LightboxImage {
  id: string;
  src: string;
  alt: string;
  name: string;
  width?: number;
  height?: number;
}

/** Full-size view: Escape or the scrim closes it, arrow keys step through, focus returns to what opened it. */
export function ImageLightbox({ images, start = 0, onClose }: { images: LightboxImage[]; start?: number; onClose: () => void }) {
  const [i, setI] = useState(Math.min(Math.max(0, start), images.length - 1));
  const closeBtn = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(typeof document !== "undefined" ? document.activeElement : null);
  const img = images[i];
  useEffect(() => {
    closeBtn.current?.focus();
    const back = opener.current;
    return () => {
      if (back instanceof HTMLElement) back.focus();
    };
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "Tab" && box.current) {
        // keyboard focus stays inside the dialog (aria-modal): Tab and Shift+Tab wrap around its controls
        const f = Array.from(box.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));
        if (!f.length) return;
        const at = document.activeElement;
        const inside = at instanceof HTMLElement && box.current.contains(at);
        if (e.shiftKey && (!inside || at === f[0])) {
          e.preventDefault();
          f[f.length - 1].focus();
        } else if (!e.shiftKey && (!inside || at === f[f.length - 1])) {
          e.preventDefault();
          f[0].focus();
        }
      } else if (e.key === "ArrowRight" && images.length > 1) setI((x) => (x + 1) % images.length);
      else if (e.key === "ArrowLeft" && images.length > 1) setI((x) => (x - 1 + images.length) % images.length);
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [images.length, onClose]);
  if (!img) return null;
  return createPortal(
    <>
      <div className="scrim img-scrim" onClick={onClose} />
      <div ref={box} className="img-lightbox" role="dialog" aria-modal="true" aria-label={`Image: ${img.alt}`}>
        <div className="img-lightbox-head">
          <span className="grow mono small" title={img.name}>
            {img.name}
            {images.length > 1 ? <span className="muted"> · {i + 1} of {images.length}</span> : null}
          </span>
          <a className="icon-btn sm" href={img.src} target="_blank" rel="noreferrer" aria-label="Open full size in a new tab" title="Open full size">
            <ExternalLink size={16} />
          </a>
          <button ref={closeBtn} type="button" className="icon-btn sm" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="img-lightbox-body">
          {images.length > 1 ? (
            <button type="button" className="img-nav prev" onClick={() => setI((x) => (x - 1 + images.length) % images.length)} aria-label="Previous image">
              <ChevronLeft size={20} />
            </button>
          ) : null}
          <img key={img.id} src={img.src} alt={img.alt} />
          {images.length > 1 ? (
            <button type="button" className="img-nav next" onClick={() => setI((x) => (x + 1) % images.length)} aria-label="Next image">
              <ChevronRight size={20} />
            </button>
          ) : null}
        </div>
      </div>
    </>,
    document.body,
  );
}
