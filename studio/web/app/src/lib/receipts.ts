// AI receipts: which Google model did what in one project, from the usage metadata Vertex AI returns with every model
// call. The server keeps one record per call in project.usage.turns[].calls[] (web/server/app/usage.py): model id,
// kind, token counts, images, seconds and an estimated cost. Pure functions.

export interface UsageCall {
  model?: string;
  kind?: string;
  input_tokens?: number;
  output_tokens?: number;
  thinking_tokens?: number;
  cached_tokens?: number;
  images?: number;
  seconds?: number | null;
  cost_usd_est?: number;
}

export interface Usage {
  basis?: string;
  turns?: { calls?: UsageCall[] | null; action?: string; mode?: string }[] | null;
  /** Calls recorded outside an agent turn (voice, sound, speech), when a feature keeps them here. */
  calls?: UsageCall[] | null;
  totals?: Record<string, number> | null;
}

export type RoleKey = "agent" | "voice" | "photos" | "sound" | "speech";

export interface ReceiptRow {
  key: string;
  role: RoleKey;
  label: string;
  what: string;
  model: string;
  modelName: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  thinkingTokens: number;
  images: number;
  seconds: number;
  costUsd: number;
  /** Token counts do not apply (Lyria is priced per clip): show "–" instead of 0. */
  noTokens: boolean;
}

export interface Receipts {
  rows: ReceiptRow[];
  calls: number;
  /** All tokens: in + out + thinking. */
  tokens: number;
  inputTokens: number;
  /** Output tokens including the thinking tokens. */
  outputTokens: number;
  images: number;
  costUsd: number;
  /** Design-agent turns only: the voice call and each sound composition are recorded as turns too, not counted here. */
  turns: number;
}

const ROLES: Record<RoleKey, { label: string; what: string; order: number }> = {
  agent: {
    label: "Design agent",
    what: "Talked the wish through, proposed the concepts as engineering specs and wrote the brief, calling tools to check and repair its own specs.",
    order: 0,
  },
  voice: { label: "Voice wish", what: "Listened to the spoken wish and talked it through.", order: 1 },
  photos: { label: "Concept photos", what: "Turned each concept's CAD render into a product photo.", order: 2 },
  sound: { label: "Gadget sound", what: "Composed this gadget's own sound.", order: 3 },
  speech: { label: "Spoken clips", what: "Spoke the gadget's lines (text-to-speech).", order: 4 },
};

/** Which job a call did, from its kind (as recorded) and its model id. */
export function roleOf(model: string, kind?: string | null): RoleKey {
  const m = (model || "").toLowerCase();
  const k = (kind || "").toLowerCase();
  if (k === "live" || k === "voice" || m.includes("live") || m.includes("native-audio")) return "voice";
  if (k === "music" || k === "lyria" || k === "sound" || m.includes("lyria")) return "sound";
  if (k === "tts" || k === "speech" || /tts/.test(m)) return "speech";
  if (k === "image" || /image|imagen/.test(m)) return "photos";
  return "agent";
}

const NAMES: [RegExp, string][] = [
  [/^gemini-3-pro-image/, "Gemini 3 Pro Image (Nano Banana Pro)"],
  [/^gemini-2\.5-flash-image/, "Gemini 2.5 Flash Image (Nano Banana)"],
  [/tts/, "Gemini-TTS"],
  [/live|native-audio/, "Gemini Live"],
  [/^lyria/, "Lyria"],
  [/^gemini-3\.1-pro/, "Gemini 3.1 Pro"],
  [/^gemini-3\.5-flash-lite/, "Gemini 3.5 Flash-Lite"],
  [/^gemini-3\.5-flash/, "Gemini 3.5 Flash"],
  [/^gemini-3-pro/, "Gemini 3 Pro"],
  [/^gemini-3-flash/, "Gemini 3 Flash"],
];

/** "gemini-3.1-pro-preview" -> "Gemini 3.1 Pro (preview)"; unknown ids come back as they are. */
export function modelName(model: string): string {
  const id = (model || "").toLowerCase();
  const hit = NAMES.find(([re]) => re.test(id));
  if (!hit) return model || "Unknown model";
  return /preview/.test(id) && !/Live|TTS/.test(hit[1]) ? `${hit[1]} (preview)` : hit[1];
}

const n = (x: any): number => (typeof x === "number" && Number.isFinite(x) ? x : Number(x) || 0);

/** All recorded calls of a project, grouped by job and model, agent first. Null when nothing was recorded. */
export function aiReceipts(usage: Usage | null | undefined): Receipts | null {
  if (!usage) return null;
  const calls: UsageCall[] = [];
  for (const t of usage.turns || []) for (const c of t?.calls || []) if (c && c.model) calls.push(c);
  for (const c of usage.calls || []) if (c && c.model) calls.push(c);
  if (!calls.length) return null;
  const rows = new Map<string, ReceiptRow>();
  for (const c of calls) {
    const role = roleOf(c.model!, c.kind);
    const key = `${role}:${c.model}`;
    let r = rows.get(key);
    if (!r) {
      r = { key, role, label: ROLES[role].label, what: ROLES[role].what, model: c.model!, modelName: modelName(c.model!), calls: 0, inputTokens: 0, outputTokens: 0, thinkingTokens: 0, images: 0, seconds: 0, costUsd: 0, noTokens: role === "sound" };
      rows.set(key, r);
    }
    r.calls += 1;
    r.inputTokens += n(c.input_tokens);
    r.outputTokens += n(c.output_tokens);
    r.thinkingTokens += n(c.thinking_tokens);
    r.images += n(c.images);
    r.seconds += n(c.seconds);
    r.costUsd += n(c.cost_usd_est);
  }
  const list = [...rows.values()].sort((a, b) => ROLES[a.role].order - ROLES[b.role].order || b.costUsd - a.costUsd);
  return {
    rows: list,
    calls: list.reduce((a, r) => a + r.calls, 0),
    tokens: list.reduce((a, r) => a + r.inputTokens + r.outputTokens + r.thinkingTokens, 0),
    inputTokens: list.reduce((a, r) => a + r.inputTokens, 0),
    outputTokens: list.reduce((a, r) => a + r.outputTokens + r.thinkingTokens, 0),
    images: list.reduce((a, r) => a + r.images, 0),
    costUsd: list.reduce((a, r) => a + r.costUsd, 0),
    turns: agentTurns(usage),
  };
}

/** Turns recorded by the voice wish or the gadget sound (usage.py records each as a turn, like an agent turn). */
const NON_AGENT_ACTIONS = new Set(["voice_wish", "sound"]);

/** How many design-agent turns the project had: usage.totals.turns counts every recorded turn, voice and sound
 * included, so it is corrected by the voice and sound turns still in the kept list. */
export function agentTurns(usage: Usage | null | undefined): number {
  if (!usage) return 0;
  const kept = usage.turns || [];
  const other = kept.filter((t) => t && NON_AGENT_ACTIONS.has(String(t.action || ""))).length;
  const total = n(usage.totals?.turns) || kept.length;
  return Math.max(0, total - other);
}

/** 1234567 -> "1.2M", 38474 -> "38k", 512 -> "512". */
export function compact(x: number): string {
  if (x >= 1e6) return `${(x / 1e6).toFixed(x >= 1e7 ? 0 : 1)}M`;
  if (x >= 1e4) return `${Math.round(x / 1e3)}k`;
  if (x >= 1e3) return `${(x / 1e3).toFixed(1)}k`;
  return String(Math.round(x));
}

/** USD estimate: "$1.29", "<$0.01". */
export function usd(x: number): string {
  if (!x) return "$0";
  if (x < 0.01) return "<$0.01";
  return `$${x.toFixed(2)}`;
}
