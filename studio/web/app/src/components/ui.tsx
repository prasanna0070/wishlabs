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
  // The Wish Labs mark: "w" with the violet wish dot, on an ink rounded square (same as favicon.svg).
  const mark = (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 1000 1000" aria-hidden="true">
      <rect width="1000" height="1000" rx="280" fill="#16132B" />
      <path transform="translate(188 760) scale(0.78 -0.78)" d="M133 0 8 528H180L241 136H246L314 528H493L559 136H564L630 528H791L668 0H457L403 356H398L342 0Z" fill="#FBF7F0" />
      <circle cx="500" cy="215" r="78" fill="#7C5CFF" />
    </svg>
  );
  if (!withText) return <span className="logo">{mark}</span>;
  // The Wish Labs wordmark (Bricolage Grotesque 800): letters take the text colour, the dot on the "i" stays violet.
  const h = Math.round(size * 0.82);
  return (
    <span className="logo" style={{ display: "inline-flex", alignItems: "center" }} aria-label="Wish Labs" role="img">
      <span className="logo-mark-small" style={{ display: "none" }}>{mark}</span>
      <svg className="logo-word" height={h} width={Math.round((h * 4124) / 811)} viewBox="0 0 4124 811" aria-hidden="true">
        <path transform="translate(16.0 773.0) scale(1 -1)" d="M133 0 8 528H180L241 136H246L314 528H493L559 136H564L630 528H791L668 0H457L403 356H398L342 0Z" fill="currentColor" />
        <path transform="translate(801.0 773.0) scale(1 -1)" d="M36 0V528H198V0Z" fill="currentColor" />
        <path transform="translate(1021.0 773.0) scale(1 -1)" d="M267 -14Q214 -14 168.5 -3.5Q123 7 89.5 30.0Q56 53 38.5 91.5Q21 130 24 185L165 197Q167 164 180.0 142.5Q193 121 216.0 111.0Q239 101 272 101Q303 101 325.5 111.5Q348 122 348 147Q348 160 340.0 168.5Q332 177 310.5 184.5Q289 192 248 202Q197 215 156.0 228.0Q115 241 86.0 259.0Q57 277 42.0 303.5Q27 330 27 369Q27 424 56.5 462.5Q86 501 138.0 521.5Q190 542 258 542Q319 542 372.0 523.0Q425 504 455.5 461.0Q486 418 478 347L340 334Q343 363 332.0 383.5Q321 404 300.0 414.5Q279 425 249 425Q215 425 196.5 412.5Q178 400 178 381Q178 366 189.5 355.5Q201 345 225.5 337.5Q250 330 289 321Q323 314 360.0 303.0Q397 292 429.0 273.5Q461 255 481.0 225.0Q501 195 501 149Q501 100 474.0 63.0Q447 26 395.0 6.0Q343 -14 267 -14Z" fill="currentColor" />
        <path transform="translate(1524.0 773.0) scale(1 -1)" d="M36 0V279V700H198V568Q198 547 195.5 512.5Q193 478 188.5 439.0Q184 400 179.5 364.5Q175 329 172 305H181Q190 388 212.0 440.5Q234 493 272.0 517.5Q310 542 364 542Q449 542 490.0 481.5Q531 421 531 299V0H369V292Q369 348 354.0 374.0Q339 400 305 400Q275 400 251.0 377.5Q227 355 212.5 306.0Q198 257 198 179V0Z" fill="currentColor" />
        <path transform="translate(2288.0 773.0) scale(1 -1)" d="M36 0V700H198V0Z" fill="currentColor" />
        <path transform="translate(2508.0 773.0) scale(1 -1)" d="M175 -14Q135 -14 99.0 0.0Q63 14 40.0 45.5Q17 77 17 130Q17 179 36.0 209.5Q55 240 87.0 258.5Q119 277 157.0 286.5Q195 296 232 303Q284 313 310.0 319.0Q336 325 345.5 333.0Q355 341 355 355Q355 384 333.5 399.0Q312 414 277 414Q255 414 233.0 406.0Q211 398 197.0 377.5Q183 357 184 319L40 329Q38 392 59.0 433.5Q80 475 116.5 498.5Q153 522 196.5 532.0Q240 542 283 542Q362 542 413.0 512.5Q464 483 488.5 427.5Q513 372 513 293V199Q513 166 513.0 132.5Q513 99 513.0 66.0Q513 33 513 0H373Q373 36 373.0 73.0Q373 110 373 151H367Q363 106 337.5 68.5Q312 31 270.5 8.5Q229 -14 175 -14ZM242 104Q259 104 278.5 110.5Q298 117 315.5 132.0Q333 147 344.0 173.5Q355 200 355 239V263L382 266Q374 253 355.5 245.0Q337 237 314.0 232.5Q291 228 267.5 223.5Q244 219 224.0 211.5Q204 204 191.5 191.5Q179 179 179 157Q179 131 197.5 117.5Q216 104 242 104Z" fill="currentColor" />
        <path transform="translate(3037.0 773.0) scale(1 -1)" d="M353 -14Q308 -14 274.0 6.0Q240 26 217.5 65.5Q195 105 184 163H177V0H36V186V700H198V545Q198 518 195.5 487.0Q193 456 188.5 423.0Q184 390 178 356H187Q196 422 219.0 463.0Q242 504 278.0 523.0Q314 542 360 542Q419 542 462.5 512.0Q506 482 530.0 422.0Q554 362 554 271Q554 174 527.5 111.0Q501 48 455.5 17.0Q410 -14 353 -14ZM296 115Q326 115 347.0 134.5Q368 154 379.0 186.5Q390 219 390 258Q390 297 380.0 331.0Q370 365 349.0 386.0Q328 407 296 407Q282 407 267.5 402.0Q253 397 240.5 386.5Q228 376 218.0 359.5Q208 343 202.0 319.5Q196 296 196 266V259Q196 222 204.5 195.0Q213 168 227.0 150.0Q241 132 259.0 123.5Q277 115 296 115Z" fill="currentColor" />
        <path transform="translate(3599.0 773.0) scale(1 -1)" d="M267 -14Q214 -14 168.5 -3.5Q123 7 89.5 30.0Q56 53 38.5 91.5Q21 130 24 185L165 197Q167 164 180.0 142.5Q193 121 216.0 111.0Q239 101 272 101Q303 101 325.5 111.5Q348 122 348 147Q348 160 340.0 168.5Q332 177 310.5 184.5Q289 192 248 202Q197 215 156.0 228.0Q115 241 86.0 259.0Q57 277 42.0 303.5Q27 330 27 369Q27 424 56.5 462.5Q86 501 138.0 521.5Q190 542 258 542Q319 542 372.0 523.0Q425 504 455.5 461.0Q486 418 478 347L340 334Q343 363 332.0 383.5Q321 404 300.0 414.5Q279 425 249 425Q215 425 196.5 412.5Q178 400 178 381Q178 366 189.5 355.5Q201 345 225.5 337.5Q250 330 289 321Q323 314 360.0 303.0Q397 292 429.0 273.5Q461 255 481.0 225.0Q501 195 501 149Q501 100 474.0 63.0Q447 26 395.0 6.0Q343 -14 267 -14Z" fill="currentColor" />
        <circle cx="918.0" cy="111.5" r="87.5" fill="#7C5CFF" />
      </svg>
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
