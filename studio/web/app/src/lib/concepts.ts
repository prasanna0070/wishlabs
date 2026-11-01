// Concept tiers, shared by the concepts dock and the concept history card in the chat.
import type { Concept } from "./types";

export const TIERS: { id: string; label: string }[] = [
  { id: "expected", label: "Expected" },
  { id: "different", label: "Different ideas" },
  { id: "wildcard", label: "Wildcards" },
];

export interface ConceptGroup {
  id: string;
  label: string;
  items: Concept[];
}

/** Group concepts by tier, using the message's tier map (payload.tiers) or each concept's own tier. Untiered sets come
 * back as one unlabelled group. */
export function groupConcepts(list: Concept[], tierMap?: Record<string, string[]> | null): ConceptGroup[] {
  const tiered = !!tierMap || list.some((c) => c.tier);
  if (!tiered) return [{ id: "all", label: "", items: list }];
  const inTier = (c: Concept, t: string) => (tierMap ? (tierMap[t] || []).includes(c.id) : (c.tier || "expected") === t);
  const groups = TIERS.map((t) => ({ ...t, items: list.filter((c) => inTier(c, t.id)) })).filter((g) => g.items.length);
  const leftover = list.filter((c) => !TIERS.some((t) => inTier(c, t.id)));
  if (leftover.length) groups.push({ id: "other", label: "More", items: leftover });
  return groups;
}
