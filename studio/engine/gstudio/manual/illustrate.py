"""Compose FigureSpecs into finished manual PNGs: title band, A/B panels, auto-placed leader labels and
an honesty footer. All 3D content comes from `render3d`; all label positions from `labels.place`.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import labels as L
from .figs import FigureSpec, LabelSpec, PanelSpec
from .geom import bbox_offset, bbox_union
from .render3d import BG, ItemSpec, MeshStore, Renderer, ray_hit, screen_quad, visible_anchor
from .scene import Scene

BG_RGB = tuple(int(round(v * 255)) for v in BG)

INK = (27, 31, 36)
MUTED = (88, 94, 102)
PAPER = (246, 245, 241)
PANEL = (255, 255, 255)
LINE = (228, 225, 216)
COBALT = (48, 70, 217)

FONT_DIRS = [Path("/usr/share/fonts/opentype/inter"), Path("/usr/share/fonts/truetype/inter"),
             Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
FONT_FILES = {
    "regular": ["Inter-Regular.otf", "Inter-Regular.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    "medium": ["Inter-Medium.otf", "Inter-Medium.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    "semibold": ["Inter-SemiBold.otf", "Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    "mono": ["JetBrainsMono-Regular.ttf", "DejaVuSansMono.ttf", "Courier New.ttf"],
}
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(weight: str, size: int):
    key = (weight, size)
    if key not in _FONT_CACHE:
        f = None
        for name in FONT_FILES.get(weight, FONT_FILES["regular"]):
            for d in FONT_DIRS:
                p = d / name
                if p.is_file():
                    try:
                        f = ImageFont.truetype(str(p), size)
                        break
                    except OSError:
                        continue
            if f:
                break
        _FONT_CACHE[key] = f or ImageFont.load_default()
    return _FONT_CACHE[key]


def wrap_px(s: str, width: float, f) -> list[str]:
    """Word-wrap to a pixel width (no truncation)."""
    lines, cur = [], ""
    for w in str(s).split():
        t = f"{cur} {w}".strip()
        if cur and text_w(t, f) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def text_w(s: str, f) -> float:
    try:
        return float(f.getlength(s))
    except AttributeError:  # pragma: no cover
        return float(f.getsize(s)[0])


# --------------------------------------------------------------------------------------------- results
@dataclass
class FigureResult:
    file: str
    ok: bool
    seconds: float
    labels: int = 0
    violations: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cameras: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    placed: list[str] = field(default_factory=list)  # label texts actually drawn (alt text is built from these)
    dropped_priority: list[str] = field(default_factory=list)  # dropped labels of priority >= 2 (fail the summary)


LABEL_FONT = 19
LABEL_PAD = (8, 5)
LINE_H = 24


def measure_label(text: str) -> tuple[float, float, list[str]]:
    f = font("regular", LABEL_FONT)
    lines = [ln for ln in text.split("\n") if ln.strip()] or [text]
    w = max(text_w(ln, f) for ln in lines) + 2 * LABEL_PAD[0]
    h = len(lines) * LINE_H + 2 * LABEL_PAD[1]
    return w, h, lines


def draw_labels(img: Image.Image, layout: L.Layout, texts: list[list[str]], offset=(0, 0)) -> None:
    d = ImageDraw.Draw(img)
    f = font("regular", LABEL_FONT)
    ox, oy = offset
    for p in layout.placed:
        a, b = p.attach, p.anchor
        d.line([(a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy)], fill=INK, width=2)
    for p in layout.placed:
        x, y = p.anchor[0] + ox, p.anchor[1] + oy
        d.ellipse([x - 5.5, y - 5.5, x + 5.5, y + 5.5], fill=(255, 255, 255))
        d.ellipse([x - 3.6, y - 3.6, x + 3.6, y + 3.6], fill=COBALT)
    for p in layout.placed:
        bx = p.box
        d.rounded_rectangle([bx.x0 + ox, bx.y0 + oy, bx.x1 + ox, bx.y1 + oy], radius=4, fill=(255, 255, 255), outline=(208, 204, 194), width=1)
        for k, ln in enumerate(texts[p.idx]):
            d.text((bx.x0 + ox + LABEL_PAD[0], bx.y0 + oy + LABEL_PAD[1] + k * LINE_H + 2), ln, font=f, fill=INK)


# --------------------------------------------------------------------------------------------- figure composer
class Illustrator:
    def __init__(self, scene: Scene, meshes: MeshStore, renderer: Renderer):
        self.s, self.meshes, self.r = scene, meshes, renderer

    def _items(self, panel: PanelSpec) -> list[ItemSpec]:
        items = []
        for name, mode in panel.show.items():
            c = self.s.comp(name)
            if c is None:
                continue
            if "#" in name:
                pd = self.meshes.solids(name.split("#", 1)[0], c.raw.get("solid_index") or [])
                if pd is not None:
                    items.append(ItemSpec(key=name, pd=pd, color=c.color, mode=mode,
                                          offset=panel.offsets.get(name, (0.0, 0.0, 0.0)), edges=self.meshes.edges(name, pd)))
                continue
            if panel.layout == "grid" and self.s.qty(name) > 1 and self.meshes.n_solids(name) > 1:
                pd = self.meshes.solid(name, 0)  # one of a group of identical items (one screw out of four)
                key = f"{name}#0"
            else:
                pd, key = self.meshes.polydata(name), name
            if pd is None:
                continue
            items.append(ItemSpec(key=name, pd=pd, color=c.color, mode=mode, offset=panel.offsets.get(name, (0.0, 0.0, 0.0)),
                                  edges=self.meshes.edges(key, pd)))
        for scr in getattr(self.s, "screens", None) or []:  # MAJ-32: the display shows the firmware's idle face, not black
            comp = scr.get("component")
            mode = panel.show.get(comp)
            if mode is None or mode == "ghost" or panel.layout == "grid":
                continue
            root = Path(self.meshes.brep_dir).parent.parent
            tex = root / str(scr.get("texture") or "")
            if not tex.is_file():
                continue
            items.append(ItemSpec(key=f"{comp}#screen", pd=screen_quad(scr), color=(1.0, 1.0, 1.0), mode=mode,
                                  offset=panel.offsets.get(comp, (0.0, 0.0, 0.0)), texture=str(tex)))
        for ms in panel.meshes:
            pd = self.meshes.stl(ms.path)
            items.append(ItemSpec(key=ms.name or ms.path, pd=pd, color=ms.color, mode="solid", offset=ms.offset,
                                  edges=self.meshes.edges("stl:" + ms.path, pd)))
        return items

    @staticmethod
    def _pd_bbox(it: ItemSpec) -> Optional[tuple]:
        b = it.pd.GetBounds()
        if b[0] > b[1]:
            return None
        return bbox_offset(tuple(b), it.offset)

    @staticmethod
    def _shelf_pack(items: list[ItemSpec], gap: float, row_w: Optional[float] = None) -> None:
        """Lay items flat on z=0 in rows (largest first, smallest rows nearest the camera at -Y)."""
        dims = []
        for it in items:
            b = it.pd.GetBounds()
            dims.append((it, b, b[1] - b[0], b[3] - b[2]))
        dims.sort(key=lambda t: -(t[2] * t[3]))
        if row_w is None:
            area = sum((w + gap) * (d + gap) for _, _, w, d in dims)
            row_w = max(max(w for _, _, w, _ in dims) + gap, math.sqrt(area) * 1.5)
        x = y = 0.0
        row_d = 0.0
        rows: list[list] = [[]]
        for it, b, w, d in dims:
            if x > 0 and x + w > row_w:
                y -= row_d + gap * 1.6
                x, row_d = 0.0, 0.0
                rows.append([])
            it.offset = (x - b[0], y - b[3], -b[4])
            rows[-1].append((it, w))
            x += w + gap
            row_d = max(row_d, d)
        for row in rows:  # centre each row
            tot = sum(w for _, w in row) + gap * (len(row) - 1)
            for it, _ in row:
                it.offset = (it.offset[0] - tot / 2, it.offset[1], it.offset[2])

    def _auto_arrows(self, panel: PanelSpec, min_gap: float = 6.0) -> list:
        """One motion arrow per displaced solid, on its insertion axis: from the centroid where the part is drawn
        toward where it goes (MIN-31). Drawn as a 2D overlay, so the part never hides its own arrow."""
        out = []
        for name in panel.arrow_comps:
            o = panel.offsets.get(name)
            if not o:
                continue
            L = math.sqrt(sum(v * v for v in o))
            if L < 1e-6:
                continue
            ax = (o[0] / L, o[1] / L, o[2] / L)
            comp = self.s.comp(name)
            many = bool(comp and self.s.qty(name) > 1) or bool(comp and any(w in name.lower() for w in ("screw", "insert", "nut", "feet", "foot")))
            base = name.split("#", 1)[0]
            ks = list((comp.raw.get("solid_index") if comp and "#" in name else None) or range(min(self.meshes.n_solids(base), 8)))
            for k in (ks[:8] if many else [None]):
                pd = self.meshes.solid(base, k) if k is not None else self.meshes.polydata(base)
                if pd is None:
                    continue
                b = pd.GetBounds()
                if b[0] > b[1]:
                    continue
                c = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)  # home centroid
                half = 0.5 * abs((b[1] - b[0]) * ax[0] + (b[3] - b[2]) * ax[1] + (b[5] - b[4]) * ax[2])
                if L - half < min_gap:
                    continue
                start = tuple(c[i] + o[i] for i in range(3))  # where the part is drawn
                end = tuple(c[i] + ax[i] * min(half + 2.0, L * 0.5) for i in range(3))  # just short of its seat
                out.append((start, end))
        return out

    @staticmethod
    def _draw_arrows_2d(img: Image.Image, pr, arrows: list, w: int, h: int) -> int:
        d = ImageDraw.Draw(img)
        n = 0
        for a, b in arrows:
            pa, pb = pr.project(a), pr.project(b)
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            Ls = math.hypot(dx, dy)
            if Ls < 14:
                continue
            ux, uy = dx / Ls, dy / Ls
            head = min(22.0, 0.45 * Ls)
            hb = (pb[0] - ux * head, pb[1] - uy * head)
            px, py = -uy, ux
            tri = [pb, (hb[0] + px * head * 0.55, hb[1] + py * head * 0.55), (hb[0] - px * head * 0.55, hb[1] - py * head * 0.55)]
            d.line([pa, hb], fill=(255, 255, 255), width=9)
            d.polygon([(x + ux * 1.5, y + uy * 1.5) for x, y in tri], fill=(255, 255, 255))
            d.line([pa, hb], fill=COBALT, width=5)
            d.polygon(tri, fill=COBALT)
            d.ellipse([pa[0] - 4, pa[1] - 4, pa[0] + 4, pa[1] + 4], fill=COBALT)
            n += 1
        return n

    def panel_bbox(self, panel: PanelSpec, items: list[ItemSpec]) -> Optional[tuple]:
        scene = bbox_union(self._pd_bbox(it) for it in items)
        if panel.focus_meshes or panel.layout == "grid":
            bb = scene
        else:
            names = panel.focus or [k for k, m in panel.show.items() if m == "focus"] or list(panel.show)
            bb = bbox_union(self._pd_bbox(it) for it in items if it.key in names)
            if bb is None:
                bb = bbox_union(bbox_offset(self.s.comp(n).bbox, panel.offsets.get(n, (0, 0, 0)))
                                for n in names if self.s.comp(n) and self.s.comp(n).bbox)
        if bb and panel.min_extent > 0:
            c = [(bb[0] + bb[1]) / 2, (bb[2] + bb[3]) / 2, (bb[4] + bb[5]) / 2]
            sz = [max(bb[1] - bb[0], panel.min_extent), max(bb[3] - bb[2], panel.min_extent), max(bb[5] - bb[4], panel.min_extent)]
            lo, hi = [c[i] - sz[i] / 2 for i in range(3)], [c[i] + sz[i] / 2 for i in range(3)]
            if scene:  # keep the enlarged frame inside the visible scene so no half-panel is empty
                for i in range(3):
                    s0, s1 = scene[2 * i], scene[2 * i + 1]
                    if hi[i] - lo[i] <= s1 - s0:
                        if lo[i] < s0:
                            hi[i] += s0 - lo[i]
                            lo[i] = s0
                        if hi[i] > s1:
                            lo[i] -= hi[i] - s1
                            hi[i] = s1
                    else:
                        lo[i], hi[i] = min(lo[i], s0), max(hi[i], s1)
            bb = (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
        return bb

    def _anchor(self, lab: LabelSpec, pr, panel: PanelSpec, items: list[ItemSpec]) -> Optional[tuple[float, float]]:
        if lab.ray:
            target, origin, direction, fallback = lab.ray
            targets = [target] if isinstance(target, str) else list(target)
            hits = []
            for t in targets:
                it = next((i for i in items if i.key == t), None)
                if it is not None:
                    h = ray_hit(it.pd, it.offset, origin, direction)
                    if h is not None:
                        hits.append(h)
            if hits:
                o = np.array(origin, float)
                return pr.project(min(hits, key=lambda h: float(np.linalg.norm(np.array(h) - o))))
            if fallback:
                lab = LabelSpec(text=lab.text, comp=fallback)
        if lab.anchor and lab.anchor in self.s.anchors:
            p = self.s.anchors[lab.anchor]
            owner = self.s.owner(lab.anchor)
            off = panel.offsets.get(owner, (0.0, 0.0, 0.0)) if owner else (0.0, 0.0, 0.0)
            q = (p[0] + off[0], p[1] + off[1], p[2] + off[2])
            return pr.project(q) if pr.visible(q) else None
        if lab.point:
            return pr.project(lab.point)
        if lab.comp:
            if lab.comp in pr.keys:
                a = visible_anchor(pr.ids, pr.keys.index(lab.comp))
                if a:
                    return a
                return None  # shown but fully hidden in this view: no dot on the wrong part
            it = next((i for i in items if i.key == lab.comp), None)
            bb = self._pd_bbox(it) if it else None
            if bb and it.mode == "ghost":
                return pr.project(((bb[0] + bb[1]) / 2, (bb[2] + bb[3]) / 2, (bb[4] + bb[5]) / 2))
        return None

    def render_panel(self, panel: PanelSpec, w: int, h: int) -> tuple[Image.Image, L.Layout, list[list[str]], dict, list[str]]:
        items = self._items(panel)
        if panel.focus_meshes:
            self._shelf_pack(items, gap=14.0)
        elif panel.layout == "grid":
            self._shelf_pack(items, gap=22.0)
        bb = self.panel_bbox(panel, items)
        if not items or bb is None:
            raise RuntimeError("nothing to render in panel: " + panel.subtitle)
        floor = None
        if panel.floor:
            floor = min((self._pd_bbox(it) or (0, 0, 0, 0, 0, 0))[4] for it in items)
        pr = self.r.render(items, w, h, panel.view_dir, bb, pad=panel.pad, label_room=panel.label_room, floor=floor,
                           arrows=list(panel.arrows))
        img = Image.fromarray(pr.rgb)
        self._draw_arrows_2d(img, pr, self._auto_arrows(panel), w, h)
        rgb = np.asarray(img)
        dropped = []
        dropped_prio = []
        labs: list[L.LabelIn] = []
        texts: list[list[str]] = []
        seen_txt: set[str] = set()
        for k, lab in sorted(enumerate(panel.labels), key=lambda t: -t[1].priority):
            if lab.text in seen_txt:
                continue  # one label per text in a panel (MIN-31)
            seen_txt.add(lab.text)
            a = self._anchor(lab, pr, panel, items)
            if a is None or not (6 <= a[0] <= w - 6 and 6 <= a[1] <= h - 6):
                dropped.append(lab.text.replace("\n", " "))
                if lab.priority >= 2:
                    dropped_prio.append(lab.text.replace("\n", " "))
                continue
            lw, lh, lines = measure_label(lab.text)
            labs.append(L.LabelIn(anchor=a, w=lw, h=lh, priority=lab.priority, key=lab.text))
            texts.append(lines)
        from scipy import ndimage

        bg = np.array(BG_RGB, dtype=np.int16)
        busy = np.abs(rgb.astype(np.int16) - bg).sum(axis=2) > 24
        focus = ndimage.binary_dilation(pr.focus_mask, iterations=4) if pr.focus_mask.any() else pr.focus_mask
        layout = L.place(labs, L.Box(0, 0, w, h), mask=focus, soft_mask=busy | pr.model_mask)
        draw_labels(img, layout, texts)
        lost = {labs[i].key for i in range(len(labs))} - {labs[p.idx].key for p in layout.placed}
        for k in lost:  # placed by nobody: the layout could not fit it
            dropped.append(k.replace("\n", " "))
            if any(l.text == k and l.priority >= 2 for l in panel.labels):
                dropped_prio.append(k.replace("\n", " "))
        placed = [labs[p.idx].key.replace("\n", " ") for p in layout.placed]
        return img, layout, texts, pr.camera, dropped, dropped_prio, placed

    def render_figure(self, fig: FigureSpec, out_root: Path) -> FigureResult:
        t0 = time.time()
        W, H = fig.wh
        top, sub, foot = 70, 40, 46
        n = len(fig.panels)
        pw = W // n
        fsub = font("regular", 21)
        wrapped = [wrap_px(panel.subtitle, pw - 56, fsub) for panel in fig.panels]  # never run into the next panel
        sub = 40 + 26 * (max([len(w) for w in wrapped] + [1]) - 1)
        canvas = Image.new("RGB", (W, H), PAPER)
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, 0, W, top + sub], fill=PANEL)
        d.text((28, 18), fig.title, font=font("medium", 31), fill=INK)
        ph = H - top - sub - foot
        res = FigureResult(file=fig.file, ok=True, seconds=0.0)
        for i, panel in enumerate(fig.panels):
            x0 = i * pw
            for k, ln in enumerate(wrapped[i]):
                d.text((x0 + 28, top + 6 + 26 * k), ln, font=fsub, fill=MUTED)
            try:
                img, layout, texts, cam, dropped, dropped_prio, placed = self.render_panel(panel, pw, ph)
            except Exception as exc:
                res.ok = False
                res.error = f"{type(exc).__name__}: {exc}"
                res.seconds = time.time() - t0
                return res
            canvas.paste(img, (x0, top + sub))
            res.labels += len(layout.placed)
            res.violations += [f"panel {i}: {v}" for v in layout.violations]
            res.dropped += dropped
            res.dropped_priority += dropped_prio
            res.placed += placed
            res.cameras.append(cam)
        if n > 1:
            for i in range(1, n):
                d.line([(i * pw, top + sub), (i * pw, H - foot)], fill=(255, 255, 255), width=4)
        d.rectangle([0, H - foot, W, H], fill=PANEL)
        d.text((28, H - foot + 13), fig.footer, font=font("regular", 16), fill=MUTED)
        path = out_root / fig.file
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path, "PNG", compress_level=6)
        res.seconds = time.time() - t0
        return res


def frame_pcb(src: Path, dst: Path, product: str) -> bool:
    """Frame pcb/board-top.png like the other manual images (title band + footer)."""
    try:
        im = Image.open(src).convert("RGB")
        W = 1400
        scale = min((W - 80) / im.width, 1500 / im.height)
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        H = im.height + 70 + 40 + 46 + 30
        cv = Image.new("RGB", (W, H), PAPER)
        d = ImageDraw.Draw(cv)
        d.rectangle([0, 0, W, 110], fill=PANEL)
        d.text((28, 18), f"Your backplane: the routed board for the {product}", font=font("medium", 31), fill=INK)
        d.text((28, 76), "Top side, USB-C at the rear edge (top of the picture); +X (the gadget's left) is on your right",
               font=font("regular", 21), fill=MUTED)
        cv.paste(im, ((W - im.width) // 2, 125))
        d.rectangle([0, H - 46, W, H], fill=PANEL)
        d.text((28, H - 33), "KiCad 3D render of pcb/backplane.kicad_pcb from the PCB stage | not a photo | not an electrical test",
               font=font("regular", 16), fill=MUTED)
        dst.parent.mkdir(parents=True, exist_ok=True)
        cv.save(dst, "PNG", compress_level=6)
        return True
    except Exception:
        return False
