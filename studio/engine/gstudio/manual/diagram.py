"""wiring.svg + wiring.png: a source-backed diagram of the backplane and every module cable, and one cable-end inset
image per harness (manual/cable-end-<ref>.png).

Top half: the backplane in top view at true scale from backplane-v1.json (outline, holes, slot positions), only the
connectors populated on the routed board (pcb/backplane.kicad_pcb), socket modules drawn from their plan rectangles,
and each module's cable drawn as a bundle of wires in their real colours from electrical.json to a module box that
lists its pins and shows the module-end housing (pin-1 mark, positions, wire colours, "made to drawing" when the cable
crosses wires between its ends). Bottom half: the pin table (module pin, wire colour, port pin, pin name, net, GPIO).

Labels are placed after the wires, in free space, and `overlaps()` audits the finished drawing: no text box may
overlap another text box or be crossed by a wire (MIN-30).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .cables import FAMILY_WORDS, ModuleEnd, module_ends
from .data import ManualData
from .draw import Canvas
from .labels import Box
from .model import ROLE_NOUN, Assembly, role_of_ref
from .wiring import Harness, cable_row, gpio_text, harnesses, slot_pin, socket_modules

INK = (27, 31, 36)
MUTED = (96, 101, 110)
PANEL = (255, 255, 255)
LINE = (228, 225, 216)
COBALT = (48, 70, 217)
COBALT_T = (229, 233, 251)
BOARD = (31, 94, 66)
BOARD_EDGE = (20, 64, 44)
GREY = (160, 160, 156)
WARN = (176, 64, 32)

WIRE_RGB = {
    "black": (30, 30, 30), "red": (205, 40, 40), "blue": (40, 95, 210), "yellow": (232, 190, 20),
    "green": (40, 150, 70), "white": (250, 250, 250), "orange": (240, 130, 30), "purple": (130, 70, 180),
    "violet": (130, 70, 180), "grey": (140, 140, 140), "gray": (140, 140, 140), "brown": (130, 80, 40),
    "pink": (235, 120, 170),
}


def wire_rgb(name: Optional[str]) -> tuple[int, int, int]:
    return WIRE_RGB.get(str(name or "").lower().strip(), (110, 110, 110))


# --------------------------------------------------------------------------------------------- text geometry + audit
def text_box(cv: Canvas, op) -> Box:
    _, x, y, s, size, _color, weight, anchor = op
    w = cv.measure(s, size, weight)
    x0 = x if anchor == "start" else x - w / 2 if anchor == "middle" else x - w
    return Box(x0, y - 0.78 * size, x0 + w, y + 0.22 * size)


def _seg_hits_box(a, b, bx: Box) -> bool:
    """Liang-Barsky clip: does segment a-b pass through the box interior?"""
    x0, y0 = a
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - bx.x0), (dx, bx.x1 - x0), (-dy, y0 - bx.y0), (dy, bx.y1 - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def overlaps(cv: Canvas, shrink: float = 1.0) -> list[str]:
    """Every text box that overlaps another text box or is crossed by a wire or leader (MIN-30)."""
    texts = [(text_box(cv, op).inflate(-shrink), op[3]) for op in cv.ops if op[0] == "text" and str(op[3]).strip()]
    segs = []
    for op in cv.ops:
        if op[0] == "poly":
            segs += [(a, b, float(op[3])) for a, b in zip(op[1], op[1][1:])]
        elif op[0] == "line":
            segs.append(((op[1], op[2]), (op[3], op[4]), float(op[6])))
    out = []
    for i, (bi, si) in enumerate(texts):
        for bj, sj in texts[i + 1:]:
            if bi.intersects(bj):
                out.append(f"text '{si}' overlaps text '{sj}'")
        for a, b, sw in segs:
            if _seg_hits_box(a, b, bi.inflate(sw / 2 + shrink)):
                out.append(f"a wire crosses text '{si}'")
                break
    return out


def free_spot(cv: Canvas, cands: list[tuple[float, float, str]], s: str, size: int, weight: str = "semibold",
              keep_out: tuple = (), keep_in: Optional[Box] = None) -> Optional[tuple[float, float, str]]:
    """First candidate (x, y, anchor) where the text (plus a margin for wire strokes) touches no drawn text, wire or
    keep-out box, and stays inside `keep_in` when given."""
    for x, y, anchor in cands:
        bx = text_box(cv, ("text", x, y, s, size, None, weight, anchor)).inflate(4.0)
        if any(bx.intersects(k) for k in keep_out):
            continue
        if keep_in is not None and not bx.inflate(-2.0).inside(keep_in):
            continue
        clash = False
        for op in cv.ops:
            if op[0] == "text" and str(op[3]).strip() and text_box(cv, op).intersects(bx):
                clash = True
                break
            if op[0] == "poly" and any(_seg_hits_box(a, b, bx) for a, b in zip(op[1], op[1][1:])):
                clash = True
                break
            if op[0] == "line" and _seg_hits_box((op[1], op[2]), (op[3], op[4]), bx):
                clash = True
                break
        if not clash:
            return (x, y, anchor)
    return None


def wrap_px(cv: Canvas, s: str, width: float, size: int, weight: str = "regular", max_lines: int = 3) -> list[str]:
    """Wrap to a pixel width without ever cutting text short (lines may grow past max_lines' budget)."""
    words, lines, cur = str(s).split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if cur and cv.measure(t, size, weight) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _default_slot_size(slot) -> tuple[float, float]:
    n = len(slot.pins) or 2
    if slot.kind == "jst_ph":
        return ((n - 1) * 2.0 + 3.9, 4.5)  # schematic proportion only
    if slot.kind == "power":
        return (9.0, 7.5)
    if slot.kind == "socket":
        return (2.54 * n, 2.54)
    return (6.0, 4.0)


def _edge_of(sl, bw: float, bh: float) -> str:
    e = str(sl.edge or "").lower()
    if e in ("front", "rear"):
        return e
    if sl.pos:
        return "front" if sl.pos[1] < bh / 2 else "rear"
    return "front"


def _spread(centres: list[float], width: float, gap: float, lo: float, hi: float) -> list[float]:
    """1D placement of equal-width boxes near their ideal centres, in order, without overlap, inside [lo, hi]."""
    xs = []
    for c in centres:
        x = c - width / 2
        if xs and x < xs[-1] + width + gap:
            x = xs[-1] + width + gap
        xs.append(x)
    over = (xs[-1] + width) - hi if xs else 0
    if over > 0:
        xs = [x - over for x in xs]
        for i in range(len(xs) - 2, -1, -1):
            if xs[i] > xs[i + 1] - width - gap:
                xs[i] = xs[i + 1] - width - gap
    under = lo - xs[0] if xs else 0
    if under > 0:
        xs = [x + under for x in xs]
    return xs


# --------------------------------------------------------------------------------------------- module-end inset
def inset_height(me: Optional[ModuleEnd]) -> float:
    if me is None:
        return 0.0
    if me.mismatch:
        return 34 + 18 * 4
    if me.family == "factory_prewired":
        return 34 + 18 * 3
    if me.family in ("dupont_2.54", "jst_ph", "jst_xh") and me.positions:
        return 34 + 66 * max(1, len(me.housings)) + (40 if me.made_to_drawing else 0)
    return 34 + 22 * max(1, len(me.positions)) + (40 if me.made_to_drawing else 0)


def draw_inset(cv: Canvas, me: ModuleEnd, x: float, y: float, w: float, big: bool = False) -> float:
    """The module end of one cable: housing cells left to right from position 1 (▲), filled with the wire colour,
    the module pin under each cell. Returns the height used."""
    k = 1.6 if big else 1.0
    fs = int(13 * k)
    if me.mismatch:
        cv.text(x, y + 16 * k, "Module end: cable does not mate", size=fs, weight="semibold", color=WARN)
        for i, ln in enumerate(wrap_px(cv, me.mismatch, w, int(12 * k))):
            cv.text(x, y + (34 + 18 * i) * k, ln, size=int(12 * k), color=WARN)
        return (34 + 18 * len(wrap_px(cv, me.mismatch, w, int(12 * k)))) * k
    title = {"dupont_2.54": "Module end: Dupont 2.54 mm", "spade_2.8": "Module end: 2.8 mm spade receptacles",
             "factory_prewired": "Module end: pre-wired at the factory", "jst_ph": "Module end: JST-PH plug",
             "jst_xh": "Module end: JST-XH plug"}.get(me.family, "Module end: not recorded")
    cv.text(x, y + 16 * k, title, size=fs, weight="semibold", color=INK)
    yy = y + 30 * k
    if me.family in ("dupont_2.54", "jst_ph", "jst_xh") and me.positions:
        for h in me.housings:
            n = max(1, len(h.positions))
            cw = min(40.0 * k, (w - 70 * k) / n)
            hx = x + 56 * k
            cv.text(x, yy + 22 * k, (f"{chr(65 + me.housings.index(h))} 1x{n}" if len(me.housings) > 1 else f"1x{n}"), size=int(12 * k), color=MUTED)
            cv.rect(hx - 3, yy + 8 * k, cw * n + 6, 22 * k, fill=(40, 40, 44), r=3)
            for j, p in enumerate(h.positions):
                cx = hx + j * cw
                cv.rect(cx + 2, yy + 11 * k, cw - 4, 16 * k, fill=(70, 70, 74) if p.empty else wire_rgb(p.colour), stroke=(90, 90, 90), sw=0.8, r=2)
                if p.pos == 1:
                    cv.rect(cx + cw / 2 - 9 * k, yy - 3 * k, 6 * k, 6 * k, fill=INK)
                    cv.text(cx + cw / 2 - 1 * k, yy + 4 * k, "1", size=int(11 * k), weight="semibold", color=INK)
                cv.text(cx + cw / 2, yy + 46 * k, str(p.module_pin), size=int(11 * k), weight="mono", anchor="middle")
            yy += 64 * k
    else:
        rows = me.positions or []
        if me.family == "factory_prewired":
            lead = (me.factory or {}).get("lead")
            pads = (me.factory or {}).get("pads") or []
            for ln in wrap_px(cv, (f"{lead} lead" if lead else "lead") + (" on " + ", ".join(str(p) for p in pads) if pads else ""),
                              w, int(12 * k)):
                cv.text(x, yy + 14 * k, ln, size=int(12 * k), color=MUTED)
                yy += 18 * k
            yy += 4 * k
        else:
            for p in rows:
                cv.rect(x, yy + 3 * k, 12 * k, 12 * k, fill=wire_rgb(p.colour), stroke=(90, 90, 90), sw=0.8, r=2)
                cv.text(x + 20 * k, yy + 14 * k, f"{p.colour or 'wire'} on {p.module_pin}", size=int(12 * k))
                yy += 22 * k
    if me.made_to_drawing:
        for ln in wrap_px(cv, "made to drawing: wires cross between the ends", w, int(12 * k), "semibold"):
            cv.text(x, yy + 12 * k, ln, size=int(12 * k), weight="semibold", color=WARN)
            yy += 17 * k
        yy += 3 * k
    return yy - y


def draw_cable_end(data: ManualData, h: Harness, me: ModuleEnd, path: Path) -> Canvas:
    """manual/cable-end-<ref>.png: one harness's module end, large, with its position table (MAJ-10)."""
    W = 1400
    cv = Canvas(W, 1400)
    cv.rect(0, 0, W, 110, fill=PANEL)
    noun = ROLE_NOUN.get(h.role, h.role)
    cv.text(28, 52, f"Cable end at the {noun}: {h.ref} to port {h.slot}", size=31, weight="medium")
    sub = FAMILY_WORDS.get(me.family, "module-end termination not recorded")
    cv.text(28, 90, f"{h.name}: {sub}", size=19, color=MUTED)
    y = 140
    used = draw_inset(cv, me, 40, y, 760, big=True)
    y += used + 30
    cols = [40, 200, 480, 700, 900]
    for x, t in zip(cols, ["Housing position" if len(me.housings) > 1 else "Position", "Module pin", "Wire colour", "Port pin", "Net"]):
        cv.text(x, y, t, size=17, weight="semibold")
    y += 12
    cv.line(36, y, W - 40, y, stroke=LINE, sw=1.5)
    y += 30
    multi = len(me.housings) > 1
    rows = [(k, p) for k, hz in enumerate(me.housings) for p in hz.positions]
    for k, p in rows:
        w = next((w for w in h.wires if str(w.get("module_pin")) == p.module_pin), {})
        cv.text(cols[0], y, ((chr(65 + k) if multi else "") + str(p.pos)) if p.pos is not None else "-", size=16, weight="mono")
        cv.text(cols[1], y, p.module_pin, size=16, weight="mono")
        cv.rect(cols[2], y - 13, 14, 14, fill=wire_rgb(p.colour), stroke=(90, 90, 90), sw=0.8, r=2)
        cv.text(cols[2] + 22, y, str(p.colour or "not recorded"), size=16)
        cv.text(cols[3], y, f"{h.slot} pin {p.slot_pin}" if p.slot_pin is not None else "-", size=16)
        cv.text(cols[4], y, str(w.get("net") or "-"), size=16, weight="mono")
        y += 30
    y += 16
    notes = []
    if me.family == "dupont_2.54":
        notes.append("Position 1 is the cell marked with the black square and 1. Read the pin labels printed on the module before pushing the housing on.")
        notes.append("If the module has no header pins fitted, stop: this build has no soldering step.")
    if me.made_to_drawing:
        notes.append("This cable is made to drawing: its wires cross between the port end and the module end. Do not re-pin it.")
    if me.family == "factory_prewired":
        notes.append("The factory solders this lead before delivery; the maker never solders.")
    notes.append(f"Source: {me.source}. Drawn from saved data, not a photo.")
    for t in notes:
        for ln in wrap_px(cv, t, W - 80, 17):
            cv.text(40, y, ln, size=17, color=INK if "Source" not in t else MUTED)
            y += 26
    cv.h = int(y + 30)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv.to_png(path)
    return cv


# --------------------------------------------------------------------------------------------- wiring diagram
def draw_wiring(data: ManualData, A: Assembly, out_dir: Path) -> dict:
    W = 1600
    bp = data.backplane
    hs = harnesses(data)
    for i, h in enumerate(hs):  # the exact purchased cable name, as the parts list has it
        r = cable_row(data, h.ref)
        if r and r.get("name"):
            hs[i] = Harness(**{**h.__dict__, "cable": str(r["name"])})
    ends = module_ends(data)
    socks = socket_modules(data)
    pop = data.populated_refs()
    bw, bh = (bp.size if bp and bp.size else (92.0, 62.0))
    has_pos = bool(bp and any(s_.pos for s_ in bp.slots.values()))
    s = min(720.0 / bw, 540.0 / bh)
    SP = 3.4  # wire spacing in a ribbon (px)
    row_h = 23.0

    rows = {"front": [], "rear": []}
    for h in hs:
        sl = bp.slots.get(h.slot) if bp else None
        rows[_edge_of(sl, bw, bh) if sl else "front"].append(h)

    def slot_x(h: Harness) -> float:
        sl = bp.slots.get(h.slot) if bp else None
        return sl.pos[0] if sl and sl.pos else 0.0

    for k in rows:
        rows[k].sort(key=slot_x)
    probe = Canvas(W, 100)
    n_max = max([len(r) for r in rows.values()] + [1])
    box_w = min(270.0, (W - 80 - (n_max - 1) * 18) / n_max)

    def head_lines(h: Harness) -> tuple[list[str], list[str]]:
        avail = box_w - 28 - (len(h.wires) * SP + 12)
        return (wrap_px(probe, h.name, avail, 13), wrap_px(probe, f"{h.cable}, to {h.slot}", avail, 13))

    def box_h(h: Harness) -> float:
        nl, cl = head_lines(h)
        return 40 + 18 * (len(nl) + len(cl)) + 14 + row_h * max(1, len(h.wires)) + inset_height(ends.get(h.ref)) + 8

    max_wires = max([len(h.wires) for h in hs] + [1])
    chan_step = max_wires * SP + 12

    def chan_h(r: list) -> float:
        return 36 + chan_step * max(1, len(r))

    top_row_h = (max([box_h(h) for h in rows["rear"]] + [0]) + chan_h(rows["rear"]) + 20) if rows["rear"] else 0
    board_top = 150 + top_row_h + 40
    bx0 = (W - bw * s) / 2
    by0, by1 = board_top, board_top + bh * s

    def bxy(x: float, y: float) -> tuple[float, float]:
        return (bx0 + x * s, by1 - y * s)

    cv = Canvas(W, 4000)
    cv.rect(0, 0, W, 118, fill=PANEL)
    cv.text(40, 54, "Wiring: plug each module into its backplane port", size=34, weight="medium")
    cv.text(40, 94, "Top view of the backplane, component side up. Disconnect USB-C power before plugging or unplugging anything.", size=20, color=MUTED)

    # ---------------------------------------------------------------- board (shapes only; labels come after the wires)
    cv.rect(bx0 - 2, by0 - 2, bw * s + 4, bh * s + 4, fill=BOARD, stroke=BOARD_EDGE, sw=2, r=8)
    cv.text(bx0 - 14, by0 + 14, "rear (+Y)", size=15, color=MUTED, anchor="end")
    cv.text(bx0 - 14, by1 - 8, "front (-Y)", size=15, color=MUTED, anchor="end")
    cv.text(bx0 + bw * s + 14, by0 + 20, "+X: the gadget's left", size=15, color=MUTED)
    cv.text(bx0 + bw * s + 14, by0 + 42, "(your right, viewed from the front)", size=13, color=MUTED)
    for (hx, hy, hd) in (bp.holes if bp else []):
        cx, cy = bxy(hx, hy)
        cv.circle(cx, cy, max(4.0, hd / 2 * s), fill=(214, 200, 150), stroke=BOARD_EDGE, sw=1)
    origin = data.cartridge.get("board_origin") if data.cartridge else None
    used_slots = {h.slot for h in hs} | {i["slot"] for i in socks} | {"J_PWR"}
    regions = []  # (px0, py0, px1, py1, inst) for the socket modules' labels
    for inst in socks:
        c = data.comp_for_ref(inst["ref"])
        sl = bp.slots.get(inst["slot"]) if bp else None
        if sl and sl.rect:
            x0, x1, y0, y1 = sl.rect
        elif c and c.bbox and origin:
            x0, x1, y0, y1 = c.bbox[0] - origin[0], c.bbox[1] - origin[0], c.bbox[2] - origin[1], c.bbox[3] - origin[1]
            x0, x1 = max(x0, -6.0), min(x1, bw + 6.0)
        elif sl and sl.pos:
            sw_, sh_ = sl.size or _default_slot_size(sl)
            x0, x1, y0, y1 = sl.pos[0] - sw_ / 2, sl.pos[0] + sw_ / 2, sl.pos[1] - sh_ / 2, sl.pos[1] + sh_ / 2
        else:
            continue
        px0, py0 = bxy(x0, y1)
        px1, py1 = bxy(x1, y0)
        role = role_of_ref(inst["ref"]) or ""
        cv.rect(px0, py0, px1 - px0, py1 - py0, fill=(58, 92, 78) if role != "core" else (44, 74, 62), stroke=(200, 214, 205), sw=1.2, r=3, dash=(6, 4))
        regions.append((px0, py0, px1, py1, inst))
    slot_px: dict[str, tuple[float, float, float, float]] = {}
    port_labels = []
    default_sizes = False
    drawn_ports = []
    if bp:
        for sid, sl in sorted(bp.slots.items()):
            if not sl.pos or sl.kind in ("socket", "testpad"):
                continue
            if pop is not None and sid not in pop and sid not in used_slots:
                continue  # not on the routed board: do not draw it (MIN-30)
            if sl.rect:
                rx0, rx1, ry0, ry1 = sl.rect
            else:
                sw_, sh_ = sl.size or _default_slot_size(sl)
                default_sizes = True
                if int(round(sl.rot)) % 180 == 90:
                    sw_, sh_ = sh_, sw_
                rx0, rx1, ry0, ry1 = sl.pos[0] - sw_ / 2, sl.pos[0] + sw_ / 2, sl.pos[1] - sh_ / 2, sl.pos[1] + sh_ / 2
            px0, py0 = bxy(rx0, ry1)
            pw, ph = (rx1 - rx0) * s, (ry1 - ry0) * s
            used = sid in used_slots
            if used:
                cv.rect(px0, py0, pw, ph, fill=COBALT_T, stroke=COBALT, sw=2.5, r=2)
            else:
                cv.rect(px0, py0, pw, ph, fill=(60, 110, 85), stroke=(200, 210, 200), sw=1.2, r=2, dash=(4, 3))
            drawn_ports.append(sid)
            slot_px[sid] = (px0, py0, pw, ph)
            if used and sl.kind == "jst_ph":
                for k, pin in enumerate(sl.pins):
                    if pin.at:
                        qx, qy = bxy(*pin.at)
                        if k == 0:
                            cv.rect(qx - 3.4, qy - 3.4, 6.8, 6.8, fill=INK)
                        else:
                            cv.circle(qx, qy, 2.6, fill=INK)
            port_labels.append((sid, sl, used))
    if not has_pos:
        cv.text(bx0 + bw * s / 2, by0 + bh * s / 2, "slot positions not available: see the table", size=16, color=(255, 255, 255), anchor="middle")

    # ---------------------------------------------------------------- module rows and ribbons
    def pin_key(w: dict):
        sp = str(w.get("slot_pin"))
        return (not sp.isdigit(), int(sp) if sp.isdigit() else 0, sp)

    def wire_layout(h: Harness):
        wires = sorted(h.wires, key=pin_key)
        pads = []
        for w in wires:
            pin = slot_pin(data, h.slot, w.get("slot_pin"))
            pads.append(bxy(*pin.at) if pin and pin.at else None)
        if not (h.slot in slot_px and pads and all(pads)):
            return wires, None, False
        n_ = len(wires)
        order = sorted(range(n_), key=lambda j: pads[j][0])
        return wires, pads, n_ > 1 and order == list(range(n_))

    boxes: list[Box] = []

    def draw_row(hlist: list[Harness], where: str) -> float:
        if not hlist:
            return by1
        sign = 1 if where == "front" else -1
        edge_y = by1 if where == "front" else by0
        chan0 = edge_y + sign * 34
        centres = []
        for h in hlist:
            if h.slot in slot_px:
                px0, py0, pw, ph = slot_px[h.slot]
                centres.append(px0 + pw / 2)
            else:
                centres.append(W / 2)
        xs = _spread(centres, box_w, 18, 40, W - 40)
        box_top = chan0 + sign * (chan_step * len(hlist) + 24) if where == "front" else None

        def bus_centre(i: int, h: Harness) -> float:
            n_ = max(1, len(h.wires))
            _, _, asc_ = wire_layout(h)
            return (xs[i] + box_w - 14 - (n_ - 1) * SP / 2) if asc_ else (xs[i] + 14 + (n_ - 1) * SP / 2)

        levels = {}
        left = [i for i in range(len(hlist)) if bus_centre(i, hlist[i]) < centres[i] - 0.5]
        right = [i for i in range(len(hlist)) if i not in left]
        for k, i in enumerate(left):
            levels[i] = k
        for k, i in enumerate(sorted(right, key=lambda i: -centres[i])):
            levels[i] = k
        bottom = edge_y
        for i, h in enumerate(hlist):
            wires, pads0, _asc = wire_layout(h)
            n = max(1, len(wires))
            hh = box_h(h)
            bx = xs[i]
            by = box_top if where == "front" else chan0 + sign * (chan_step * len(hlist) + 24) - hh
            cv.rect(bx, by, box_w, hh, fill=PANEL, stroke=(200, 196, 186), sw=1.2, r=8)
            boxes.append(Box(bx, by, bx + box_w, by + hh))
            pads = list(pads0) if pads0 else [None] * n
            if not pads0 and h.slot in slot_px:
                px0, py0, pw, ph = slot_px[h.slot]
                pads = [(px0 + pw / 2 + ((n - 1) / 2 - j) * SP, py0 + ph / 2) for j in range(n)]
            order = sorted(range(n), key=lambda j: pads[j][0]) if pads and all(pads) else list(range(n))[::-1]
            rank = {j: r for r, j in enumerate(order)}
            asc = all(rank[j] == j for j in range(n))
            bus_right = asc and n > 1
            row_of = {j: (rank[j] if bus_right else n - 1 - rank[j]) for j in range(n)}
            noun = ROLE_NOUN.get(h.role, h.role)
            if bus_right:
                bus_x = bx + box_w - 14 - (n - 1) * SP
                tx = bx + 14
                chip_x = bus_x - 22
            else:
                bus_x = bx + 14
                tx = bus_x + n * SP + 10
                chip_x = tx
            nl, cl = head_lines(h)
            cv.text(tx, by + 24, f"{h.ref}  {noun}", size=17, weight="semibold")
            yy = by + 24
            for ln in nl:
                yy += 18
                cv.text(tx, yy, ln, size=13, color=MUTED)
            for ln in cl:
                yy += 18
                cv.text(tx, yy, ln, size=13, color=COBALT)
            rows_top = yy + 14
            if h.slot in slot_px:
                px0, py0, pw, ph = slot_px[h.slot]
                sx0 = px0 + pw / 2 - (n - 1) * SP / 2
                edge = py0 + ph if where == "front" else py0
                lvl = chan0 + sign * levels[i] * chan_step
                leftward = i in left
                for j, w in enumerate(wires):
                    r = rank[j]
                    q = r if leftward else (n - 1 - r)
                    x1 = sx0 + r * SP
                    yl = lvl + sign * q * SP
                    x2 = bus_x + r * SP
                    ry = rows_top + row_of[j] * row_h + 10
                    col = wire_rgb(w.get("color"))
                    pts = [pads[j], (pads[j][0], edge + sign * 4), (x1, edge + sign * 16), (x1, yl), (x2, yl)]
                    pts += [(x2, ry), (chip_x + (10 if bus_right else 0), ry)]
                    if col == (250, 250, 250):
                        cv.polyline(pts, stroke=(120, 120, 120), sw=3.6)
                    cv.polyline(pts, stroke=col, sw=2.2)
            for j, w in enumerate(wires):
                ry = rows_top + row_of[j] * row_h + 15
                col = wire_rgb(w.get("color"))
                cv.rect(chip_x, ry - 11, 10, 12, fill=col, stroke=(90, 90, 90), sw=0.8, r=2)
                info = f"{w.get('color') or ''}  pin {w.get('slot_pin')}"
                if bus_right:
                    cv.text(tx, ry, str(w.get("module_pin") or "?"), size=14, weight="mono")
                    cv.text(chip_x - 8, ry, info, size=12, color=MUTED, anchor="end")
                else:
                    cv.text(tx + 18, ry, str(w.get("module_pin") or "?"), size=14, weight="mono")
                    cv.text(bx + box_w - 12, ry, info, size=12, color=MUTED, anchor="end")
            me = ends.get(h.ref)
            if me is not None:
                iy = rows_top + n * row_h + 6
                cv.line(bx + 10, iy, bx + box_w - 10, iy, stroke=LINE, sw=1.0)
                draw_inset(cv, me, bx + 14, iy + 2, box_w - 28)
            bottom = max(bottom, by + hh) if where == "front" else bottom
        return bottom

    diag_bottom = max(by1 + 40, draw_row(rows["front"], "front"))
    draw_row(rows["rear"], "rear")

    # ---------------------------------------------------------------- labels in free space (after the wires)
    for (px0, py0, px1, py1, inst) in regions:
        role = role_of_ref(inst["ref"]) or ""
        t1s = [f"{inst['slot']}  {ROLE_NOUN.get(role, '')}".strip(), inst["slot"]]
        cx = (px0 + px1) / 2
        xs_try = [cx] + [px0 + (px1 - px0) * f for f in (0.25, 0.75, 0.15, 0.85)]
        ys_try = [(py0 + py1) / 2 + d for d in (0, -14, 14, -28, 28, -42, 42)]
        placed = None
        for t1 in t1s:
            if cv.measure(t1, 14, "semibold") > (px1 - px0) - 8:
                continue
            placed = free_spot(cv, [(x, y, "middle") for y in ys_try for x in xs_try], t1, 14,
                               keep_out=tuple(boxes), keep_in=Box(px0, py0, px1, py1))
            if placed:
                cv.text(placed[0], placed[1], t1, size=14, color=(236, 242, 238), weight="semibold", anchor="middle")
                t2 = next((t for t in (f"{inst['ref']} plugs straight in", inst["ref"]) if cv.measure(t, 11) <= (px1 - px0) - 8), None)
                if t2:
                    p2 = free_spot(cv, [(placed[0], placed[1] + 17, "middle"), (placed[0], placed[1] - 19, "middle")], t2, 11, "regular",
                                   keep_in=Box(px0, py0, px1, py1))
                    if p2:
                        cv.text(p2[0], p2[1], t2, size=11, color=(200, 214, 205), anchor="middle")
                break
    for sid, sl, used in port_labels:
        px0, py0, pw, ph = slot_px[sid]
        label = sid if sid != "J_PWR" else "J_PWR  USB-C power in"
        size = 14 if used else 11
        col = (255, 255, 255) if used else (196, 210, 200)
        cands = []
        if (sl.rect[3] if sl.rect else sl.pos[1]) > bh - 8 and used:  # rear-edge port: outside the board, in ink
            cands.append((px0 + pw / 2, by0 - 12, "middle"))
            col = INK
        if not used:
            cands.append((px0 + pw / 2, py0 + ph / 2 + 4, "middle"))
        cands += [(px0 + pw / 2, py0 - 8, "middle"), (px0 - 6, py0 + ph / 2 + 5, "end"), (px0 + pw + 6, py0 + ph / 2 + 5, "start"),
                  (px0 + pw / 2, py0 + ph + 17, "middle"), (px0 - 6, py0 - 6, "end"), (px0 + pw + 6, py0 - 6, "start"),
                  (px0 - 6, py0 + ph + 16, "end"), (px0 + pw + 6, py0 + ph + 16, "start")]
        spot = free_spot(cv, cands, label, size, "semibold" if used else "regular", keep_out=tuple(boxes))
        if spot is None:
            spot = cands[0]
        if spot[1] < by0 - 2 or spot[1] > by1 + 2 or spot[0] < bx0 or spot[0] > bx0 + bw * s:
            col = INK
        cv.text(spot[0], spot[1], label, size=size, color=col, weight="semibold" if used else "regular", anchor=spot[2])

    # ---------------------------------------------------------------- table
    cols = [40, 360, 480, 620, 790, 940, 1180, 1380]
    heads = ["Module (ref)", "Module pin", "Wire", "Port, pin", "Pin name", "Net", "ESP32-S3 GPIO"]
    y = diag_bottom + 40
    cv.rect(30, y, W - 60, 40, fill=(236, 234, 228), r=4)
    for x, t in zip(cols, heads):
        cv.text(x, y + 27, t, size=16, weight="semibold")
    y += 44
    n_rows = 0
    for h in sorted(hs, key=lambda h: h.slot):
        head = wrap_px(cv, f"{h.ref}  {h.name}", W - 120, 15, "semibold")
        hh = 24 * len(head) + 30
        cv.rect(30, y, W - 60, hh, fill=COBALT_T, r=3)
        for k, ln in enumerate(head):
            cv.text(cols[0], y + 21 + 24 * k, ln, size=15, weight="semibold")
        cv.text(cols[0], y + 21 + 24 * len(head), f"to port {h.slot}: {h.cable or 'cable'}", size=13, color=MUTED)
        y += hh
        for i, w in enumerate(sorted(h.wires, key=lambda w: w.get("slot_pin") or 0)):
            if i % 2:
                cv.rect(30, y, W - 60, 30, fill=(250, 249, 246))
            pin = slot_pin(data, h.slot, w.get("slot_pin"))
            net = w.get("net") or (pin.net if pin else None)
            gt = gpio_text(data, net, pin, w)
            col = wire_rgb(w.get("color"))
            cv.text(cols[1], y + 21, str(w.get("module_pin") or "?"), size=15, weight="mono")
            cv.rect(cols[2], y + 9, 14, 13, fill=col, stroke=(90, 90, 90), sw=0.8, r=2)
            cv.text(cols[2] + 22, y + 21, str(w.get("color") or "not recorded"), size=15)
            cv.text(cols[3], y + 21, f"{h.slot} pin {w.get('slot_pin')}", size=15)
            cv.text(cols[4], y + 21, pin.name if pin else "not recorded", size=15, color=INK if pin else MUTED)
            cv.text(cols[5], y + 21, str(net or "not recorded"), size=15, weight="mono" if net else "regular", color=INK if net else MUTED)
            cv.text(cols[6], y + 21, gt or "-", size=15, weight="mono" if gt else "regular")
            y += 30
            n_rows += 1

    # ---------------------------------------------------------------- notes and sources
    y += 34
    from .cables import lookalike_ports

    look = lookalike_ports(data, sorted({h.slot for h in hs}))
    notes = ["Every port plug is keyed, so it only goes in one way round. Push straight in; never pull a cable by its wires."]
    for gp in look:
        notes.append(f"Ports {', '.join(gp)} share connector and pin count, so their leads fit either: read the port name before plugging.")
    notes.append("Wire colours are a cross-check, not the identifier: read the port name on the board and pin 1 on the module.")
    if any(me.made_to_drawing for me in ends.values()):
        notes.append("A cable marked \"made to drawing\" crosses wires between its two ends; its module end follows the module's pin order.")
    if socks:
        notes.append(", ".join(f"{i['ref']} in {i['slot']}" for i in socks) + ": plug straight into their sockets, no cable.")
    buses = data.electrical.get("buses") or []
    spi = next((b for b in buses if isinstance(b, dict) and b.get("type") == "spi"), None)
    if spi and len(spi.get("devices") or []) > 1:
        notes.append("Shared SPI bus: " + ", ".join(str(d.get("ref")) for d in spi["devices"] if isinstance(d, dict)) + ".")
    notes.append("Board outline, holes, port positions" + (" and pads" if not default_sizes else "") + " are to scale"
                 + ("; connector bodies are drawn schematically" if default_sizes else "") + ". The black square marks pin 1 of each used port."
                 + (" Only connectors fitted on the routed board are drawn." if pop is not None else ""))
    for t in notes:
        for ln in wrap_px(cv, t, W - 80, 17):
            cv.text(40, y, ln, size=17)
            y += 28
    y += 10
    src = [f"Sources: {bp.source or 'library/backplane-v1.json'} (board outline, port positions, pin names and GPIO numbers)" if bp else "Sources: backplane slot data not available",
           "and electrical.json (harness wires, colours, nets and module-end housings; the firmware's include/pins.h is generated from the same file)"
           + ("; pcb/backplane.kicad_pcb (which connectors are fitted)." if pop is not None else "."),
           "Diagram from saved data, not a photo, and not an electrical or safety test."]
    for t in src:
        for ln in wrap_px(cv, t, W - 80, 15):
            cv.text(40, y, ln, size=15, color=MUTED)
            y += 24
    cv.h = int(y + 24)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wiring.svg").write_text(cv.to_svg(), encoding="utf-8")
    cv.to_png(out_dir / "wiring.png")
    canvases = {"manual/wiring.png": cv}
    for h in hs:
        me = ends.get(h.ref)
        if me is not None:
            rel = f"manual/cable-end-{h.ref.lower()}.png"
            canvases[rel] = draw_cable_end(data, h, me, out_dir / f"cable-end-{h.ref.lower()}.png")
    ov = overlaps(cv)
    return {"harnesses": len(hs), "rows": n_rows, "height": cv.h, "slots_drawn": len(slot_px), "positions": has_pos,
            "ports_drawn": drawn_ports, "overlaps": ov, "canvases": canvases,
            "module_ends": {r: {"family": me.family, "made_to_drawing": me.made_to_drawing, "source": me.source,
                                "positions": [[p.pos, p.module_pin, p.colour] for p in me.positions]} for r, me in ends.items()}}


def ports_pin_count(data: ManualData, slot: str) -> Optional[int]:
    """Pin count of a port from the slot data (MIN-30: never from the wires used)."""
    bp = data.backplane
    if bp and slot in bp.slots:
        n = len(bp.slots[slot].pins) or bp.slots[slot].raw.get("pins_count")
        return int(n) if n else None
    return None
