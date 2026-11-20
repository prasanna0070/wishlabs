// What the design agent read from the user's starting image (record_image_brief, _notes/image-contract.md §5): the
// SSE event's shape, the card's labels and counts. Pure, unit-tested in imageBrief.test.ts.
import type { FeatureStatus, ImageBrief, ImageBriefFeature, Project, Version } from "./types";
import { hiddenImages, IMAGE_ONLY_TEXT, messageAttachments } from "./uploads";

/** The "image_brief" SSE event carries the stored object, either as {seq, image_brief: {...}} or flat {seq, ...}. */
export function briefFromEvent(d: any): ImageBrief | null {
  if (!d || typeof d !== "object") return null;
  const raw = d.image_brief && typeof d.image_brief === "object" ? d.image_brief : d;
  const { seq: _seq, ...b } = raw as Record<string, unknown>;
  return looksLikeBrief(b) ? (b as ImageBrief) : null;
}

function looksLikeBrief(b: any): boolean {
  return !!b && typeof b === "object" && (typeof b.title === "string" || typeof b.kind === "string" || Array.isArray(b.features) || typeof b.purpose === "string");
}

/** The share Overview's predicate (cards.ts contract): a brief with something to show. */
export function hasImageBrief(project: Pick<Project, "image_brief"> | null | undefined, _version?: Version | null): boolean {
  const b = project?.image_brief;
  return !!b && looksLikeBrief(b) && !!(b.title || b.purpose || b.features?.length || b.closest_buildable?.title || b.out_of_scope?.length);
}

const KINDS: Record<string, string> = {
  concept_sheet: "Concept sheet",
  sketch: "Sketch",
  product_photo: "Product photo",
  reference: "Reference image",
  other: "Image",
};

export function kindLabel(kind: string | null | undefined): string {
  const k = (kind || "").toLowerCase();
  return KINDS[k] || (k ? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Image");
}

export interface StatusInfo {
  key: FeatureStatus | "other";
  label: string;
  hint: string; // for screen readers and the tooltip
}

const STATUS: Record<FeatureStatus, StatusInfo> = {
  buildable: { key: "buildable", label: "Buildable", hint: "We can build this with parts in our catalog." },
  closest: { key: "closest", label: "Closest", hint: "Not exactly, but our catalog has a close match." },
  not_yet: { key: "not_yet", label: "Not yet", hint: "Our catalog has no part for this today." },
};

export function statusInfo(s: string | null | undefined): StatusInfo {
  const k = (s || "").toLowerCase().replace(/[\s-]+/g, "_");
  if (k in STATUS) return STATUS[k as FeatureStatus];
  if (k === "notyet" || k === "not_buildable" || k === "no") return STATUS.not_yet;
  return { key: "other", label: s ? s.replace(/_/g, " ") : "Unknown", hint: "" };
}

/** Features in a stable order: buildable, closest, not yet, anything else (each group keeps the agent's order). */
export function sortedFeatures(list: ImageBriefFeature[] | null | undefined): ImageBriefFeature[] {
  const rank = (f: ImageBriefFeature) => ["buildable", "closest", "not_yet", "other"].indexOf(statusInfo(f.status).key);
  return (list || [])
    .filter((f) => f && typeof f.text === "string" && f.text.trim())
    .map((f, i) => ({ f, i }))
    .sort((a, b) => rank(a.f) - rank(b.f) || a.i - b.i)
    .map((x) => x.f);
}

export function featureCounts(list: ImageBriefFeature[] | null | undefined): { buildable: number; closest: number; not_yet: number } {
  const c = { buildable: 0, closest: 0, not_yet: 0 };
  for (const f of list || []) {
    const k = statusInfo(f?.status).key;
    if (k !== "other") c[k] += 1;
  }
  return c;
}

/** "3 buildable · 2 closest · 4 not yet" (empty groups left out). */
export function countLine(list: ImageBriefFeature[] | null | undefined): string {
  const c = featureCounts(list);
  return [c.buildable ? `${c.buildable} buildable` : "", c.closest ? `${c.closest} closest` : "", c.not_yet ? `${c.not_yet} not yet` : ""].filter(Boolean).join(" · ");
}

/** The chat message the card sits under: the user message holding the images it read, else the first message with
 * images; null when no message has images (the card then sits at the top of the chat). */
export function imageBriefAnchor(p: Pick<Project, "chat" | "image_brief"> | null | undefined): string | null {
  const ids = (p?.image_brief?.upload_ids || []).filter(Boolean);
  let first: string | null = null;
  for (const m of p?.chat || []) {
    if (m.role !== "user") continue;
    const atts = messageAttachments(m);
    if (!atts.length && !hiddenImages(m)) continue; // a share link without share_uploads only gives the count
    if (ids.length && atts.some((a) => ids.includes(a.id))) return m.id;
    if (!first) first = m.id;
  }
  return first;
}

/** Did the project start from an image? "image-only" when the first message was images without words (stored with
 * the server's stand-in sentence), "image" when it had words too, null otherwise. */
export function imageStart(p: Pick<Project, "chat"> | null | undefined): "image" | "image-only" | null {
  const first = (p?.chat || []).find((m) => m.role === "user");
  if (!first || (!messageAttachments(first).length && !hiddenImages(first))) return null;
  const words = (first.text || "").trim();
  return !words || words === IMAGE_ONLY_TEXT ? "image-only" : "image";
}
