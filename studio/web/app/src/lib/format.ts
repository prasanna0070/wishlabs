// Formatting helpers (INR, sizes, times, names). Pure functions.

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `₹${inrFmt.format(Math.round(n))}`;
}

export function inrRange(lo?: number | null, hi?: number | null): string {
  if (lo == null && hi == null) return "—";
  if (lo == null || hi == null || Math.round(lo) === Math.round(hi)) return inr(lo ?? hi);
  return `₹${inrFmt.format(Math.round(lo))}–${inrFmt.format(Math.round(hi))}`;
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

export function mm(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n.toFixed(digits));
  return `${v} mm`;
}

export function dims(w?: number, d?: number, h?: number): string {
  const f = (x?: number) => (x == null ? "?" : String(Math.round(x * 10) / 10));
  return `${f(w)} × ${f(d)} × ${f(h)} mm`;
}

export function timeAgo(iso?: string | null, now = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  return new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  return t.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function duration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  return m ? `${m}m ${s % 60}s` : `${s}s`;
}

/** PRINT_RearCover -> "Rear cover"; PURCHASE_CoreBoard -> "Core board". */
export function humanPart(name: string): string {
  const base = name.replace(/^(PRINT|PURCHASE|REFERENCE|TEST)_/, "");
  const words = base
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .trim()
    .split(/\s+/);
  return words.map((w, i) => (i === 0 ? w : /^[A-Z0-9]{2,}$/.test(w) ? w : w.toLowerCase())).join(" ");
}

export function partKind(name: string, kind?: string): "PRINT" | "PURCHASE" | "REFERENCE" | "OTHER" {
  const k = (kind || name.split("_", 1)[0] || "").toUpperCase();
  return k === "PRINT" || k === "PURCHASE" || k === "REFERENCE" ? k : "OTHER";
}

export function kindLine(kind: string): string {
  return kind === "PRINT" ? "Printed part." : kind === "PURCHASE" ? "Bought part." : kind === "REFERENCE" ? "Shown for reference." : "Part.";
}

export function stageLabel(stage: string): string {
  return (
    { clarify: "Clarifying", concepts: "Concepts", brief: "Brief review", building: "Building", ready: "Ready", error: "Needs attention" } as Record<
      string,
      string
    >
  )[stage] || stage;
}

export function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

/** RFC 7386 merge patch that turns `base` into `edited`. Arrays are replaced whole. */
export function diffMergePatch(base: any, edited: any): any {
  if (!isObj(base) || !isObj(edited)) return edited;
  const out: Record<string, any> = {};
  for (const k of Object.keys(base)) if (!(k in edited)) out[k] = null;
  for (const [k, v] of Object.entries(edited)) {
    if (!(k in base)) out[k] = v;
    else if (isObj(base[k]) && isObj(v)) {
      const sub = diffMergePatch(base[k], v);
      if (Object.keys(sub).length) out[k] = sub;
    } else if (JSON.stringify(base[k]) !== JSON.stringify(v)) out[k] = v;
  }
  return out;
}

function isObj(x: any): x is Record<string, any> {
  return x !== null && typeof x === "object" && !Array.isArray(x);
}

/** A plain line right after a list item would join that item (Markdown lazy continuation); end the list first. */
export function endLists(md: string): string {
  return (md || "").replace(/^(\s*(?:[-*+]|\d+[.)])\s.*)\n(?=[^\s\-*+\d])/gm, "$1\n\n");
}

/** Step text is written line by line ("**Use:** ...\n**Tools:** ..."): keep single newlines as line breaks. */
export function softBreaks(md: string): string {
  return (md || "").replace(/([^\n])\n(?!\n|\s*(?:[-*+]|\d+[.)])\s|\s*```)/g, "$1  \n");
}

// ---------------------------------------------------------------- status words (no raw enum reaches the UI)

const STATUS_WORDS: Record<string, string> = {
  ESTIMATE_REVIEW_REQUIRED: "estimate, review needed",
  ESTIMATE_INCOMPLETE: "incomplete estimate",
  UNSUPPORTED_AT_LAUNCH: "not offered at launch",
  NOT_QUOTED: "not quoted",
  REVIEW_REQUIRED: "review needed",
  ESTIMATE: "estimate",
  BINDING: "binding price",
};
const STATUS_RE = new RegExp(`\\b(${Object.keys(STATUS_WORDS).join("|")})\\b`, "g");

/** ESTIMATE_REVIEW_REQUIRED -> "estimate, review needed". Unknown SNAKE_CASE values become plain lower-case words. */
export function statusWords(s?: string | null): string {
  if (!s) return "";
  if (STATUS_WORDS[s]) return STATUS_WORDS[s];
  return /^[A-Z][A-Z0-9_]+$/.test(s) ? s.toLowerCase().replace(/_/g, " ") : s;
}

/** Replace raw status enums and internal field names inside a free text line (stage messages, narration). */
export function plainText(t?: string | null): string {
  return (t || "").replace(STATUS_RE, (m) => STATUS_WORDS[m]).replace(/\bmissing_prices\b/g, "parts without a price");
}

/** The quote's "Not included" lines, without internal field names; names the unpriced parts when there are any. */
export function excludesText(excludes: string[] | null | undefined, missing: string[] | null | undefined): string[] {
  const out = (excludes || []).filter((x) => !/missing_prices/.test(x)).map(plainText);
  if (missing?.length) out.push(`Parts without a price yet: ${missing.join(", ")}`);
  return out;
}

// ---------------------------------------------------------------- checks tally (chat chip, build card)

export interface CheckTally {
  passed: number;
  total: number;
  failed: number;
  failedTitles: string[];
  warned: number;
}

/** BuildSummary.checks (summary.py) in either shape: digital_failed as titles or a count; optional digital_warned. */
export function checkTally(c: any): CheckTally | null {
  if (!c || c.digital_total == null) return null;
  const listOrCount = (x: any): [number, string[]] => (Array.isArray(x) ? [x.length, x.map(String)] : [Number(x) || 0, []]);
  const [failed, failedTitles] = listOrCount(c.digital_failed);
  const [warned] = listOrCount(c.digital_warned ?? c.digital_warnings);
  return { passed: Number(c.digital_passed) || 0, total: Number(c.digital_total) || 0, failed, failedTitles, warned };
}

/** A finished stage whose own result has failures (the checks stage: "85 passed, 2 failed, ..."). */
export function stageHasFailures(s: { message?: string | null; failed?: any; flag?: string | null } | null | undefined): boolean {
  if (!s) return false;
  if (s.flag === "failed" || s.flag === "warn" || s.flag === "warning") return true;
  if (typeof s.failed === "number") return s.failed > 0;
  if (Array.isArray(s.failed)) return s.failed.length > 0;
  const m = /\b(\d+)\s+failed\b/.exec(s.message || "");
  return !!m && Number(m[1]) > 0;
}

// ---------------------------------------------------------------- one parts total (MAJ-23 / MIN-27)

export interface PartsTotal {
  inr: number;
  basis: string;
  source: "quote" | "build" | "quote-breakdown";
}

function numOf(x: any): number | null {
  if (x == null) return null;
  if (typeof x === "number") return Number.isFinite(x) ? x : null;
  if (typeof x === "object") return numOf(x.one_off ?? x.inr ?? x.value ?? x.incl_gst ?? x.total_inr ?? x.price_inr_incl_gst);
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

/** The single parts figure every view shows. Prefers the pipeline's parts total (MAJ-23: quote.json
 * `parts_total_inr {one_off, batch_unit, steady_state_unit, basis}`), then build.json order.parts_total_inr (= one_off),
 * then the parts line of the one-off quote scenario for older versions. Null when none exists. */
export function partsTotal(quote: any, build: any): PartsTotal | null {
  const q = quote || {};
  const pt = q.parts_total_inr ?? q.parts_total;
  const v = numOf(pt);
  if (v != null)
    return { inr: v, basis: (pt && typeof pt === "object" && (pt.basis || pt.label)) || "one-off parts total from the quote", source: "quote" };
  const b = numOf(build?.order?.parts_total_inr ?? build?.order?.parts_total);
  if (b != null) return { inr: b, basis: build?.order?.parts_total_basis || "parts total from the quote", source: "build" };
  const sc = (q.scenarios || [])[0];
  const p = numOf(sc?.breakdown_mean?.parts);
  if (p != null) return { inr: p, basis: `parts line of the quote (${sc?.name || "one-off"}; average before margin and GST)`, source: "quote-breakdown" };
  return null;
}
