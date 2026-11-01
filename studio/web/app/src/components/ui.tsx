import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { X } from "lucide-react";
import { useOnClickOutside } from "../lib/hooks";

export function Markdown({ text, className = "", resolveImg }: { text: string; className?: string; resolveImg?: (src: string) => string }) {
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _n, ...p }) => <a {...p} target="_blank" rel="noreferrer" />,
          img: ({ node: _n, src, ...p }) => <img {...p} src={src && resolveImg ? resolveImg(src) : src} loading="lazy" />,
        }}
      >
        {text || ""}
      </ReactMarkdown>
    </div>
  );
}

export function Dialog({
  open,
  onClose,
  title,
  sub,
  children,
  footer,
  variant = "center",
  wide = false,
  label,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  sub?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  variant?: "center" | "sheet";
  wide?: boolean;
  label?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <div className={`dialog ${variant}${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={label || (typeof title === "string" ? title : undefined)}>
        <div className="dialog-head">
          <div className="grow">
            <h2>{title}</h2>
            {sub ? <div className="muted small" style={{ marginTop: 4 }}>{sub}</div> : null}
          </div>
          <button className="icon-btn sm" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer ? <div className="dialog-foot">{footer}</div> : null}
      </div>
    </>,
    document.body,
  );
}

/** A button that opens an anchored popover. */
export function Dropdown({
  trigger,
  children,
  align = "right",
  width,
  open: controlled,
  onOpenChange,
}: {
  trigger: (p: { open: boolean; toggle: () => void }) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "left" | "right";
  width?: number;
  open?: boolean;
  onOpenChange?: (v: boolean) => void;
}) {
  const [own, setOwn] = useState(false);
  const open = controlled ?? own;
  const set = (v: boolean) => (onOpenChange ? onOpenChange(v) : setOwn(v));
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => set(false), open);
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex" }}>
      {trigger({ open, toggle: () => set(!open) })}
      {open ? (
        <div className="popover" style={{ top: "calc(100% + 6px)", [align]: 0, width }}>
          {typeof children === "function" ? children(() => set(false)) : children}
        </div>
      ) : null}
    </div>
  );
}

export function Logo({ size = 28, withText = true }: { size?: number; withText?: boolean }) {
  return (
    <span className="logo" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <rect x="1" y="1" width="30" height="30" rx="9" fill="#3046D9" />
        <path d="M10 13.5c0-2.5 2.7-4.5 6-4.5s6 2 6 4.5V20a3 3 0 0 1-3 3h-6a3 3 0 0 1-3-3z" fill="#fff" />
        <circle cx="13.6" cy="15.6" r="1.35" fill="#3046D9" />
        <circle cx="18.4" cy="15.6" r="1.35" fill="#3046D9" />
        <path d="M14 19.2c1.2.9 2.8.9 4 0" stroke="#3046D9" strokeWidth="1.3" strokeLinecap="round" fill="none" />
        <circle cx="16" cy="6.4" r="1.6" fill="#fff" />
        <path d="M16 8v1.2" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
      {withText ? <span style={{ fontWeight: 650, fontSize: 16, letterSpacing: "-0.01em" }}>Gadget Studio</span> : null}
    </span>
  );
}

export function Toast({ text, onDone, ms = 2600 }: { text: string | null; onDone: () => void; ms?: number }) {
  useEffect(() => {
    if (!text) return;
    const t = window.setTimeout(onDone, ms);
    return () => window.clearTimeout(t);
  }, [text, ms, onDone]);
  return text ? createPortal(<div className="toast" role="status">{text}</div>, document.body) : null;
}

export function Spinner({ lg = false }: { lg?: boolean }) {
  return <span className={`spinner${lg ? " lg" : ""}`} aria-hidden="true" />;
}

export function ProgressBar({ value }: { value: number | null | undefined }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${v}%` }} />
    </div>
  );
}

export function Ring({ value, total, size = 40 }: { value: number; total: number; size?: number }) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const f = total ? value / total : 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--paper-2)" strokeWidth="4" fill="none" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        stroke="var(--success)"
        strokeWidth="4"
        fill="none"
        strokeLinecap="round"
        strokeDasharray={`${c * f} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 0.3s" }}
      />
    </svg>
  );
}

/** Pan and zoom container for an image or SVG (wheel, drag, buttons). */
export function ZoomPan({ src, alt, height = 420, background = "var(--panel)" }: { src: string; alt: string; height?: number | string; background?: string }) {
  const [z, setZ] = useState(1);
  const [p, setP] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setZ(1);
    setP({ x: 0, y: 0 });
  }, [src]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const w = (e: WheelEvent) => {
      e.preventDefault();
      setZ((v) => Math.min(12, Math.max(0.5, v * (e.deltaY < 0 ? 1.12 : 1 / 1.12))));
    };
    el.addEventListener("wheel", w, { passive: false });
    return () => el.removeEventListener("wheel", w);
  }, []);
  return (
    <div
      ref={box}
      className="zoompan"
      style={{ height, background }}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        drag.current = { x: e.clientX, y: e.clientY, px: p.x, py: p.y };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d) setP({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y });
      }}
      onPointerUp={() => (drag.current = null)}
      onDoubleClick={() => {
        setZ(1);
        setP({ x: 0, y: 0 });
      }}
    >
      <div className="zoompan-fit">
        <img src={src} alt={alt} draggable={false} style={{ transform: `translate(${p.x}px, ${p.y}px) scale(${z})` }} />
      </div>
      <div className="zoompan-ctl">
        <button className="icon-btn sm" aria-label="Zoom in" onClick={() => setZ((v) => Math.min(12, v * 1.25))}>
          +
        </button>
        <button className="icon-btn sm" aria-label="Zoom out" onClick={() => setZ((v) => Math.max(0.5, v / 1.25))}>
          −
        </button>
        <button
          className="icon-btn sm"
          aria-label="Reset zoom"
          onClick={() => {
            setZ(1);
            setP({ x: 0, y: 0 });
          }}
        >
          ↺
        </button>
        <span className="small muted" style={{ padding: "0 6px", minWidth: 40 }}>
          {Math.round(z * 100)}%
        </span>
      </div>
    </div>
  );
}

export function Lightbox({ src, alt, caption, onClose }: { src: string | null; alt?: string; caption?: string; onClose: () => void }) {
  return (
    <Dialog open={!!src} onClose={onClose} title={alt || "Image"} wide>
      {src ? (
        <>
          <ZoomPan src={src} alt={alt || ""} height="70vh" />
          {caption ? <p className="muted" style={{ marginBottom: 0 }}>{caption}</p> : null}
        </>
      ) : null}
    </Dialog>
  );
}
