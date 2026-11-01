// Wait-time estimates from measured durations: finished builds in this project, concept turns from the project's
// usage log, plus the last few durations this browser saw in other projects. No fixed numbers: with no data the UI
// says "a few minutes" and nothing more.
import type { Version } from "./types";

export type TimingKind = "build" | "concepts";
const KEY = "gs.timings.v1";
const KEEP = 8;

/** Seconds of each finished build (restores copy files and are left out). */
export function buildSeconds(versions: Version[] | null | undefined): number[] {
  const out: number[] = [];
  for (const v of versions || []) {
    if (v.state !== "done" || !v.started || !v.ended || v.source === "restore") continue;
    const s = (Date.parse(v.ended) - Date.parse(v.started)) / 1000;
    if (Number.isFinite(s) && s > 5) out.push(s);
  }
  return out;
}

/** Seconds of each concept turn in a project's usage log (usage.turns[]): turns that made images. Text calls run one
 * after another and the photos run side by side, so a turn takes about the text calls plus the slowest image. */
export function conceptSeconds(turns: any[] | null | undefined): number[] {
  const out: number[] = [];
  for (const t of turns || []) {
    const calls: any[] = Array.isArray(t?.calls) ? t.calls : [];
    const images = calls.filter((c) => c?.kind === "image");
    if (!images.length && !(Number(t?.images) > 0)) continue;
    const text = calls.filter((c) => c?.kind !== "image").reduce((a, c) => a + (Number(c?.seconds) || 0), 0);
    const img = images.reduce((a, c) => Math.max(a, Number(c?.seconds) || 0), 0);
    const s = text + img;
    if (s > 5) out.push(s);
  }
  return out;
}

export interface MinuteRange {
  lo: number;
  hi: number;
  n: number; // how many measurements
}

/** A rounded minute range from measured seconds (most recent last). */
export function minuteRange(secs: number[]): MinuteRange | null {
  const xs = secs.filter((s) => Number.isFinite(s) && s > 0).slice(-KEEP);
  if (!xs.length) return null;
  const min = Math.min(...xs);
  const max = Math.max(...xs);
  const lo = Math.max(1, Math.round(min / 60));
  const hi = Math.max(lo + 1, Math.ceil((max * 1.3) / 60));
  return { lo, hi, n: xs.length };
}

export function waitText(r: MinuteRange | null): string {
  return r ? `about ${r.lo} to ${r.hi} minutes` : "a few minutes";
}

// ---------------------------------------------------------------- this browser's recent durations (a convenience)

export function recentSeconds(kind: TimingKind): number[] {
  try {
    const d = JSON.parse(localStorage.getItem(KEY) || "{}");
    const xs = d?.[kind];
    return Array.isArray(xs) ? xs.filter((x: any) => typeof x === "number").slice(-KEEP) : [];
  } catch {
    return [];
  }
}

/** Remember measured durations keyed by an id (version or turn), so a reload does not count one twice. */
export function rememberSeconds(kind: TimingKind, items: { id: string; s: number }[]): void {
  if (!items.length) return;
  try {
    const d = JSON.parse(localStorage.getItem(KEY) || "{}") || {};
    const seen: string[] = Array.isArray(d[`${kind}_ids`]) ? d[`${kind}_ids`] : [];
    const xs: number[] = Array.isArray(d[kind]) ? d[kind] : [];
    for (const it of items) {
      if (seen.includes(it.id)) continue;
      seen.push(it.id);
      xs.push(Math.round(it.s));
    }
    d[kind] = xs.slice(-KEEP);
    d[`${kind}_ids`] = seen.slice(-40);
    localStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* storage unavailable: estimates fall back to this project's data */
  }
}

/** Build wait: this project's finished builds, else this browser's recent builds. */
export function buildEstimate(versions: Version[] | null | undefined, pid?: string): MinuteRange | null {
  const own = buildSeconds(versions);
  if (pid) rememberSeconds("build", (versions || []).filter((v) => v.state === "done" && v.started && v.ended && v.source !== "restore").map((v) => ({ id: `${pid}/v${v.n}`, s: (Date.parse(v.ended!) - Date.parse(v.started!)) / 1000 })).filter((x) => x.s > 5));
  return minuteRange(own.length ? own : recentSeconds("build"));
}

/** Concept wait: this project's concept turns, else this browser's recent ones. */
export function conceptEstimate(turns: any[] | null | undefined, pid?: string): MinuteRange | null {
  const own = conceptSeconds(turns);
  if (pid) {
    const items = (turns || []).map((t, i) => ({ id: `${pid}/${t?.turn_id || i}`, s: conceptSeconds([t])[0] })).filter((x) => x.s != null);
    rememberSeconds("concepts", items as { id: string; s: number }[]);
  }
  return minuteRange(own.length ? own : recentSeconds("concepts"));
}
