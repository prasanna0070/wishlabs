// Build-step text helpers: split the "**Use:** 1 × A, 2 × B and 3 × C." paragraph out of a step so the step card can
// show it grouped by role with counts (the full list stays one click away).
import type { Build } from "./types";

export interface UseItem {
  qty: number;
  name: string;
  role: UseRole;
}

export type UseRole = "electronics" | "cables" | "printed" | "hardware";

export const ROLE_LABEL: Record<UseRole, string> = {
  electronics: "Electronics",
  cables: "Cables",
  printed: "Printed parts",
  hardware: "Hardware and consumables",
};

const USE_RE = /(^|\n)\*\*Use:\*\*\s*([\s\S]*?)(?=\n\s*\n|\n\*\*[A-Z][^*]*:\*\*|$)/;

/** {items, rest}: the Use list parsed into items, and the instruction text without it. Null when there is no list. */
export function splitUse(md: string, build?: Build | null): { items: UseItem[]; rest: string } | null {
  const m = USE_RE.exec(md || "");
  if (!m) return null;
  const body = m[2].trim().replace(/\.$/, "");
  const parts = body.split(/,\s+(?=\d+\s*[×x]\s)|\s+and\s+(?=\d+\s*[×x]\s)/);
  const items: UseItem[] = [];
  for (const p of parts) {
    const im = /^(\d+)\s*[×x]\s+(.+)$/s.exec(p.trim());
    if (!im) return null; // not a list we understand: leave the text as it is
    const name = im[2].replace(/\*\*|`/g, "").trim();
    items.push({ qty: Number(im[1]), name, role: roleOf(name, build) });
  }
  if (!items.length) return null;
  const rest = (md.slice(0, m.index) + (m[1] || "") + md.slice(m.index + m[0].length)).replace(/^\s*\n/, "");
  return { items, rest };
}

export function roleOf(name: string, build?: Build | null): UseRole {
  if (/^PRINT_/.test(name)) return "printed";
  const rows = build?.purchased || [];
  const low = name.toLowerCase();
  const row =
    rows.find((r) => r.name.toLowerCase() === low) ||
    rows.find((r) => r.name.toLowerCase().includes(low) || low.includes(r.name.toLowerCase().split(" (")[0]));
  if (row?.kind === "cable") return "cables";
  if (row?.kind === "hardware") return "hardware";
  if (row?.kind === "module") return "electronics";
  if (/screw|insert|nut\b|nuts\b|feet|foot|cable tie|washer|standoff|label|gasket|foam|adhesive/i.test(name)) return "hardware";
  if (/harness|cable|lead\b/i.test(name)) return "cables";
  if ((build?.printed || []).some((p) => p.name === name)) return "printed";
  return "electronics";
}

/** Role groups with item and piece counts, in a fixed order. */
export function groupUse(items: UseItem[]): { role: UseRole; label: string; lines: number; pieces: number; items: UseItem[] }[] {
  const order: UseRole[] = ["electronics", "cables", "printed", "hardware"];
  return order
    .map((role) => {
      const its = items.filter((i) => i.role === role);
      return { role, label: ROLE_LABEL[role], lines: its.length, pieces: its.reduce((a, i) => a + i.qty, 0), items: its };
    })
    .filter((g) => g.lines);
}
