// Feature cards that other streams add to the Overview tab of a share link, found by file name (OverviewTab.tsx globs
// src/**/VoiceWishCard.tsx, src/**/ImageBriefCard.tsx and src/**/SoundCard.tsx) so the tab builds with or without them. A card module exports
// the component (default or by its name) and may export a predicate hasVoiceWish(project, version) /
// hasSound(project, version); without one the card returns null when it has no data. Pure: no imports of the cards.
import type { ComponentType } from "react";
import type { Project, Version } from "../../lib/types";
import type { Ctx } from "../../lib/urls";

/** Props every mounted card receives. On a share link readOnly is true and ctx.share is set. */
export interface CardProps {
  project: Project;
  version: Version | null;
  ctx: Ctx;
  readOnly: boolean;
  compact?: boolean;
}
export type HasCard = (project: Project, version: Version | null) => boolean;
export interface MountedCard {
  C: ComponentType<CardProps>;
  has: HasCard | null;
}

/** The first module that exports the card component, with its predicate when it has one. */
export function pickCard(mods: Record<string, Record<string, unknown>>, name: string, pred: string): MountedCard | null {
  for (const m of Object.values(mods)) {
    const C = (m.default || m[name]) as ComponentType<CardProps> | undefined;
    if (typeof C === "function" || (!!C && typeof C === "object")) return { C, has: typeof m[pred] === "function" ? (m[pred] as HasCard) : null };
  }
  return null;
}

/** True when the card should render: its predicate says so, or it has none (the card then decides by itself). */
export function cardWanted(m: MountedCard | null, project: Project, version: Version | null): boolean {
  if (!m) return false;
  if (!m.has) return true;
  try {
    return !!m.has(project, version);
  } catch {
    return false;
  }
}

