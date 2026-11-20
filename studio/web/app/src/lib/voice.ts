// Voice wish (Gemini Live): the spoken session stored on a project, and the pure audio helpers the live call uses.
// Server side: web/server/app/voice_live.py. The browser streams 16 kHz 16-bit mono PCM up and plays 24 kHz back.
import type { Project, Version } from "./types";

export const MIC_RATE = 16000;
export const LIVE_RATE = 24000;

export interface VoiceTurn {
  role: "user" | "assistant";
  text: string;
  t: number; // seconds from the start of the call
}

export interface VoiceWish {
  kind?: string; // "gemini-live"
  model?: string;
  location?: string;
  voice?: string;
  started?: string;
  ended?: string;
  seconds?: number;
  wish?: string;
  details?: { question: string; answer: string }[];
  questions?: number;
  turns?: VoiceTurn[];
  audio_url?: string | null;
  audio_seconds?: number | null;
}

/** The spoken session on a project, or null when the wish was typed. */
export function voiceOf(p: Project | null | undefined): VoiceWish | null {
  const v = (p as { voice?: unknown } | null | undefined)?.voice as VoiceWish | null | undefined;
  if (!v || typeof v !== "object") return null;
  const turns = Array.isArray(v.turns) ? v.turns.filter((t) => t && typeof t.text === "string" && t.text.trim()) : [];
  if (!turns.length && !v.wish) return null;
  return { ...v, turns };
}

/** For the share page's mount point: render the card only when the project carries a spoken wish. */
export function hasVoiceWish(p: Project | null | undefined, _v?: Version | null): boolean {
  return voiceOf(p) !== null;
}

/** A stored URL for this view: on a share link every file goes through /api/share/<token>/. */
export function voiceUrl(url: string | null | undefined, share?: string | null): string | null {
  if (!url) return null;
  if (!share) return url;
  return url.replace(/^\/api\/projects\/[^/]+\//, `/api/share/${share}/`);
}

/** "gemini-live-2.5-flash-native-audio" -> "Gemini Live 2.5 Flash (native audio)". */
export function liveModelName(model?: string | null): string {
  const m = (model || "").trim();
  if (!m) return "Gemini Live";
  const live = /^gemini-live-([\d.]+)-(\w+)(-native-audio)?/.exec(m);
  if (live) return `Gemini Live ${live[1]} ${cap(live[2])}${live[3] ? " (native audio)" : ""}`;
  const v = /^gemini-([\d.]+)-live/.exec(m);
  if (v) return `Gemini ${v[1]} Live`;
  return m;
}

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

/** 83 -> "1:23". */
export function clock(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The WebSocket URL of the live voice route on this origin (the API is at the root, the SPA under /studio). */
export function liveSocketUrl(loc: { protocol: string; host: string } = location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/api/voice/live`;
}

/** Microphone samples (any rate) -> 16 kHz 16-bit PCM, keeping the leftover between chunks. Box-filter averaging,
 *  which is enough for speech recognition. */
export class Downsampler {
  private rest: Float32Array = new Float32Array(0);
  constructor(
    private from: number,
    private to: number = MIC_RATE,
  ) {}

  push(x: Float32Array): Int16Array {
    const src = new Float32Array(this.rest.length + x.length);
    src.set(this.rest);
    src.set(x, this.rest.length);
    const ratio = this.from / this.to;
    if (ratio <= 1) {
      this.rest = new Float32Array(0);
      return floatToPcm16(src);
    }
    const n = Math.floor(src.length / ratio);
    const out = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * ratio);
      const b = Math.min(src.length, Math.floor((i + 1) * ratio));
      let s = 0;
      for (let j = a; j < b; j++) s += src[j];
      out[i] = toInt16(b > a ? s / (b - a) : src[a]);
    }
    this.rest = src.slice(Math.floor(n * ratio));
    return out;
  }
}

function toInt16(v: number): number {
  const c = Math.max(-1, Math.min(1, v));
  return c < 0 ? Math.round(c * 0x8000) : Math.round(c * 0x7fff);
}

export function floatToPcm16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) out[i] = toInt16(f[i]);
  return out;
}

/** Little-endian 16-bit PCM bytes -> floats in [-1, 1). */
export function pcm16ToFloat(buf: ArrayBuffer): Float32Array {
  const n = Math.floor(buf.byteLength / 2);
  const view = new DataView(buf, 0, n * 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

/** Loudness of a chunk for the level meter, 0..1 (RMS with a gentle curve so speech fills the bar). */
export function level(f: Float32Array): number {
  if (!f.length) return 0;
  let s = 0;
  for (let i = 0; i < f.length; i++) s += f[i] * f[i];
  const rms = Math.sqrt(s / f.length);
  return Math.max(0, Math.min(1, Math.sqrt(rms * 4)));
}
