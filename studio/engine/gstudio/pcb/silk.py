"""Silkscreen: port names, per-pin function names, pin-1 dots, board name/revision/ID, test-pad labels.

Top: every populated port gets its name (I2C1, MIC, SPK, IO1, ...) and a vertical function label beside each
pin, placed on the inward side of the connector where nothing else sits. Bottom: board name, revision,
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
        self.pads = bb.pad_boxes()
        self.top_boxes: list[tuple] = []
        self.bot_boxes: list[tuple] = []
        self.skipped: list[str] = []

    def _blocked(self, box, own: str | None, layer="top") -> bool:
        W, H = self.bb.d.width, self.bb.d.height
        if box[0] < 0.4 or box[1] < 0.4 or box[2] > W - 0.4 or box[3] > H - 0.4:
            return True
        if any(_overlap(box, pb, 0.05) for pb in self.pads["top" if layer == "top" else "bottom"]):
            return True
        if layer == "top":
            for ref, cb in self.bb.boxes.items():
                if ref == own or ref.startswith("TP"):
                    continue
                if _overlap(box, cb, 0.1):
                    return True
            return any(_overlap(box, b, 0.2) for b in self.top_boxes)
        for ref, cb in self.bb.boxes.items():
            if ref.startswith("TP") or ref.startswith("H") or ref.startswith("S1_POST"):
                if _overlap(box, cb, 0.1):
                    return True
        return any(_overlap(box, b, 0.2) for b in self.bot_boxes)

    # -------------------------------------------------------------- ports
    def port(self, part: Part, name_label: bool = True):
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

        def room(nv):  # free distance from the courtyard centre to the board edge along nv
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
        placed = False
        for pins_, name_ in ((True, name_label), (False, name_label), (True, False)):
            for nv in candidates:
                if self._port_side(part, pads, pos, row, nv, cb, pins_, name_):
                    placed = True
                    break
            if placed:
                break
        if not placed and name_label:
            placed = self._port_end(part, pos, row, cb)
        if not placed:
            self.skipped.append(part.ref)
        # pin-1 dot: just beyond pad 1 along the row, outside the body
        if len(pos) >= 2:
            half = max(abs((cb[2] - cb[0]) * row[0]), abs((cb[3] - cb[1]) * row[1])) / 2
            c_along = (cx - pos[0][0]) * row[0] + (cy - pos[0][1]) * row[1]
            d = c_along - half - 0.7  # negative: beyond pad 1 side
            for k in (d, d + 0.0):
                px, py = pos[0][0] + row[0] * k, pos[0][1] + row[1] * k
                box = _text_box(px, py, 0.9, 0.9)
                if not self._blocked(box, part.ref):
                    self.bb.silk_shape("circle", ((px, py), 0.3), width=0.3, filled=True)
                    self.top_boxes.append(box)
                    break

    def _port_end(self, part, pos, row, cb) -> bool:
        """Port name beside the connector, beyond one end of its pin row."""
        label = part.label or part.ref
        size = 1.0
        L = len(label) * CHAR_W * size
        horizontal = abs(row[0]) >= abs(row[1])
        for sgn in (-1, 1):
            if horizontal:
                x = (cb[0] - 0.4 - L / 2) if sgn < 0 else (cb[2] + 0.4 + L / 2)
                y = (cb[1] + cb[3]) / 2
                box, rot = _text_box(x, y, L, size), 0.0
            else:
                x = (cb[0] + cb[2]) / 2
                y = (cb[1] - 0.4 - L / 2) if sgn < 0 else (cb[3] + 0.4 + L / 2)
                box, rot = _text_box(x, y, size, L), 90.0
            if not self._blocked(box, part.ref):
                self.bb.text(label, x, y, size, 0.18, rot)
                self.top_boxes.append(box)
                return True
        return False

    def _port_side(self, part, pads, pos, row, nv, cb, want_pins=True, want_name=True) -> bool:
        """Try to place per-pin labels + the port name on side `nv` of the connector."""
        # distance from pad row to courtyard edge along nv
        def extent(nv_):
            corners = [(cb[0], cb[1]), (cb[2], cb[1]), (cb[2], cb[3]), (cb[0], cb[3])]
            return max((x - pos[0][0]) * nv_[0] + (y - pos[0][1]) * nv_[1] for x, y in corners)
        e = extent(nv) + 0.35
        size_pin = 0.8 if len(pos) > 1 else 1.0
        vertical_row = abs(row[1]) > abs(row[0])
        names = [short_name(part.pin_names.get(p.GetNumber(), "")) for p in pads]
        pitch = math.hypot(pos[1][0] - pos[0][0], pos[1][1] - pos[0][1]) if len(pos) > 1 else 2.5
        show_pins = want_pins and len(pos) > 1 and pitch >= 1.9
        boxes = []
        texts = []
        maxlen = max((len(nm) for nm in names), default=1)
        tl = maxlen * CHAR_W * size_pin + 0.2
        if show_pins:
            for (x, y), nm in zip(pos, names):
                if not nm:
                    continue
                L = len(nm) * CHAR_W * size_pin
                # text runs along nv (perpendicular to the row): vertical text for a horizontal row
                c = e + L / 2
                tx, ty = x + nv[0] * c, y + nv[1] * c
                rot = 0.0 if vertical_row else 90.0
                w, h = (L, size_pin) if vertical_row else (size_pin, L)
                box = _text_box(tx, ty, w, h)
                boxes.append(box)
                texts.append((nm, tx, ty, rot, size_pin, 0.12))
            e2 = e + tl + 0.45
        else:
            e2 = e
        label = part.label or (names[0] if len(pos) == 1 and names and names[0] else part.ref)
        size_l = 1.2 if len(label) > 1 else 1.0
        L = len(label) * CHAR_W * size_l
        mid_along = sum((x - pos[0][0]) * row[0] + (y - pos[0][1]) * row[1] for x, y in pos) / len(pos)
        bx = pos[0][0] + row[0] * mid_along + nv[0] * (e2 + (L / 2 if not vertical_row else size_l / 2))
        by = pos[0][1] + row[1] * mid_along + nv[1] * (e2 + (size_l / 2 if not vertical_row else L / 2))
        rot = 0.0
        if vertical_row and abs(nv[0]) > 0.5:
            rot = 90.0
            bx = pos[0][0] + row[0] * mid_along + nv[0] * (e2 + size_l / 2)
            by = pos[0][1] + row[1] * mid_along
            lbox = _text_box(bx, by, size_l, L)
        elif not vertical_row:
            bx = pos[0][0] + row[0] * mid_along
            by = pos[0][1] + row[1] * mid_along + nv[1] * (e2 + size_l / 2)
            lbox = _text_box(bx, by, L, size_l)
        else:
            lbox = _text_box(bx, by, L, size_l)
        allb = boxes + ([lbox] if want_name else [])
        if not allb:
            return False
        if any(self._blocked(b, part.ref) for b in allb):
            if show_pins:
                # try just the port name right next to the connector
                return False
            return False
        for nm, tx, ty, r, s, th in texts:
            self.bb.text(nm, tx, ty, s, th, r)
        if want_name:
            self.bb.text(label, bx, by, size_l, 0.2, rot)
        self.top_boxes.extend(allb)
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
