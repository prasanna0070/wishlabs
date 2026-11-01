"""A tiny 2D canvas with two backends (SVG text and a Pillow PNG) fed by the same draw calls, so the
wiring SVG and PNG can never disagree. Coordinates are px, y down; text y is the baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

from .illustrate import font, text_w

RGB = tuple[int, int, int]


def hexc(c: Optional[RGB]) -> str:
    return "none" if c is None else "#%02X%02X%02X" % tuple(int(v) for v in c[:3])


@dataclass
class Canvas:
    w: int
    h: int
    bg: RGB = (246, 245, 241)
    ops: list = field(default_factory=list)

    # ------------------------------------------------------------------ primitives
    def rect(self, x, y, w, h, fill: Optional[RGB] = None, stroke: Optional[RGB] = None, sw: float = 1.0, r: float = 0.0,
             dash: Optional[tuple[int, int]] = None):
        self.ops.append(("rect", x, y, w, h, fill, stroke, sw, r, dash))

    def line(self, x1, y1, x2, y2, stroke: RGB, sw: float = 1.0, dash: Optional[tuple[int, int]] = None):
        self.ops.append(("line", x1, y1, x2, y2, stroke, sw, dash))

    def polyline(self, pts, stroke: RGB, sw: float = 1.0):
        self.ops.append(("poly", [tuple(p) for p in pts], stroke, sw))

    def circle(self, cx, cy, r, fill: Optional[RGB] = None, stroke: Optional[RGB] = None, sw: float = 1.0):
        self.ops.append(("circle", cx, cy, r, fill, stroke, sw))

    def text(self, x, y, s: str, size: int = 18, color: RGB = (27, 31, 36), weight: str = "regular", anchor: str = "start"):
        self.ops.append(("text", x, y, str(s), size, color, weight, anchor))

    def measure(self, s: str, size: int = 18, weight: str = "regular") -> float:
        return text_w(str(s), font(weight, size))

    # ------------------------------------------------------------------ SVG
    def to_svg(self) -> str:
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
               'font-family="Inter, \'DejaVu Sans\', Arial, sans-serif">',
               f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="{hexc(self.bg)}"/>']
        for op in self.ops:
            k = op[0]
            if k == "rect":
                _, x, y, w, h, fill, stroke, sw, r, dash = op
                da = f' stroke-dasharray="{dash[0]} {dash[1]}"' if dash else ""
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r:.1f}" fill="{hexc(fill)}" '
                           f'stroke="{hexc(stroke)}" stroke-width="{sw if stroke else 0}"{da}/>')
            elif k == "line":
                _, x1, y1, x2, y2, stroke, sw, dash = op
                da = f' stroke-dasharray="{dash[0]} {dash[1]}"' if dash else ""
                out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{hexc(stroke)}" stroke-width="{sw}"{da}/>')
            elif k == "poly":
                _, pts, stroke, sw = op
                ps = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
                out.append(f'<polyline points="{ps}" fill="none" stroke="{hexc(stroke)}" stroke-width="{sw}" stroke-linejoin="round"/>')
            elif k == "circle":
                _, cx, cy, r, fill, stroke, sw = op
                out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{hexc(fill)}" stroke="{hexc(stroke)}" stroke-width="{sw if stroke else 0}"/>')
            elif k == "text":
                _, x, y, s, size, color, weight, anchor = op
                fw = {"regular": 400, "medium": 500, "semibold": 600}.get(weight, 400)
                fam = ' font-family="\'JetBrains Mono\', \'DejaVu Sans Mono\', monospace"' if weight == "mono" else ""
                out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{fw}" fill="{hexc(color)}" '
                           f'text-anchor="{anchor}"{fam}>{escape(s)}</text>')
        out.append("</svg>")
        return "\n".join(out)

    # ------------------------------------------------------------------ PNG
    def to_png(self, path: Path, scale: float = 1.0) -> None:
        S = scale
        img = Image.new("RGB", (int(self.w * S), int(self.h * S)), self.bg)
        d = ImageDraw.Draw(img)
        for op in self.ops:
            k = op[0]
            if k == "rect":
                _, x, y, w, h, fill, stroke, sw, r, dash = op
                box = [x * S, y * S, (x + w) * S, (y + h) * S]
                if dash and stroke:
                    if fill:
                        d.rounded_rectangle(box, radius=r * S, fill=fill)
                    _dashed_rect(d, box, stroke, max(1, int(sw * S)), dash, S)
                else:
                    d.rounded_rectangle(box, radius=r * S, fill=fill, outline=stroke, width=max(1, int(round(sw * S))) if stroke else 0)
            elif k == "line":
                _, x1, y1, x2, y2, stroke, sw, dash = op
                if dash:
                    _dashed_line(d, (x1 * S, y1 * S), (x2 * S, y2 * S), stroke, max(1, int(sw * S)), dash, S)
                else:
                    d.line([(x1 * S, y1 * S), (x2 * S, y2 * S)], fill=stroke, width=max(1, int(round(sw * S))))
            elif k == "poly":
                _, pts, stroke, sw = op
                d.line([(a * S, b * S) for a, b in pts], fill=stroke, width=max(1, int(round(sw * S))), joint="curve")
            elif k == "circle":
                _, cx, cy, r, fill, stroke, sw = op
                d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=fill, outline=stroke,
                          width=max(1, int(round(sw * S))) if stroke else 0)
            elif k == "text":
                _, x, y, s, size, color, weight, anchor = op
                f = font(weight, int(round(size * S)))
                a = {"start": "ls", "middle": "ms", "end": "rs"}[anchor]
                d.text((x * S, y * S), s, font=f, fill=color, anchor=a)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG", compress_level=6)


def _dashed_line(d, a, b, color, width, dash, S):
    import math

    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L == 0:
        return
    on, off = dash[0] * S, dash[1] * S
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    t = 0.0
    while t < L:
        e = min(L, t + on)
        d.line([(a[0] + ux * t, a[1] + uy * t), (a[0] + ux * e, a[1] + uy * e)], fill=color, width=width)
        t += on + off


def _dashed_rect(d, box, color, width, dash, S):
    x0, y0, x1, y1 = box
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        _dashed_line(d, a, b, color, width, dash, S)
