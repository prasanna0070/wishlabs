"""Silkscreen: port names, per-pin function names, pin-1 dots, board name/revision/ID, test-pad labels.

Top: every populated port gets its reference designator as the build guide names it (J_MIC, J_I2C1, A1, S1, J_PWR,
plus U1 between the devkit sockets and BP1 in the title; REVIEW-1 MAJ-16), a function line where the id alone is not
enough ("LED 5V", "USB-C", "AMP", "SD"; MAJ-3), and a function label beside each pin (backplane-v1 `slots[].silk`
when present). Text boxes are measured with KiCad and kept clear of pads, other parts' silk outlines and other text. Bottom: board name, revision,
design ID and an ID-label box (where the serial/QR sticker goes; it is a placeholder, not a code), plus
labels for the factory test pads. Bottom text is mirrored so it reads correctly from below.
"""

from __future__ import annotations

import math

import pcbnew

from .board import BoardBuilder, _overlap
from .design import Part

ABBR = {"GND": "G", "3V3": "3V3", "5V": "5V", "VCC": "VCC", "VIN": "VIN", "SPK+": "+", "SPK-": "-"}
INWARD = {"front": (0.0, 1.0), "rear": (0.0, -1.0), "left": (1.0, 0.0), "right": (-1.0, 0.0)}
CHAR_W = 0.95  # text advance per char as a fraction of text height (KiCad stroke font, conservative)


def _text_box(cx, cy, w, h):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def short_name(name: str) -> str:
    if name.startswith("GPIO"):
        rest = name[4:]
        if "/" in rest:
            return rest.split("/", 1)[1].replace("0", "") if rest.split("/", 1)[1] in ("TX0", "RX0") else rest
        return rest
    return ABBR.get(name, name)


class Silk:
    def __init__(self, bb: BoardBuilder):
        self.bb = bb
        self._dims: dict = {}
        self.why = ""  # what blocked the last rejected silk box (for the skipped log)
        self.pads = bb.pad_boxes()
        self.obstacles = self._obstacles()
        self.top_boxes: list[tuple] = []
        self.bot_boxes: list[tuple] = []
        self.skipped: list[str] = []

    def _obstacles(self) -> dict[str, tuple]:
        """Per footprint, what silk text must keep clear of: its own silkscreen outline (the body as printed) when it
        has one, else its courtyard. Text may sit inside another connector's courtyard but never on its body or
        outline (KiCad DRC flags silk over silk)."""
        out = {}
        f = self.bb.frame
        for ref, fp in self.bb.fps.items():
            xs, ys = [], []
            for g in fp.GraphicalItems():
                if g.GetLayer() == pcbnew.F_SilkS and g.GetClass() == "PCB_SHAPE":
                    bbx = g.GetBoundingBox()
                    for v in (bbx.GetOrigin(), bbx.GetEnd()):
                        x, y = f.to_board(v)
                        xs.append(x)
                        ys.append(y)
            out[ref] = (min(xs), min(ys), max(xs), max(ys)) if xs else self.bb.boxes[ref]
        return out

    def _blocked(self, box, own: str | None, layer="top") -> bool:
        W, H = self.bb.d.width, self.bb.d.height
        self.why = ""
        if box[0] < 0.4 or box[1] < 0.4 or box[2] > W - 0.4 or box[3] > H - 0.4:
            self.why = "board edge"
            return True
        if any(_overlap(box, pb, 0.05) for pb in self.pads["top" if layer == "top" else "bottom"]):
            self.why = "a pad"
            return True
        if layer == "top":
            for ref, cb in self.obstacles.items():
                if ref == own or ref.startswith("TP"):
                    continue
                if _overlap(box, cb, 0.15):
                    self.why = f"{ref} outline"
                    return True
            if any(_overlap(box, b, 0.2) for b in self.top_boxes):
                self.why = "other silk text"
                return True
            return False
        for ref, cb in self.bb.boxes.items():
            if ref.startswith("TP") or ref.startswith("H") or ref.startswith("S1_POST"):
                if _overlap(box, cb, 0.1):
                    return True
        return any(_overlap(box, b, 0.2) for b in self.bot_boxes)

    # -------------------------------------------------------------- ports
    def port(self, part: Part, name_label: bool = True):
        """Per-pin function labels on the side with room, then the name block (designator + function line) wherever
        it fits: beyond the pin labels, on the other side, or at either end of the pin row. Then the pin-1 dot."""
        fp = self.bb.fps[part.ref]
        pads = sorted([p for p in fp.Pads() if p.GetNumber()], key=lambda p: int(p.GetNumber()) if p.GetNumber().isdigit() else 0)
        if len(pads) < 1:
            return
        f = self.bb.frame
        pos = [f.to_board(p.GetPosition()) for p in pads]
        if len(pos) >= 2:
            ax, ay = pos[-1][0] - pos[0][0], pos[-1][1] - pos[0][1]
            n = math.hypot(ax, ay) or 1.0
            row = (ax / n, ay / n)
        else:
            row = (1.0, 0.0)
        cb = self.bb.boxes[part.ref]
        cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        W, H = self.bb.d.width, self.bb.d.height
        normals = [(round(-row[1], 6), round(row[0], 6)), (round(row[1], 6), round(-row[0], 6))]

        def room(nv):  # free distance from the courtyard to the board edge along nv
            d = []
            if nv[0] > 0.5:
                d.append(W - cb[2])
            if nv[0] < -0.5:
                d.append(cb[0])
            if nv[1] > 0.5:
                d.append(H - cb[3])
            if nv[1] < -0.5:
                d.append(cb[1])
            return min(d) if d else 0.0
        candidates = sorted(normals, key=lambda nv: -room(nv))
        names = [short_name(part.pin_names.get(p.GetNumber(), "")) for p in pads]
        pitch = math.hypot(pos[1][0] - pos[0][0], pos[1][1] - pos[0][1]) if len(pos) > 1 else 2.5
        pin_side = None
        if len(pos) > 1 and pitch >= 1.9 and any(names):
            why = []
            # labels running away from the connector first; then short labels lying along the row (one per pin)
            for across in (False, True):
                for nv in candidates:
                    got = self._pin_labels(part, pos, row, nv, cb, names, across=across, pitch=pitch)
                    if got is not None:
                        pin_side = (nv, got)
                        break
                    why.append(self.why)
                if pin_side:
                    break
            if pin_side is None:
                self.skipped.append(f"{part.ref} pin labels (blocked by {', '.join(sorted(set(w for w in why if w)))})")
        if name_label:
            label = part.label or (names[0] if len(pos) == 1 and names and names[0] else part.ref)
            if not self._name_block(part, label, part.sub_label, pos, row, cb, candidates, pin_side):
                self.skipped.append(part.ref)
        # pin-1 dot: just beyond pad 1 along the row, outside the body
        if len(pos) >= 2:
            half = max(abs((cb[2] - cb[0]) * row[0]), abs((cb[3] - cb[1]) * row[1])) / 2
            c_along = (cx - pos[0][0]) * row[0] + (cy - pos[0][1]) * row[1]
            d = c_along - half - 0.7  # negative: beyond pad 1 side
            px, py = pos[0][0] + row[0] * d, pos[0][1] + row[1] * d
            box = _text_box(px, py, 0.9, 0.9)
            if not self._blocked(box, part.ref):
                self.bb.silk_shape("circle", ((px, py), 0.3), width=0.3, filled=True)
                self.top_boxes.append(box)

    def dims(self, text: str, size: float, thick: float = 0.15) -> tuple[float, float]:
        """(reading length, line height) in mm of an unrotated KiCad stroke-font text, measured by KiCad itself
        (character widths differ a lot, so a per-character estimate lets short labels collide)."""
        return self._measure(text, size, thick)[:2]

    def _measure(self, text: str, size: float, thick: float) -> tuple[float, float, float, float]:
        """(w, h, dx, dy): box size, and the box centre minus the text anchor in board axes (+y up), unrotated."""
        key = (text, size, thick)
        if key not in self._dims:
            t = pcbnew.PCB_TEXT(self.bb.board)
            t.SetText(text)
            t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
            t.SetTextThickness(pcbnew.FromMM(thick))
            t.SetPosition(pcbnew.VECTOR2I(0, 0))
            box = t.GetBoundingBox()
            c = box.GetCenter()
            self._dims[key] = (pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight()),
                               pcbnew.ToMM(c.x), -pcbnew.ToMM(c.y))
        return self._dims[key]

    def put(self, text: str, cx: float, cy: float, size: float, thick: float, rot: float = 0.0):
        """Place a text so its measured box is centred on (cx, cy) (board coords), rot 0 or 90 (CCW)."""
        _, _, dx, dy = self._measure(text, size, thick)
        if rot == 90.0:
            dx, dy = -dy, dx
        return self.bb.text(text, cx - dx, cy - dy, size, thick, rot)

    @staticmethod
    def _extent(cb, origin, nv) -> float:
        corners = [(cb[0], cb[1]), (cb[2], cb[1]), (cb[2], cb[3]), (cb[0], cb[3])]
        return max((x - origin[0]) * nv[0] + (y - origin[1]) * nv[1] for x, y in corners)

    def _pin_labels(self, part, pos, row, nv, cb, names, size=0.8, across=False, pitch=2.0):
        """Function name beside each pin on side nv. across=False: the text runs away from the connector (fits any
        length); across=True: the text lies along the pin row, one short label over each pin (only when every label
        is narrower than the pin pitch). Returns the far edge distance (from the pad row), or None on a collision."""
        e = self._extent(cb, pos[0], nv) + 0.25
        vertical_row = abs(row[1]) > abs(row[0])
        if across and max(self.dims(nm, size, 0.12)[0] for nm in names if nm) > pitch - 0.2:
            self.why = "labels wider than the pitch"
            return None
        texts, boxes, far = [], [], e
        for (x, y), nm in zip(pos, names):
            if not nm:
                continue
            L, th = self.dims(nm, size, 0.12)
            depth = th if across else L
            c = e + depth / 2
            tx, ty = x + nv[0] * c, y + nv[1] * c
            if across:
                rot = 90.0 if vertical_row else 0.0
                w, h = (th, L) if vertical_row else (L, th)
            else:
                rot = 0.0 if vertical_row else 90.0
                w, h = (L, th) if vertical_row else (th, L)
            box = _text_box(tx, ty, w, h)
            if self._blocked(box, part.ref):
                self.why = f"{nm}: {self.why}"
                return None
            boxes.append(box)
            texts.append((nm, tx, ty, rot))
            far = max(far, e + depth)
        for nm, tx, ty, rot in texts:
            self.put(nm, tx, ty, size, 0.12, rot)
        self.top_boxes.extend(boxes)
        return far

    GAP = 0.3  # mm between stacked lines

    @staticmethod
    def _thick(sz: float) -> float:
        return 0.2 if sz >= 1.0 else 0.15

    def _block(self, lines: list[str], size: float):
        """(sizes, along, across): text lines stacked; 'along' = reading direction extent, 'across' = stack extent."""
        sizes = [size] + [max(0.8, round(size - 0.2, 2))] * (len(lines) - 1)
        d = [self.dims(t, sz, self._thick(sz)) for t, sz in zip(lines, sizes)]
        along = max(w for w, _ in d)
        across = sum(h for _, h in d) + self.GAP * (len(lines) - 1)
        return sizes, along, across

    def _draw_block(self, lines, sizes, cx, cy, along, across, rot):
        """Draw stacked lines centred on (cx, cy). rot 0: lines top to bottom; rot 90: text reads upward, lines left
        to right (the text's 'up' points to -x)."""
        off = -across / 2
        for t, sz in zip(lines, sizes):
            h = self.dims(t, sz, self._thick(sz))[1]
            c = off + h / 2
            if rot == 0.0:
                self.put(t, cx, cy - c, sz, self._thick(sz), 0.0)
            else:
                self.put(t, cx + c, cy, sz, self._thick(sz), 90.0)
            off += h + self.GAP

    def _name_block(self, part, label, sub, pos, row, cb, candidates, pin_side) -> bool:
        vertical_row = abs(row[1]) > abs(row[0])
        rot0 = 90.0 if vertical_row else 0.0  # text reads along the pin row
        variants = ([[label, sub], [f"{label} {sub}"]] if sub else []) + [[label]]
        mid = sum((x - pos[0][0]) * row[0] + (y - pos[0][1]) * row[1] for x, y in pos) / len(pos)
        base = (pos[0][0] + row[0] * mid, pos[0][1] + row[1] * mid)
        half_row = max(abs((cb[2] - cb[0]) * row[0]), abs((cb[3] - cb[1]) * row[1])) / 2
        ccx, ccy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        for lines in variants:
            for size in (1.2, 1.0, 0.9):
                sizes, along, across = self._block(lines, size)
                spots = []  # (cx, cy, rot)
                # beside the connector (beyond its pin labels on that side), text along the row
                a_row, a_nrm = (across, along) if vertical_row else (along, across)
                for nv in candidates:
                    e = (pin_side[1] + 0.4) if pin_side and pin_side[0] == nv else self._extent(cb, pos[0], nv) + 0.3
                    for slide in (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 4.0, -4.0):
                        c = e + a_nrm / 2
                        spots.append((base[0] + nv[0] * c + row[0] * slide, base[1] + nv[1] * c + row[1] * slide, rot0))
                # beyond either end of the pin row: text along the row, then standing across it
                for rot in (rot0, 90.0 - rot0):
                    along_row = along if (rot == 90.0) == vertical_row else across
                    for sgn in (1.0, -1.0):
                        c = half_row + 0.4 + along_row / 2
                        for shift in (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0):
                            nv = candidates[0]
                            spots.append((ccx + row[0] * c * sgn + nv[0] * shift,
                                          ccy + row[1] * c * sgn + nv[1] * shift, rot))
                for bx, by, rot in spots:
                    w, h = (across, along) if rot == 90.0 else (along, across)
                    box = _text_box(bx, by, w, h)
                    if not self._blocked(box, part.ref):
                        self._draw_block(lines, sizes, bx, by, along, across, rot)
                        self.top_boxes.append(box)
                        if len(lines) == 1 and sub and lines[0] == label:
                            self.skipped.append(f"{part.ref} function line '{sub}'")
                        return True
        return False

    def free_label(self, lines: list[str], prefer: tuple[float, float], size: float = 1.4, tag: str = "") -> bool:
        """A name block anywhere free on the top silk, nearest to `prefer` (board coords)."""
        sizes, along, across = self._block(lines, size)
        spot = self.find_spot(along, across, "top", prefer)
        if spot is None:
            self.skipped.append(tag or lines[0])
            return False
        self._draw_block(lines, sizes, spot[0], spot[1], along, across, 0.0)
        self.top_boxes.append(_text_box(spot[0], spot[1], along, across))
        return True

    # -------------------------------------------------------------- reference designators
    def refdes(self, parts: list[Part]):
        """Support parts keep a small refdes on silk where it fits; ports/sockets/holes/pads move it to Fab."""
        b = self.bb
        for p in parts:
            fp = b.fps.get(p.ref)
            if fp is None:
                continue
            ref = fp.Reference()
            is_support = p.slot is None and not p.board_only and p.block not in ("test", "mech")
            bottom = fp.GetLayer() == pcbnew.B_Cu
            if not is_support:
                ref.SetLayer(pcbnew.B_Fab if bottom else pcbnew.F_Fab)
                continue
            cb = b.boxes[p.ref]
            cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
            size = 0.8
            L = len(p.ref) * CHAR_W * size
            placed = False
            for (x, y) in ((cx, cb[3] + 0.6), (cx, cb[1] - 0.6), (cb[0] - L / 2 - 0.3, cy), (cb[2] + L / 2 + 0.3, cy)):
                box = _text_box(x, y, L, size)
                if not self._blocked(box, p.ref):
                    ref.SetLayer(pcbnew.F_SilkS)
                    ref.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
                    ref.SetTextThickness(pcbnew.FromMM(0.12))
                    ref.SetTextAngleDegrees(0)
                    ref.SetPosition(b.frame.pt(x, y))
                    self.top_boxes.append(box)
                    placed = True
                    break
            if not placed:
                ref.SetLayer(pcbnew.F_Fab)
                self.skipped.append(f"refdes {p.ref} on Fab")

    # -------------------------------------------------------------- free-spot search
    def find_spot(self, w: float, h: float, layer: str, prefer: tuple[float, float], step: float = 1.0):
        W, H = self.bb.d.width, self.bb.d.height
        pts = []
        y = h / 2 + 0.5
        while y < H - h / 2 - 0.5:
            x = w / 2 + 0.5
            while x < W - w / 2 - 0.5:
                pts.append(((x - prefer[0]) ** 2 + (y - prefer[1]) ** 2, x, y))
                x += step
            y += step
        for _, x, y in sorted(pts):
            if not self._blocked(_text_box(x, y, w, h), None, layer):
                return x, y
        return None

    # -------------------------------------------------------------- bottom side identity
    def bottom_identity(self, title: str, sub: str, did: str):
        W, H = self.bb.d.width, self.bb.d.height
        b = self.bb
        layer = pcbnew.B_SilkS
        w = max(len(title) * CHAR_W * 1.6, len(sub) * CHAR_W * 1.0) + 0.6
        h = 1.6 + 1.0 + 1.2
        spot = self.find_spot(w, h, "bottom", (W / 2, H * 0.25))
        if spot:
            cx, cy = spot
            b.text(title, cx, cy + h / 2 - 0.8, 1.6, 0.25, 0.0, layer, mirror=True)
            b.text(sub, cx, cy - h / 2 + 0.5, 1.0, 0.15, 0.0, layer, mirror=True)
            self.bot_boxes.append(_text_box(cx, cy, w, h))
        else:
            self.skipped.append("bottom title")
        # ID label box (where the serial/QR sticker goes): finder-style corners, placeholder only
        s = 11.0
        spot = self.find_spot(s + 0.6, s + 3.2, "bottom", (W * 0.8, H * 0.6))
        if spot is None:
            self.skipped.append("id box")
            return
        cx, cy = spot
        x0, y0 = cx - s / 2, cy - (s + 2.6) / 2
        b.silk_shape("rect", ((x0, y0 + 2.5), (x0 + s, y0 + s + 2.5)), 0.15, False, layer)
        for fx, fy in ((x0 + 0.6, y0 + s + 2.5 - 3.1), (x0 + s - 3.1, y0 + s + 2.5 - 3.1), (x0 + 0.6, y0 + 3.1)):
            b.silk_shape("rect", ((fx, fy), (fx + 2.5, fy + 2.5)), 0.2, False, layer)
            b.silk_shape("rect", ((fx + 0.8, fy + 0.8), (fx + 1.7, fy + 1.7)), 0.1, True, layer)
        b.text("ID LABEL", x0 + s / 2, y0 + 2.5 + s / 2, 1.0, 0.15, 0.0, layer, mirror=True)
        b.text(did, x0 + s / 2, y0 + 1.1, 0.9, 0.15, 0.0, layer, mirror=True)
        self.bot_boxes.append(_text_box(cx, cy, s + 0.6, s + 3.2))

    def test_pads(self, parts: list[Part]):
        b = self.bb
        short = {"UART0_TX": "TX", "UART0_RX": "RX", "BOOT_IO0": "IO0", "TXD0": "TX", "RXD0": "RX"}
        xs, ys = [], []
        for p in parts:
            fp = b.fps[p.ref]
            x, y = b.frame.to_board(fp.GetPosition())
            xs.append(x)
            ys.append(y)
            net = p.pads.get("1", "")
            name = short.get(net, net)
            for dy in (-2.1, 2.1):
                box = _text_box(x, y + dy, len(name) * CHAR_W * 0.9, 0.9)
                if not self._blocked(box, None, "bottom"):
                    b.text(name, x, y + dy, 0.9, 0.15, 0.0, pcbnew.B_SilkS, mirror=True)
                    self.bot_boxes.append(box)
                    break
        if parts:
            t = "FACTORY TEST (pogo, 5.08 mm)"
            w = len(t) * CHAR_W * 1.0
            for dy in (4.4, -4.4, 6.0, -6.0):
                box = _text_box((min(xs) + max(xs)) / 2, (max(ys) if dy > 0 else min(ys)) + dy, w, 1.0)
                if not self._blocked(box, None, "bottom"):
                    b.text(t, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, 1.0, 0.15, 0.0, pcbnew.B_SilkS, mirror=True)
                    self.bot_boxes.append(box)
                    break

    def top_title(self, title: str, sub: str):
        W, H = self.bb.d.width, self.bb.d.height
        w = max(len(title) * CHAR_W * 1.5, len(sub) * CHAR_W * 1.0) + 0.6
        h = 1.5 + 1.0 + 1.2
        spot = self.find_spot(w, h, "top", (W * 0.25, H * 0.25))
        if spot is None:
            self.skipped.append("top_title")
            return False
        cx, cy = spot
        self.bb.text(title, cx, cy + h / 2 - 0.75, 1.5, 0.22, 0.0)
        self.bb.text(sub, cx, cy - h / 2 + 0.5, 1.0, 0.15, 0.0)
        self.top_boxes.append(_text_box(cx, cy, w, h))
        return True


__all__ = ["Silk"]
