// Friendly error text. The engine reports a failed build as raw text such as
// "1 stage(s) failed: normalize: SpecError: features[3].at: speaker: its kit ... Fix: move speaker toward th",
// cut at 160 characters. The UI shows a plain sentence and keeps the raw text behind "Technical details". Pure.
import type { Status } from "./types";

export interface FriendlyError {
  /** One plain sentence for the card. */
  headline: string;
  /** Paragraphs that followed the error line (advice), unchanged. */
  rest: string;
  /** The engine's suggested change, only when the full (untruncated) engine text carries one. */
  fix: string | null;
  /** The raw technical text, for an expandable "Technical details". Null when the text was already plain. */
  details: string | null;
}

const ERR_CLASS = /\b[A-Z][A-Za-z]*(?:Error|Exception)\b:/;
const TECHNICAL = /\b[A-Z][A-Za-z]*(?:Error|Exception)\b:|\b\d+ stage\(s\) failed\b|Traceback \(most recent call last\)|\{'[A-Za-z_]+':|\bfeatures\[\d+\]/;
// Where the technical part starts: "N stage(s) failed:", "stage: SomeError:", or "SomeError:".
const TECH_START = /\b\d+ stage\(s\) failed:|\b[a-z_]+:\s*[A-Z][A-Za-z]*(?:Error|Exception):|\b[A-Z][A-Za-z]*(?:Error|Exception):|Traceback \(most recent call last\)/;

export function isTechnical(text: string | null | undefined): boolean {
  return TECHNICAL.test(text || "");
}

function sentence(s: string): string {
  const t = s.trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/** The engine's own "Fix: ..." advice from a full error text, if it is there and was not cut short. */
export function engineFix(full: string | null | undefined): string | null {
  const m = /\bFix:\s*([^\n]+)/.exec((full || "").trim());
  if (!m) return null;
  const fix = m[1].replace(/\s+/g, " ").trim();
  if (fix.length < 8 || fix.length > 400) return null;
  return sentence(fix.replace(/[.\s]+$/, "")) + ".";
}

/**
 * Turn an error message into a plain headline plus optional details.
 * `full` is the untruncated engine text (the failed stage's message), when the caller has it.
 */
export function friendlyError(text: string | null | undefined, full?: string | null): FriendlyError {
  const t = (text || "").trim();
  const fullText = (full || "").trim() || null;
  if (!t) {
    return { headline: "Something went wrong.", rest: "", fix: engineFix(fullText), details: fullText };
  }
  const cut = t.indexOf("\n\n");
  const head = cut >= 0 ? t.slice(0, cut).trim() : t;
  const rest = cut >= 0 ? t.slice(cut + 2).trim() : "";
  if (!isTechnical(head)) {
    return { headline: head, rest, fix: null, details: fullText && isTechnical(fullText) ? fullText : null };
  }
  const start = TECH_START.exec(head);
  const lead = (start ? head.slice(0, start.index) : "").trim().replace(/[:;,\s]+$/, "");
  const spec = /\bSpecError\b/.test(head) || /\bSpecError\b/.test(fullText || "");
  const why = spec
    ? "the design did not pass one of its own engineering checks, so this version was not built"
    : ERR_CLASS.test(head)
      ? "one step of the pipeline reported an error, so this version was not finished"
      : "the details are technical";
  const headline = lead ? `${sentence(lead)}: ${why}.` : `${sentence(why)}.`;
  return { headline, rest, fix: engineFix(fullText), details: fullText || head };
}

/** The failed stage's own message in a build status (it is not cut short like version.error). */
export function stageErrorText(status: Status | null | undefined): string | null {
  const st = (status?.stages || []).find((s) => s.state === "error" || s.state === "failed");
  if (!st) return null;
  const texts = [(st as any).error, st.message].filter((x): x is string => typeof x === "string" && !!x.trim());
  if (!texts.length) return null;
  return texts.sort((a, b) => b.length - a.length)[0].trim();
}
