// Screen-space callout placement for the 3D viewer: greedy, collision-free. Each label is placed next to its anchor
// so that it overlaps no other label, no obstacle (the step card, the controls), no anchor dot, stays inside the
// viewer, and its leader line crosses no other label. A label that cannot be placed is hidden, never stacked.

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface LabelIn {
  id: string;
  ax: number; // anchor, px in viewer space
  ay: number;
  w: number; // label size, px
  h: number;
}

export interface Placed {
  id: string;
  rect: Rect;
  ax: number;
  ay: number;
  lx: number; // leader end on the label edge
  ly: number;
}

export interface Placement {
  placed: Placed[];
  hidden: string[];
}

export interface PlaceOpts {
  dot?: number; // anchor dot radius
  pad?: number; // clearance between labels
  margin?: number; // clearance to the viewer edge and obstacles
}

export function overlaps(a: Rect, b: Rect, pad = 0): boolean {
  return a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;
}

export function inside(p: { x: number; y: number }, r: Rect, pad = 0): boolean {
  return p.x >= r.x - pad && p.x <= r.x + r.w + pad && p.y >= r.y - pad && p.y <= r.y + r.h + pad;
}

function nearestOnRect(px: number, py: number, r: Rect): [number, number] {
  return [Math.min(Math.max(px, r.x), r.x + r.w), Math.min(Math.max(py, r.y), r.y + r.h)];
}

function segHitsRect(x1: number, y1: number, x2: number, y2: number, r: Rect): boolean {
  // Liang-Barsky clip of the segment against the rect
  let t0 = 0;
  let t1 = 1;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const edges: [number, number][] = [
    [-dx, x1 - r.x],
    [dx, r.x + r.w - x1],
    [-dy, y1 - r.y],
    [dy, r.y + r.h - y1],
  ];
  for (const [p, q] of edges) {
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const t = q / p;
      if (p < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
  }
  return t0 < t1;
}

/** Candidate label rects around an anchor, nearest first. */
function candidates(l: LabelIn, cx: number): Rect[] {
  const out: Rect[] = [];
  const preferRight = l.ax >= cx; // labels fan outwards from the model's centre
  const sides = preferRight ? [1, -1] : [-1, 1];
  const gaps = [28, 56, 96, 150];
  const rises = [-36, -8, -64, 20, -96, 48, -130, 80, -170, 120];
  for (const g of gaps)
    for (const side of sides)
      for (const r of rises) {
        const x = side > 0 ? l.ax + g : l.ax - g - l.w;
        out.push({ x, y: l.ay + r - l.h / 2, w: l.w, h: l.h });
      }
  // straight above or below
  for (const g of [34, 70, 110]) {
    out.push({ x: l.ax - l.w / 2, y: l.ay - g - l.h, w: l.w, h: l.h });
    out.push({ x: l.ax - l.w / 2, y: l.ay + g, w: l.w, h: l.h });
  }
  return out;
}

export function placeLabels(labels: LabelIn[], bounds: Rect, obstacles: Rect[], opts: PlaceOpts = {}): Placement {
  const dot = opts.dot ?? 5;
  const pad = opts.pad ?? 6;
  const margin = opts.margin ?? 6;
  const hidden: string[] = [];
  const live = labels.filter((l) => {
    const ok = inside({ x: l.ax, y: l.ay }, { x: bounds.x + 2, y: bounds.y + 2, w: bounds.w - 4, h: bounds.h - 4 }) && !obstacles.some((o) => inside({ x: l.ax, y: l.ay }, o, dot + 2));
    if (!ok) hidden.push(l.id);
    return ok;
  });
  const dots: Rect[] = live.map((l) => ({ x: l.ax - dot - 2, y: l.ay - dot - 2, w: 2 * dot + 4, h: 2 * dot + 4 }));
  const cx = live.length ? live.reduce((a, l) => a + l.ax, 0) / live.length : bounds.x + bounds.w / 2;
  const inner: Rect = { x: bounds.x + margin, y: bounds.y + margin, w: bounds.w - 2 * margin, h: bounds.h - 2 * margin };
  const placed: Placed[] = [];
  // top-most anchors first, so their labels take the space above the model
  const order = [...live].sort((a, b) => a.ay - b.ay || a.ax - b.ax);
  for (const l of order) {
    let best: { r: Rect; cost: number; lx: number; ly: number } | null = null;
    for (const r of candidates(l, cx)) {
      if (r.x < inner.x || r.y < inner.y || r.x + r.w > inner.x + inner.w || r.y + r.h > inner.y + inner.h) continue;
      if (obstacles.some((o) => overlaps(r, o, margin))) continue;
      if (placed.some((p) => overlaps(r, p.rect, pad))) continue;
      if (dots.some((d) => overlaps(r, d, 1))) continue;
      const [lx, ly] = nearestOnRect(l.ax, l.ay, r);
      if (placed.some((p) => segHitsRect(l.ax, l.ay, lx, ly, p.rect))) continue;
      if (obstacles.some((o) => segHitsRect(l.ax, l.ay, lx, ly, o))) continue;
      const dist = Math.hypot(lx - l.ax, ly - l.ay);
      const cost = dist + (Math.sign(r.x + r.w / 2 - l.ax) === Math.sign(l.ax - cx) ? 0 : 18);
      if (!best || cost < best.cost) best = { r, cost, lx, ly };
    }
    if (best) placed.push({ id: l.id, rect: best.r, ax: l.ax, ay: l.ay, lx: best.lx, ly: best.ly });
    else hidden.push(l.id);
  }
  return { placed, hidden };
}

/** Problems in a placement (used by tests and the screenshot check): label-label, label-obstacle, label-dot overlaps. */
export function placementProblems(p: Placement, obstacles: Rect[], dot = 5): string[] {
  const out: string[] = [];
  for (let i = 0; i < p.placed.length; i++) {
    const a = p.placed[i];
    for (let j = i + 1; j < p.placed.length; j++) if (overlaps(a.rect, p.placed[j].rect)) out.push(`${a.id} overlaps ${p.placed[j].id}`);
    for (const o of obstacles) if (overlaps(a.rect, o)) out.push(`${a.id} overlaps an obstacle`);
    for (const b of p.placed) if (inside({ x: b.ax, y: b.ay }, a.rect, dot)) out.push(`${a.id} covers the anchor of ${b.id}`);
  }
  return out;
}
