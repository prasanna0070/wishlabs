// What the read-only share page shows: the original wish, the chosen concept, the build's own summary and its files.
// Pure functions over the shared Project (GET /api/share/{token}).
import type { ChatMessage, Concept, FileEntry, Project, Version } from "./types";
import { briefFor } from "./versions";

/** The wish as the person first typed it: the first plain user message (not an answer or a button press). */
export function originalWish(p: Pick<Project, "chat"> | null | undefined): string | null {
  for (const m of p?.chat || []) {
    if (m.role !== "user" || m.kind !== "text") continue;
    if (m.payload && (m.payload as any).action) continue;
    const t = (m.text || "").trim();
    if (!t || /^My choices:/i.test(t)) continue;
    return t;
  }
  return null;
}

/** The concept the shown version was built from: its brief's concept, else the selected one, else the last pick. */
export function chosenConcept(p: Project | null | undefined, v: Version | null | undefined): Concept | null {
  if (!p) return null;
  const byId = (id: string | null | undefined) => (id ? p.concepts.find((c) => c.id === id) || null : null);
  const fromBrief = byId(briefFor(p, v)?.concept_id);
  if (fromBrief) return fromBrief;
  const sel = byId(p.selected_concept);
  if (sel) return sel;
  const picks = (p.chat || []).filter((m) => m.role === "user" && m.payload?.action === "select_concept");
  return byId(picks.length ? picks[picks.length - 1].payload?.concept_id : null);
}

/** The best picture of a concept: the product photo, else the CAD render. */
export function conceptImage(c: Concept | null | undefined): string | null {
  return c?.photo_url || c?.render_url || null;
}

/** The finished version a visitor should see: the current one when it finished, else the newest finished one. */
export function showcaseVersion(p: Project | null | undefined): Version | null {
  if (!p) return null;
  const cur = p.versions.find((v) => v.n === p.current_version);
  if (cur?.state === "done") return cur;
  const done = p.versions.filter((v) => v.state === "done");
  return done.length ? done[done.length - 1] : null;
}

/** The result card the server posted when version n finished (its payload.summary holds the checks and price). */
export function resultMessage(p: Pick<Project, "chat"> | null | undefined, n: number | null | undefined): ChatMessage | null {
  if (n == null) return null;
  const list = (p?.chat || []).filter((m) => m.kind === "result" && m.payload?.version === n);
  return list.length ? list[list.length - 1] : null;
}

/** "50/50" -> {passed: 50, total: 50}. */
export function testCount(x: unknown): { passed: number; total: number } | null {
  if (typeof x === "string") {
    const m = /^(\d+)\s*\/\s*(\d+)$/.exec(x.trim());
    return m ? { passed: Number(m[1]), total: Number(m[2]) } : null;
  }
  return null;
}

export interface FileGroup {
  folder: string;
  label: string;
  count: number;
  bytes: number;
}

const FOLDER_LABELS: Record<string, string> = {
  cad: "3D CAD (STEP, STL, GLB)",
  pcb: "Circuit board (KiCad, Gerbers)",
  firmware: "Firmware source and binaries",
  manual: "Build manual pictures",
  print: "Print files (sliced)",
  references: "Part datasheets and references",
  checks: "Check reports",
  order: "Price quote",
  "": "Docs and summaries",
};

/** A version's files grouped by top folder, biggest first. */
export function fileGroups(files: FileEntry[] | null | undefined): FileGroup[] {
  const map = new Map<string, FileGroup>();
  for (const f of files || []) {
    const folder = f.path.includes("/") ? f.path.slice(0, f.path.indexOf("/")) : "";
    let g = map.get(folder);
    if (!g) {
      g = { folder, label: FOLDER_LABELS[folder] || folder, count: 0, bytes: 0 };
      map.set(folder, g);
    }
    g.count += 1;
    g.bytes += f.bytes || 0;
  }
  return [...map.values()].sort((a, b) => b.count - a.count || b.bytes - a.bytes);
}

/** Minutes and seconds between two ISO times: "9 min 43 s". Null when either is missing. */
export function buildTime(started?: string | null, ended?: string | null): string | null {
  if (!started || !ended) return null;
  const s = Math.round((Date.parse(ended) - Date.parse(started)) / 1000);
  if (!Number.isFinite(s) || s <= 0) return null;
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

/** The fixed price line for every surface (decided 20 Nov 2026). */
export const PRICE_LINE = "₹19k to 28k for first units; under ₹10k at volume (modelled).";
