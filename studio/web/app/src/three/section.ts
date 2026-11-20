// Section view: one cut plane across the model (Preview tab). Pure maths, no React: the viewer (Viewer.tsx) puts the
// plane into every model material's clippingPlanes and draws back faces in a flat colour so the cut reads as solid.
//
// Axes are the gadget's: X = width, Y = depth (front to back), Z = height. The scene is +Y up (glTF) with
// gadget (x, y, z) -> scene (x, z, -y) (Viewer.tsx), so X cuts along scene x, Y along scene z and Z along scene y.
// The position t runs 0..1 across the given box along the scene axis. Unflipped, the cut keeps the side away from the
// default camera (which looks from the right, the front and above), so the cut face points at the viewer and moving
// the slider up shows more of the model; Flip keeps the other side.
import * as THREE from "three";

export type SectionAxis = "x" | "y" | "z";

export interface Section {
  axis: SectionAxis;
  t: number; // 0..1 across the box along the axis
  flip: boolean;
}

export const SECTION_DEFAULT: Section = { axis: "y", t: 0.5, flip: false };

export const SECTION_AXES: readonly { axis: SectionAxis; label: string; title: string }[] = [
  { axis: "x", label: "X", title: "Cut across the width (X)" },
  { axis: "y", label: "Y", title: "Cut across the depth (Y)" },
  { axis: "z", label: "Z", title: "Cut across the height (Z)" },
];

/** Scene component (0 x, 1 y, 2 z) each gadget axis runs along. */
export const SCENE_COMPONENT: Record<SectionAxis, 0 | 1 | 2> = { x: 0, y: 2, z: 1 };

/**
 * The clipping plane for a cut on `axis` at fraction `t` of `bbox` (scene coordinates, mm). three.js keeps the points
 * where normal . p + constant >= 0 and clips the rest. The range is padded by a hair on both ends so t = 0 clips the
 * whole box and t = 1 keeps all of it (faces lying exactly on the box do not flicker). An empty box cuts at 0.
 */
export function sectionPlane(axis: SectionAxis, t: number, flip: boolean, bbox: THREE.Box3, out: THREE.Plane = new THREE.Plane()): THREE.Plane {
  const i = SCENE_COMPONENT[axis] ?? 2;
  const empty = bbox.isEmpty();
  const lo = empty ? 0 : bbox.min.getComponent(i);
  const hi = empty ? 0 : bbox.max.getComponent(i);
  const pad = (hi - lo) * 0.001 + 0.01;
  const f = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0.5;
  const at = lo - pad + (hi - lo + 2 * pad) * f;
  const normal = new THREE.Vector3();
  normal.setComponent(i, flip ? 1 : -1);
  return out.set(normal, flip ? -at : at);
}

/** "Cut at 40% of the depth" style words for the slider's screen-reader value. */
export function sectionWords(s: Section): string {
  const word = s.axis === "x" ? "width" : s.axis === "y" ? "depth" : "height";
  const pct = Math.round((Number.isFinite(s.t) ? Math.min(1, Math.max(0, s.t)) : 0.5) * 100);
  return `Cut at ${pct}% of the ${word}${s.flip ? ", flipped" : ""}`;
}
