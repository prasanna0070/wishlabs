// Version history helpers: where a version came from, whether the workspace is behind the latest, file comparison,
// and whether the design moved away from the brief. Pure functions.
import type { Brief, ChatMessage, FileEntry, Project, Version } from "./types";

export function latestN(versions: Version[] | null | undefined): number | null {
  const ns = (versions || []).map((v) => v.n);
  return ns.length ? Math.max(...ns) : null;
}

/** {cur, latest} when the current version is older than the newest one, else null. */
export function behindLatest(p: Pick<Project, "versions" | "current_version"> | null | undefined): { cur: number; latest: number } | null {
  if (!p || p.current_version == null) return null;
  const latest = latestN(p.versions);
  return latest != null && p.current_version < latest ? { cur: p.current_version, latest } : null;
}

/** "from v2" (or "restored from v1"), from base_version / restored_from. */
export function originText(v: Version): string | null {
  if (v.source === "restore" && (v.restored_from ?? v.base_version) != null) return `restored from v${v.restored_from ?? v.base_version}`;
  if (v.base_version != null) return `from v${v.base_version}`;
  return null;
}

export function sourceText(v: Version): string {
  return ({ build: "Built from the brief", update: "Chat edit", tweak: "Direct tweak", restore: "Restore", story_audio: "Story audio" } as Record<string, string>)[v.source || ""] || (v.source ? v.source.replace(/_/g, " ") : "Version");
}

/** The user message that started a version: the last user message before that version's progress card. */
export function triggerMessage(chat: ChatMessage[] | null | undefined, n: number): ChatMessage | null {
  const list = chat || [];
  const i = list.findIndex((m) => (m.kind === "progress" || m.kind === "result") && m.payload?.version === n);
  for (let k = (i < 0 ? -1 : i) - 1; k >= 0; k--) if (list[k].role === "user") return list[k];
  const tweak = list.find((m) => m.role === "user" && m.kind === "tweak" && m.payload?.version === n);
  return tweak || null;
}

export interface FileDiff {
  added: string[];
  removed: string[];
  changed: string[];
  same: number;
}

/** Compare two manifests by sha256 (or size when a hash is missing). `a` is the version shown, `b` the other one. */
export function compareFiles(a: FileEntry[], b: FileEntry[]): FileDiff {
  const mb = new Map(b.map((f) => [f.path, f]));
  const ma = new Map(a.map((f) => [f.path, f]));
  const out: FileDiff = { added: [], removed: [], changed: [], same: 0 };
  for (const f of a) {
    const o = mb.get(f.path);
    if (!o) out.added.push(f.path);
    else if ((f.sha256 && o.sha256 ? f.sha256 !== o.sha256 : f.bytes !== o.bytes)) out.changed.push(f.path);
    else out.same++;
  }
  for (const f of b) if (!ma.has(f.path)) out.removed.push(f.path);
  return out;
}

export function totalBytes(files: FileEntry[] | null | undefined): number {
  return (files || []).reduce((a, f) => a + (f.bytes || 0), 0);
}

/** The spec hash of the design a brief describes, on the same basis as version hashes: the version the brief was
 * written for (auto_for_version, set on edit briefs), else the first version built from that brief. A brief's own
 * spec_hash is not used: the server hashes the input spec there but rehashes a version from its built spec.json at
 * finalize, so the two never match. Null when no version was built from the brief (then no banner). */
export function briefSpecHash(brief: Brief | null | undefined, versions: Version[] | null | undefined): string | null {
  if (!brief) return null;
  const vs = [...(versions || [])].sort((x, y) => x.n - y.n);
  const forN = (brief as any).auto_for_version;
  const own = forN != null ? vs.find((v) => v.n === forN && v.spec_hash) : null;
  if (own) return own.spec_hash!;
  return vs.find((v) => v.brief_version === brief.version && v.spec_hash)?.spec_hash || null;
}

/** True when the version's design differs from the design the brief describes (both hashes known). */
export function designChangedSinceBrief(brief: Brief | null | undefined, version: Version | null | undefined, versions: Version[] | null | undefined): boolean {
  const h = briefSpecHash(brief, versions);
  return !!(h && version?.spec_hash && version.spec_hash !== h);
}

/** The brief that belongs to a version (by brief_version), falling back to the latest brief. */
export function briefFor(p: Pick<Project, "brief" | "brief_history">, v: Version | null | undefined): Brief | null {
  const all = [...(p.brief_history || [])];
  if (p.brief && !all.some((b) => b.version === p.brief!.version)) all.push(p.brief);
  if (v?.brief_version != null) {
    const b = all.find((x) => x.version === v.brief_version);
    if (b) return b;
  }
  return p.brief || all[all.length - 1] || null;
}
