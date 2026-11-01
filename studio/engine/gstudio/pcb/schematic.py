"""Programmatic KiCad 9 schematic (.kicad_sch) for one design's backplane.

Layout: functional blocks (power input, core, audio, storage, ports, test, mechanical) framed with titled
rectangles. Every connected pin gets a 2.54 mm wire stub ending in a power symbol (GND, +5V, +3V3, VBUS) or a
local net label; unused pins get a no-connect flag. Pin coordinates come from the real library symbol, so
labels attach exactly (KiCad connects only on identical coordinates).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from . import sexpr, symbols
from .design import Design, Part, _natkey
from .sexpr import Q

GRID = 1.27
STUB = 2.54
FONT = 1.27
SCH_VERSION = 20250114
POWER_SYMBOL = {"GND": "power:GND", "+5V": "power:+5V", "+3V3": "power:+3V3", "VBUS": "power:VBUS"}
FLAG_NETS = ["VBUS", "+5V", "+3V3", "GND"]
BLOCKS = [
    ("power", "POWER INPUT  (USB-C 5 V sink, protection, devkit feed)"),
    ("core", "CORE  (ESP32-S3-DevKitC-1 sockets)"),
    ("audio", "AUDIO  (I2S amp, speaker, I2S mic)"),
    ("storage", "STORAGE  (microSD over SPI)"),
    ("ports", "PORTS  (keyed JST-PH, LED level shifter, I2C pull-ups)"),
    ("test", "FACTORY TEST PADS  (bottom side)"),
    ("mech", "MECHANICAL"),
]
PAPERS = [("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0)]


def g(v: float) -> float:
    """Snap to the 1.27 mm grid."""
    return round(round(v / GRID) * GRID, 4)


class Ids:
    def __init__(self, seed: str):
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, "gadget-studio/" + seed)
        self.n = 0

    def __call__(self, key: str | None = None) -> Q:
        self.n += 1
        return Q(str(uuid.uuid5(self.ns, key or f"auto-{self.n}")))


def _font(size=FONT, hide=False, justify=None, bold=False):
    f = ["font", ["size", size, size]]
    if bold:
        f.append(["bold", "yes"])
    e = ["effects", f]
    if justify:
        e.append(["justify", *justify])
    if hide:
        e.append(["hide", "yes"])
    return e


@dataclass
class Placed:
    part: Part
    sym: symbols.LibSymbol
    x: float
    y: float
    rot: int


def _sym_bbox(sym: symbols.LibSymbol) -> tuple[float, float, float, float]:
    """Sheet-frame bbox (rot 0) of graphics + pins: (xmin, ymin, xmax, ymax), Y down."""
    xs, ys = [], []
    for node in sexpr.walk(sym.node):
        h = sexpr.head(node)
        if h in ("start", "end", "xy", "center", "mid") and len(node) >= 3:
            try:
                xs.append(float(node[1]))
                ys.append(-float(node[2]))
            except (TypeError, ValueError):
                pass
    for p in sym.pins:
        xs.append(p.x)
        ys.append(-p.y)
    if not xs:
        return (-2.54, -2.54, 2.54, 2.54)
    return (min(xs), min(ys), max(xs), max(ys))


def _symbol_for(part: Part, gen: dict[str, symbols.LibSymbol]) -> symbols.LibSymbol:
    if part.symbol.startswith("gstudio:"):
        return gen[part.symbol]
    return symbols.get(part.symbol)


def _rot_for(part: Part) -> int:
    return 0


class Sheet:
    def __init__(self, design: Design, root_uuid: Q, ids: Ids, project: str):
        self.d = design
        self.ids = ids
        self.root = root_uuid
        self.project = project
        self.items: list = []
        self.lib_ids: dict[str, list] = {}
        self.pwr_n = 0
        self.flg_n = 0
        self.points: dict[tuple[float, float], str] = {}   # connection point -> net (sanity)

    # -------------------------------------------------------------- primitives
    def wire(self, x1, y1, x2, y2):
        self.items.append(["wire", ["pts", ["xy", x1, y1], ["xy", x2, y2]],
                           ["stroke", ["width", 0], ["type", "default"]], ["uuid", self.ids()]])

    def label(self, net: str, x: float, y: float, d: tuple[int, int]):
        ang, just = {(1, 0): (0, ["left", "bottom"]), (-1, 0): (180, ["right", "bottom"]),
                     (0, -1): (90, ["left", "bottom"]), (0, 1): (270, ["right", "bottom"])}[d]
        self.items.append(["label", Q(net), ["at", x, y, ang], ["fields_autoplaced", "yes"],
                           _font(justify=just), ["uuid", self.ids()]])

    def no_connect(self, x, y):
        self.items.append(["no_connect", ["at", x, y], ["uuid", self.ids()]])

    def text(self, s: str, x, y, size=FONT, bold=False):
        self.items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", x, y, 0],
                           _font(size, justify=["left", "top"], bold=bold), ["uuid", self.ids()]])

    def rect(self, x1, y1, x2, y2, dash=True):
        self.items.append(["rectangle", ["start", x1, y1], ["end", x2, y2],
                           ["stroke", ["width", 0.254 if not dash else 0.1524], ["type", "dash" if dash else "default"]],
                           ["fill", ["type", "none"]], ["uuid", self.ids()]])

    def _use_lib(self, sym: symbols.LibSymbol):
        if sym.lib_id not in self.lib_ids:
            self.lib_ids[sym.lib_id] = sym.node

    def symbol(self, sym: symbols.LibSymbol, ref: str, value: str, x: float, y: float, rot: int = 0,
               footprint: str = "", fields: dict | None = None, in_bom=True, on_board=True, ref_at=None,
               val_at=None, hide_ref=False, hide_val=False, dnp=False, val_angle=0, val_center=False):
        self._use_lib(sym)
        key = f"sym/{ref}"
        props = []
        bx0, by0, bx1, by1 = _sym_bbox(sym)
        rx, ry = ref_at or (x + bx0, y + by0 - 1.27)
        vx, vy = val_at or (x + bx0, y + by1 + 2.54)
        props.append(["property", Q("Reference"), Q(ref), ["at", g(rx), g(ry), 0],
                      _font(justify=["left"], hide=hide_ref)])
        props.append(["property", Q("Value"), Q(value), ["at", round(vx, 4), round(vy, 4), val_angle],
                      _font(justify=None if val_center else ["left"], hide=hide_val)])
        props.append(["property", Q("Footprint"), Q(footprint), ["at", x, y, 0], _font(hide=True)])
        ds = ""
        for node in sexpr.find_all(sym.node, "property"):
            if str(node[1]) == "Datasheet":
                ds = str(node[2])
        props.append(["property", Q("Datasheet"), Q(ds), ["at", x, y, 0], _font(hide=True)])
        desc = (fields or {}).get("description", "")
        props.append(["property", Q("Description"), Q(desc), ["at", x, y, 0], _font(hide=True)])
        for k, v in (fields or {}).items():
            if k == "description" or v in (None, ""):
                continue
            props.append(["property", Q(k.upper() if k in ("mpn", "lcsc") else k.title()), Q(str(v)),
                          ["at", x, y, 0], _font(hide=True)])
        pins = [["pin", Q(p.number), ["uuid", self.ids(f"{key}/pin/{p.number}")]]
                for p in sorted({p.number: p for p in sym.pins}.values(), key=lambda p: _natkey(p.number))]
        node = ["symbol", ["lib_id", Q(sym.lib_id)], ["at", x, y, rot], ["unit", 1],
                ["exclude_from_sim", "no"], ["in_bom", "yes" if in_bom else "no"],
                ["on_board", "yes" if on_board else "no"], ["dnp", "yes" if dnp else "no"],
                ["uuid", self.ids(key)], *props, *pins,
                ["instances", ["project", Q(self.project),
                               ["path", Q(f"/{self.root}"), ["reference", Q(ref)], ["unit", 1]]]]]
        self.items.append(node)

    def power(self, net: str, x: float, y: float, d: tuple[int, int]):
        """Power symbol at a stub end, its body pointing away from the pin along d (so neighbours never overlap)."""
        sym = symbols.get(POWER_SYMBOL[net])
        self.pwr_n += 1
        if net == "GND":
            rot = {(0, 1): 0, (1, 0): 90, (-1, 0): 270, (0, -1): 180}[d]
        else:
            rot = {(0, -1): 0, (-1, 0): 90, (1, 0): 270, (0, 1): 180}[d]
        L = len(net) * 1.05
        if d[0] != 0:
            vx, vy, fang = x + d[0] * (3.6 + L / 2), y, 90
        else:
            vx, vy, fang = x, y + d[1] * (4.2 if (net == "GND") == (d[1] > 0) else 3.6), 0
        self.symbol(sym, f"#PWR{self.pwr_n:03d}", net, x, y, rot, in_bom=False, on_board=True, hide_ref=True,
                    val_at=(vx, vy), val_angle=fang, val_center=True)

    def flag(self, net: str, x: float, y: float):
        """Power symbol at (x,y) wired to a PWR_FLAG 5.08 mm to the right."""
        self.power(net, x, y, (0, 1) if net == "GND" else (0, -1))
        self.wire(x, y, x + 5.08, y)
        self.flg_n += 1
        sym = symbols.get("power:PWR_FLAG")
        self.symbol(sym, f"#FLG{self.flg_n:02d}", "PWR_FLAG", x + 5.08, y, 0, in_bom=False, on_board=True,
                    hide_ref=True, val_at=(x + 5.08 - 3.81, y - 5.08))

    # -------------------------------------------------------------- one part with all its pin stubs
    def place_part(self, pl: Placed):
        p = pl.part
        self.symbol(pl.sym, p.ref, p.value, pl.x, pl.y, pl.rot, footprint=_fp_id(p), fields=p.bom,
                    in_bom=p.in_bom, on_board=p.on_board)
        seen: dict[tuple[float, float], str] = {}
        for pin in pl.sym.pins:
            dx, dy = symbols.transform(pin.x, pin.y, pl.rot)
            px, py = round(pl.x + dx, 4), round(pl.y + dy, 4)
            net = p.pads.get(pin.number, "")
            if (px, py) in seen:
                if seen[(px, py)] != net:
                    raise ValueError(f"{p.ref}: stacked pins at one point carry different nets "
                                     f"({seen[(px, py)]!r} vs {net!r})")
                continue
            seen[(px, py)] = net
            if not net:
                self.no_connect(px, py)
                continue
            d = symbols.pin_direction(pin, pl.rot)
            ex, ey = round(px + d[0] * STUB, 4), round(py + d[1] * STUB, 4)
            self.wire(px, py, ex, ey)
            if net in POWER_SYMBOL:
                self.power(net, ex, ey, d)
            else:
                self.label(net, ex, ey, d)


def _fp_id(p: Part) -> str:
    return p.footprint


# ----------------------------------------------------------------------------- block layout

def _cell_size(sym: symbols.LibSymbol, part: Part) -> tuple[float, float, float, float]:
    """Space a symbol needs incl. stubs + labels + ref/value text: (left, top, right, bottom) from its origin."""
    x0, y0, x1, y1 = _sym_bbox(sym)
    lab = 0.0
    for pin in sym.pins:
        net = part.pads.get(pin.number, "")
        if net:
            lab = max(lab, len(net) * 1.05 + (1.5 if net not in POWER_SYMBOL else 4.0))
    ext = STUB + max(lab, 4.0)
    left = -x0 + (ext if any(p.x <= x0 + 0.01 and abs(p.angle) < 1 for p in sym.pins) else 2.54)
    right = x1 + (ext if any(p.x >= x1 - 0.01 and abs(p.angle - 180) < 1 for p in sym.pins) else 2.54)
    top = -y0 + (STUB + 6.0 if any(abs(p.angle - 270) < 1 for p in sym.pins) else 2.54) + 2.54
    bottom = y1 + (STUB + 6.0 if any(abs(p.angle - 90) < 1 for p in sym.pins) else 2.54) + 3.81
    text_w = max(len(part.value), len(part.ref)) * 1.05 + 1.0
    right = max(right, x0 + text_w)
    return left, top, right, bottom


ORDER = {
    "power": ["J_PWR", "U2", "F1", "D1", "C1", "C2", "R1", "R2"],
    "core": ["J_U1A", "J_U1B"],
    "audio": ["J_A1", "J_A1P", "J_A1N", "R7", "C3", "J_SPK", "J_MIC"],
    "storage": ["J_S1"],
    "ports": ["J_I2C1", "J_I2C2", "J_I2C3", "R4", "R5", "J_DISP", "J_IO1", "J_IO2", "J_IO3", "J_LED", "U3", "R3", "C5",
              "C4", "J_SRV", "R6"],
    "test": ["TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7"],
    "mech": ["H1", "H2", "H3", "H4"],
}
BLOCK_W = {"power": 132.0, "core": 118.0, "audio": 138.0, "storage": 62.0, "ports": 205.0, "test": 112.0,
           "mech": 52.0}


def _layout_block(parts: list[Part], width: float, gen) -> tuple[list[tuple[Part, float, float]], float]:
    """Flow parts into rows inside a block of `width`; returns positions relative to block top-left + height."""
    out = []
    x, y, row_h = 6.0, 10.0, 0.0
    for p in parts:
        sym = _symbol_for(p, gen)
        l, t, r, b = _cell_size(sym, p)
        w, h = l + r, t + b
        if x + w > width - 4.0 and x > 6.0:
            x, y = 6.0, y + row_h + 2.54
            row_h = 0.0
        out.append((p, x + l, y + t))
        x += w + 2.54
        row_h = max(row_h, h)
    return out, y + row_h + 4.0


def write_schematic(design: Design, path: Path, gen_nodes: list[list], project: str = "backplane") -> dict:
    ids = Ids(design.design_id)
    root = ids("root")
    sh = Sheet(design, root, ids, project)
    gen = {str(n[1]): symbols.from_node(str(n[1]), n) for n in gen_nodes}

    by_block: dict[str, list[Part]] = {}
    for p in design.parts:
        if p.board_only:
            continue
        by_block.setdefault(p.block, []).append(p)
    for k, lst in by_block.items():
        order = ORDER.get(k, [])
        lst.sort(key=lambda p: (order.index(p.ref) if p.ref in order else 99, _natkey(p.ref)))

    # compute block sizes
    sizes = {}
    layouts = {}
    for key, _title in BLOCKS:
        parts = by_block.get(key, [])
        if not parts and key != "power":
            continue
        pos, h = _layout_block(parts, BLOCK_W[key], gen)
        if key == "power":
            h += 22.0  # room for PWR_FLAG row
        layouts[key], sizes[key] = pos, (BLOCK_W[key], h)

    margin, gap = 14.0, 5.08
    notes_h = 8.0 + 4.2 * len(design.notes)
    notes_w = max(len(n) for n in design.notes) * 1.05 + 8.0
    order = [k for k, _ in BLOCKS if k in sizes] + ["notes"]
    sizes["notes"] = (notes_w, notes_h)

    def pack(page_w: float):
        rows, cur, w = [], [], 0.0
        for k in order:
            bw = sizes[k][0]
            if cur and w + gap + bw > page_w - 2 * margin:
                rows.append(cur)
                cur, w = [], 0.0
            cur.append(k)
            w += (gap if len(cur) > 1 else 0.0) + bw
        if cur:
            rows.append(cur)
        heights = [max(sizes[k][1] for k in r) for r in rows]
        return rows, heights

    paper = PAPERS[-1]
    for cand in PAPERS:
        rows, heights = pack(cand[1])
        # keep the last row clear of the title block (about 110 x 40 mm, bottom right)
        need_h = sum(heights) + gap * (len(rows) - 1) + 2 * margin + 4.0
        last_w = sum(sizes[k][0] for k in rows[-1]) + gap * (len(rows[-1]) - 1)
        if need_h + (40.0 if last_w > cand[1] - 2 * margin - 115.0 else 0.0) <= cand[2]:
            paper = cand
            break
    rows, heights = pack(paper[1])

    y = margin
    for r, rh in zip(rows, heights):
        x = margin
        for key in r:
            bw, bh = sizes[key]
            bx, by = g(x), g(y)
            if key == "notes":
                sh.rect(bx, by, g(bx + bw), g(by + notes_h))
                sh.text("NOTES AND HONEST LIMITS", bx + 1.27, by + 1.27, 1.524, bold=True)
                for i, line in enumerate(design.notes):
                    sh.text(line, bx + 2.54, by + 6.35 + i * 4.2, FONT)
                x += bw + gap
                continue
            sh.rect(bx, by, g(bx + bw), g(by + rh))
            title = dict(BLOCKS)[key]
            sh.text(title, bx + 1.27, by + 1.27, 1.524, bold=True)
            for part, rx, ry in layouts[key]:
                sym = _symbol_for(part, gen)
                sh.place_part(Placed(part, sym, g(bx + rx), g(by + ry), _rot_for(part)))
            if key == "power":
                fx = bx + 8.89
                fy = by + rh - 10.16
                sh.text("Power flags (rail sources for ERC: USB-C VBUS, F1 output, devkit 3V3 regulator)",
                        bx + 1.27, fy - 8.89, FONT)
                for net in FLAG_NETS:
                    sh.flag(net, g(fx), g(fy))
                    fx += 22.86
            x += bw + gap
        y += rh + gap

    tb = ["title_block", ["title", Q(f"{design.name}: backplane ({design.backplane_id})")],
          ["date", Q(design.date)], ["rev", Q(design.revision)], ["company", Q("Gadget Studio")],
          ["comment", 1, Q(f"Engine {design.engine_version}; design id {design.design_id}")],
          ["comment", 2, Q("Generated programmatically. Digital checks only; no physical test made.")],
          ["comment", 3, Q(f"{len(design.used_slots)} slots populated; omitted: " + (", ".join(design.omitted_slots) or "none"))]]
    lib_symbols = ["lib_symbols", *[sexpr.deep_copy(n) for _, n in sorted(sh.lib_ids.items())]]
    doc = ["kicad_sch", ["version", SCH_VERSION], ["generator", Q("eeschema")], ["generator_version", Q("9.0")],
           ["uuid", root], ["paper", Q(paper[0])], tb, lib_symbols, *sh.items,
           ["sheet_instances", ["path", Q("/"), ["page", Q("1")]]], ["embedded_fonts", "no"]]
    path.write_text(sexpr.dumps(doc) + "\n")
    return {"paper": paper[0], "symbols": len([i for i in sh.items if i[0] == "symbol"])}


__all__ = ["write_schematic"]
