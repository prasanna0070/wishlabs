// The agent's working state: what the chat shows while a turn runs, whether this tab streams the turn itself (POST
// /chat) or mirrors a turn found on the server after a reload or a dropped stream (GET /api/projects/{pid}/turn,
// API.md §3.3). Pure functions, unit-tested in working.test.ts.
import type { ChatMessage } from "./types";

export interface TurnStep {
  id: string;
  label: string;
  state: "running" | "done" | "error";
  detail?: string; // e.g. a repair round's reason
  ts?: number;
}

export interface TurnState {
  turnId?: string;
  running: boolean;
  startedAt: number;
  endedAt?: number;
  userMsgId?: string;
  status: string;
  steps: TurnStep[];
  action?: string; // what the turn was asked to do (text, answers, regenerate_concepts, ...)
  thoughts?: string; // the model's thought summaries (thinking.delta; the server's newest ~3 KB when remote)
  remote?: boolean; // mirrors a turn this tab is not streaming (after a reload or a dropped stream)
  stale?: boolean; // the server's turn lock is older than 15 min: the turn stopped unexpectedly
  concepts?: Record<string, string>; // concept id -> render state, for this turn's concept counter
  conceptTotal?: number;
}

/** GET /api/projects/{pid}/turn */
export interface TurnView {
  running: boolean;
  stale: boolean;
  state: string; // idle | running | stale | done | error | ended
  turn_id: string | null;
  started: string | null;
  age_s: number | null;
  step: string | null;
  steps: { label: string; ts?: string | null; detail?: string | null }[];
  thoughts: string;
  user_message_id: string | null;
  action: string | null;
  mode: string | null;
  updated: string | null;
  quiet_s: number | null;
  error?: string | null;
}

export const MAX_THOUGHTS = 30000;
export const POLL_MS = 2000;
export const TURN_STALE_S = 15 * 60; // the server's turn lock blocks a new message this long (agent/turn.py)

/** Add one streamed thought summary; keeps the newest `limit` characters, cut where a paragraph (ideally a bold
 * heading) starts. Mirrors the server's append_thought. */
export function appendThought(buf: string, text: string, limit = MAX_THOUGHTS): string {
  if (!text || !text.trim()) return buf;
  let out = buf;
  if (out && !out.endsWith("\n") && text.trimStart().startsWith("**")) out += "\n\n";
  out += text;
  if (out.length > limit) {
    const tail = out.slice(-limit);
    let cut = tail.indexOf("\n\n**");
    if (cut < 0 || cut >= limit / 2) cut = tail.indexOf("\n\n");
    out = cut >= 0 && cut < limit / 2 ? tail.slice(cut + 2) : tail;
  }
  return out;
}

/** Local thoughts (streamed here before the stream dropped) plus the server's newest window, without repeating the
 * overlap. The server's text wins when they do not overlap. */
export function mergeThoughts(local = "", server = ""): string {
  if (!server) return local;
  if (!local) return server;
  if (local.endsWith(server) || local.includes(server)) return local;
  const probe = server.slice(0, 60);
  const i = local.lastIndexOf(probe);
  if (i >= 0 && server.startsWith(local.slice(i))) return local.slice(0, i) + server;
  return server;
}

/** The one live line under the working card: the newest thought's bold heading, else its last line. */
export function latestThought(text: string | undefined | null, max = 140): string {
  if (!text || !text.trim()) return "";
  const heads = [...text.matchAll(/\*\*([^*\n]{2,200}?)\*\*/g)];
  let line = heads.length ? heads[heads.length - 1][1] : "";
  if (!line) {
    const lines = text
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    line = lines[lines.length - 1] || "";
  }
  line = line.replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

/** Concepts render in parallel: one counter, not six lines (the server words its step the same way). */
export function conceptStepLabel(states: Record<string, string> = {}, total = 0): string {
  const vals = Object.values(states);
  const n = Math.max(total, vals.length);
  const done = vals.filter((s) => s === "ready" || s === "failed").length;
  const failed = vals.filter((s) => s === "failed").length;
  return `Rendering concept photos: ${done} of ${n} done${failed ? `, ${failed} failed` : ""}`;
}

/** "Rendering 6 concepts" -> 6 */
export function renderingTotal(status: string | undefined | null): number | null {
  const m = /^Rendering (\d+) concepts?$/.exec((status || "").trim());
  return m ? Number(m[1]) : null;
}

/** What the working card says it is doing right now. */
export function currentStepLabel(t: Pick<TurnState, "steps" | "status">): string {
  const cur = [...(t.steps || [])].reverse().find((s) => s.state === "running");
  return ((cur?.label || t.status || "Working") as string).replace(/…$/, "").trim() || "Working";
}

export type WatchDecision = "running" | "stale" | "ended";

export function watchDecision(v: Pick<TurnView, "running" | "stale">): WatchDecision {
  return v.running ? "running" : v.stale ? "stale" : "ended";
}

/** The server's view of a turn as this tab's TurnState (steps, thoughts, start time without clock skew). */
export function remoteTurn(v: TurnView, prev: TurnState | null | undefined, chat: ChatMessage[], now = Date.now()): TurnState {
  const same = !!prev && !!prev.turnId && prev.turnId === v.turn_id;
  const parsed = v.started ? Date.parse(v.started) : NaN;
  const fromAge = v.age_s != null ? now - v.age_s * 1000 : NaN; // the server measured the age: no clock skew
  const startedAt = same && prev ? prev.startedAt : !Number.isNaN(fromAge) ? fromAge : !Number.isNaN(parsed) ? Math.min(parsed, now) : now;
  const lastUser = [...chat].reverse().find((m) => m.role === "user");
  const all = v.steps || [];
  const steps: TurnStep[] = all.map((s, i) => ({
    id: `r:${i}`,
    label: s.label,
    detail: s.detail || undefined,
    ts: s.ts ? Date.parse(s.ts) : undefined,
    state: i === all.length - 1 && v.running ? "running" : "done",
  }));
  return {
    turnId: v.turn_id || prev?.turnId,
    running: !!v.running,
    stale: !!v.stale,
    remote: true,
    startedAt,
    userMsgId: v.user_message_id || (same ? prev?.userMsgId : undefined) || lastUser?.id,
    status: v.step || prev?.status || "Working",
    steps: steps.length ? steps : prev?.steps || [],
    action: v.action || prev?.action,
    thoughts: same ? mergeThoughts(prev?.thoughts || "", v.thoughts || "") : v.thoughts || "",
  };
}

/** The request body that sends a turn's user message again (Retry after a turn stopped unexpectedly). */
export function retryBody(m: ChatMessage | null | undefined): Record<string, any> | null {
  if (!m || m.role !== "user" || m.kind !== "text") return null;
  const p = m.payload || {};
  switch (p.action) {
    case undefined:
    case null:
      return m.text?.trim() ? { text: m.text } : null;
    case "answers":
      return { answers: Array.isArray(p.answers) ? p.answers : [], ...(p.skipped || !p.answers?.length ? { skipped: true } : {}) };
    case "select_concept":
      return p.concept_id ? { action: "select_concept", concept_id: p.concept_id } : { action: "skip_concepts" };
    case "edit_part": {
      if (!p.component) return null;
      const text = m.text || "";
      const i = text.indexOf(": ");
      return { action: "edit_part", component: p.component, instruction: i >= 0 ? text.slice(i + 2) : text };
    }
    case "story_audio":
      return null; // not a chat turn
    default:
      return { action: p.action };
  }
}

/** "started 3 min ago" for the composer while a turn runs. */
export function agoText(secs: number): string {
  const m = Math.floor(Math.max(0, secs) / 60);
  if (m < 1) return "less than a minute ago";
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ${m % 60} min ago`;
}

/** The friendly words for a 409 from /chat: the previous message is still being worked on. */
export const CONFLICT_TEXT = "The assistant is still working on your previous message, so this one was not sent.";
