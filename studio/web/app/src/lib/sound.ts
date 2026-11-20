// The gadget's own sound, composed by Lyria on Vertex AI (server: web/server/app/sound.py, routes/sound.py).
// project.sound = { status, job, error, current, history }; current holds the prompt, the model and the theme, chime
// and device-chime files. Pure helpers plus the two owner calls; share links only read project.sound.
import { request } from "./api";
import type { Project, Version } from "./types";
import { rewriteShareUrl, type Ctx } from "./urls";

export interface SoundFile {
  file: string;
  mime: string;
  seconds: number;
  bytes: number;
  url: string;
  peaks?: number[];
  onset_s?: number;
  sample_rate?: number;
  channels?: number;
  bits?: number;
}

export interface SoundRecord {
  id: string;
  version: number;
  created: string;
  provider?: string;
  model: string;
  location?: string;
  prompt: string;
  negative_prompt?: string;
  prompt_source?: string;
  bpm?: number;
  theme: SoundFile;
  chime: SoundFile;
  device_chime?: SoundFile | null;
  source?: { file?: string; seconds?: number; sample_rate?: number; channels?: number } | null;
  /** Lyria 3's own description of what it composed (its "Caption:" text part). */
  caption?: string | null;
  /** Set when the main model failed and the fallback model composed this one. */
  fallback_from?: string | null;
  model_seconds?: number;
  elapsed_s?: number;
  cost_usd_est?: number;
  trigger?: string | null;
}

export interface SoundState {
  status?: "generating" | "ready" | "error" | string;
  job?: { id: string; version: number; trigger?: string; started?: string } | null;
  error?: string | null;
  current?: SoundRecord | null;
  history?: SoundRecord[];
  count?: number;
}

/** GET/POST /api/projects/{pid}/sound */
export interface SoundApiState {
  enabled: boolean;
  status: "none" | "generating" | "ready" | "error" | string;
  sound: SoundState | null;
  model?: string;
  cost_usd_est?: number;
  session_hourly?: number;
  session_left?: number | null;
}

/** A job that has said "generating" for this long died with its server instance (the server agrees: STALE_S). */
export const STALE_MS = 300_000;

export function soundOf(p: Project | null | undefined): SoundState | null {
  const s = (p as (Project & { sound?: SoundState | null }) | null | undefined)?.sound;
  return s && typeof s === "object" ? s : null;
}

/** A playable composition: a theme and a chime with URLs. */
export function playable(s: SoundState | null | undefined): SoundRecord | null {
  const c = s?.current;
  return c && c.theme?.url && c.chime?.url ? c : null;
}

/** For the share page's mount point: true when the project has a sound to play. */
export function hasSound(p: Project | null | undefined, _v?: Version | null): boolean {
  return !!playable(soundOf(p));
}

export function soundStatus(s: SoundState | null | undefined, now: number = Date.now()): "none" | "generating" | "ready" | "error" {
  if (!s) return "none";
  if (s.status === "generating") {
    const t = Date.parse(s.job?.started || "");
    return Number.isFinite(t) && now - t < STALE_MS ? "generating" : "error";
  }
  if (s.status === "error") return "error";
  return s.current ? "ready" : "none";
}

/** Same-origin audio URL for this view (share links go through /api/share/<token>/...). */
export function audioUrl(url: string, ctx: Ctx | null | undefined): string {
  return rewriteShareUrl(url, ctx?.share) || url;
}

export function clock(s: number | null | undefined): string {
  const t = Math.max(0, Math.round(Number(s) || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/** 48000 -> "48 kHz", 44100 -> "44.1 kHz" */
export function khz(rate: number | null | undefined): string {
  const k = (Number(rate) || 0) / 1000;
  return `${Number.isInteger(k) ? k : k.toFixed(1)} kHz`;
}

export function kb(bytes: number | null | undefined): string {
  const b = Number(bytes) || 0;
  return b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1000))} KB`;
}

export const soundApi = {
  state: (pid: string) => request<SoundApiState>("GET", `/api/projects/${pid}/sound`),
  compose: (pid: string, version?: number | null) =>
    request<SoundApiState>("POST", `/api/projects/${pid}/sound`, version != null ? { version } : {}),
};
