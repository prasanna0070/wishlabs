"""wiring.svg + wiring.png: a source-backed diagram of the backplane and every module cable.

Top half: the backplane in top view at true scale from backplane-v1.json (outline, holes, slot positions),
used ports highlighted, socket modules drawn from their CAD footprints, and each module's cable drawn
as a bundle of wires in their real colours from electrical.json to a module box listing its pins.
Bottom half: the pin table (module pin, wire colour, port pin, pin name, net, ESP32-S3 GPIO).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .data import ManualData
from .draw import Canvas
from .model import ROLE_NOUN, Assembly, role_of_ref
from .wiring import Harness, gpio_for, harnesses, slot_pin, socket_modules

INK = (27, 31, 36)
MUTED = (96, 101, 110)
PANEL = (255, 255, 255)
LINE = (228, 225, 216)
COBALT = (48, 70, 217)
COBALT_T = (229, 233, 251)
BOARD = (31, 94, 66)
BOARD_EDGE = (20, 64, 44)
GREY = (160, 160, 156)

WIRE_RGB = {
    "black": (30, 30, 30), "red": (205, 40, 40), "blue": (40, 95, 210), "yellow": (232, 190, 20),
    "green": (40, 150, 70), "white": (250, 250, 250), "orange": (240, 130, 30), "purple": (130, 70, 180),
    "violet": (130, 70, 180), "grey": (140, 140, 140), "gray": (140, 140, 140), "brown": (130, 80, 40),
    "pink": (235, 120, 170),
}


def wire_rgb(name: Optional[str]) -> tuple[int, int, int]:
    return WIRE_RGB.get(str(name or "").lower().strip(), (110, 110, 110))


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


def draw_wiring(data: ManualData, A: Assembly, out_dir: Path) -> dict:
    W = 1600
    bp = data.backplane
    hs = harnesses(data)
    socks = socket_modules(data)
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

    def box_h(h: Harness) -> float:
        return 78 + row_h * max(1, len(h.wires))

    n_max = max([len(r) for r in rows.values()] + [1])
    box_w = min(250.0, (W - 80 - (n_max - 1) * 18) / n_max)
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

    # ---------------------------------------------------------------- board
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
        room = (px1 - px0) - 8
        t1 = f"{inst['slot']}  {ROLE_NOUN.get(role, '')}"
        if cv.measure(t1, 14, "semibold") > room:
            t1 = inst["slot"]
        t2 = next((t for t in (f"{inst['ref']} sits here, plugged straight in", f"{inst['ref']}, plugged in", inst["ref"])
                   if cv.measure(t, 11) <= room), inst["ref"])
        cv.text((px0 + px1) / 2, (py0 + py1) / 2 - 2, t1, size=14, color=(236, 242, 238), weight="semibold", anchor="middle")
        cv.text((px0 + px1) / 2, (py0 + py1) / 2 + 16, t2, size=11, color=(200, 214, 205), anchor="middle")
    slot_px: dict[str, tuple[float, float, float, float]] = {}
    default_sizes = False
    if bp:
        for sid, sl in sorted(bp.slots.items()):
            if not sl.pos or sl.kind in ("socket", "testpad"):
                continue
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
            slot_px[sid] = (px0, py0, pw, ph)
            if used and sl.kind == "jst_ph":
                for k, pin in enumerate(sl.pins):
                    if pin.at:
                        qx, qy = bxy(*pin.at)
                        if k == 0:
                            cv.rect(qx - 3.4, qy - 3.4, 6.8, 6.8, fill=INK)
                        else:
                            cv.circle(qx, qy, 2.6, fill=INK)
            label = sid if sid != "J_PWR" else "J_PWR  USB-C power in"
            if not used:
                cv.text(px0 + pw / 2, py0 + ph / 2 + 4, sid, size=11, color=(196, 210, 200), anchor="middle")
            elif (sl.rect[3] if sl.rect else sl.pos[1]) > bh - 8:  # rear-edge port: label outside the board, in ink
                cv.text(px0 + pw / 2, by0 - 12, label, size=14, color=INK, weight="semibold", anchor="middle")
            else:
                cv.text(px0 + pw / 2, py0 - 8, label, size=14, color=(255, 255, 255), weight="semibold", anchor="middle")
    if not has_pos:
        cv.text(bx0 + bw * s / 2, by0 + bh * s / 2, "slot positions not available: see the table", size=16, color=(255, 255, 255), anchor="middle")

    # ---------------------------------------------------------------- module rows and ribbons
    def pin_key(w: dict):
        sp = str(w.get("slot_pin"))
        return (not sp.isdigit(), int(sp) if sp.isdigit() else 0, sp)

    def wire_layout(h: Harness):
        """(wires in pin order, pad points or None, asc) where asc = pin order runs left to right at the port."""
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
        # channel levels: leftward cables (box left of slot) by slot x ascending; rightward by slot x descending
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
            cv.text(tx, by + 24, f"{h.ref}  {noun}", size=17, weight="semibold")
            avail = (chip_x - tx - 6) if bus_right else (bx + box_w - tx - 10)
            nm = h.name
            while cv.measure(nm, 13) > avail and len(nm) > 8:
                nm = nm[:-2]
            cv.text(tx, by + 43, nm if nm == h.name else nm.rstrip() + "…", size=13, color=MUTED)
            cab = (h.cable or f"{n}-wire cable") + (f" {h.length_mm} mm" if h.length_mm and str(h.length_mm) not in (h.cable or "") else "") + f"  to {h.slot}"
            cv.text(tx, by + 62, cab, size=13, color=COBALT)
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
                    ry = by + 78 + row_of[j] * row_h + 10
                    col = wire_rgb(w.get("color"))
                    pts = [pads[j], (pads[j][0], edge + sign * 4), (x1, edge + sign * 16), (x1, yl), (x2, yl)]
                    pts += [(x2, ry), (chip_x + (10 if bus_right else 0), ry)]
                    if col == (250, 250, 250):
                        cv.polyline(pts, stroke=(120, 120, 120), sw=3.6)
                    cv.polyline(pts, stroke=col, sw=2.2)
            for j, w in enumerate(wires):
                ry = by + 78 + row_of[j] * row_h + 15
                col = wire_rgb(w.get("color"))
                cv.rect(chip_x, ry - 11, 10, 12, fill=col, stroke=(90, 90, 90), sw=0.8, r=2)
                info = f"{w.get('color') or ''}  pin {w.get('slot_pin')}"
                if bus_right:
                    cv.text(tx, ry, str(w.get("module_pin") or "?"), size=14, weight="mono")
                    cv.text(chip_x - 8, ry, info, size=12, color=MUTED, anchor="end")
                else:
                    cv.text(tx + 18, ry, str(w.get("module_pin") or "?"), size=14, weight="mono")
                    cv.text(bx + box_w - 12, ry, info, size=12, color=MUTED, anchor="end")
            bottom = max(bottom, by + hh) if where == "front" else bottom
        return bottom

    diag_bottom = max(by1 + 40, draw_row(rows["front"], "front"))
    draw_row(rows["rear"], "rear")

    # ---------------------------------------------------------------- table
    cols = [40, 340, 470, 600, 770, 930, 1180, 1380]
    heads = ["Module (ref)", "Module pin", "Wire", "Port, pin", "Pin name", "Net", "ESP32-S3 GPIO"]
    y = diag_bottom + 40
    cv.rect(30, y, W - 60, 40, fill=(236, 234, 228), r=4)
    for x, t in zip(cols, heads):
        cv.text(x, y + 27, t, size=16, weight="semibold")
    y += 44
    n_rows = 0
    for h in sorted(hs, key=lambda h: h.slot):
        cv.rect(30, y, W - 60, 30, fill=COBALT_T, r=3)
        cv.text(cols[0], y + 21, f"{h.ref}  {h.name}", size=15, weight="semibold")
        cv.text(cols[3], y + 21, f"{h.slot}  ({h.cable or 'cable'})", size=14, color=MUTED)
        y += 30
        for i, w in enumerate(sorted(h.wires, key=lambda w: w.get("slot_pin") or 0)):
            if i % 2:
                cv.rect(30, y, W - 60, 30, fill=(250, 249, 246))
            pin = slot_pin(data, h.slot, w.get("slot_pin"))
            net = w.get("net") or (pin.net if pin else None)
            g = gpio_for(data, net, pin)
            col = wire_rgb(w.get("color"))
            cv.text(cols[1], y + 21, str(w.get("module_pin") or "?"), size=15, weight="mono")
            cv.rect(cols[2], y + 9, 14, 13, fill=col, stroke=(90, 90, 90), sw=0.8, r=2)
            cv.text(cols[2] + 22, y + 21, str(w.get("color") or "not recorded"), size=15)
            cv.text(cols[3], y + 21, f"{h.slot} pin {w.get('slot_pin')}", size=15)
            cv.text(cols[4], y + 21, pin.name if pin else "not recorded", size=15, color=INK if pin else MUTED)
            cv.text(cols[5], y + 21, str(net or "not recorded"), size=15, weight="mono" if net else "regular", color=INK if net else MUTED)
            cv.text(cols[6], y + 21, f"GPIO{g}" if g is not None else "-", size=15, weight="mono" if g is not None else "regular")
            y += 30
            n_rows += 1

    # ---------------------------------------------------------------- notes and sources
    y += 34
    notes = [
        "Every JST-PH plug is keyed and fits one way. Push straight in; never pull a cable by its wires.",
        "Wire colours are a cross-check, not the identifier: read the port name on the board and pin 1 on the module.",
    ]
    if socks:
        notes.append(", ".join(f"{i['ref']} in {i['slot']}" for i in socks) + ": plug straight into their sockets, no cable.")
    buses = data.electrical.get("buses") or []
    spi = next((b for b in buses if isinstance(b, dict) and b.get("type") == "spi"), None)
    if spi and len(spi.get("devices") or []) > 1:
        notes.append("Shared SPI bus: " + ", ".join(str(d.get("ref")) for d in spi["devices"] if isinstance(d, dict)) + ".")
    notes.append("Board outline, holes, port positions" + (" and pads" if not default_sizes else "") + " are to scale"
                 + ("; connector bodies are drawn schematically" if default_sizes else "") + ". The black square marks pin 1 of each used port.")
    for t in notes:
        cv.text(40, y, t, size=17)
        y += 30
    y += 10
    src = [f"Sources: {bp.source or 'library/backplane-v1.json'} (board outline, port positions, pin names and GPIO numbers)" if bp else "Sources: backplane slot map not available",
           "and electrical.json (harness wires, colours and nets; the firmware's include/pins.h is generated from the same file).",
           "Diagram from saved data, not a photo, and not an electrical or safety test."]
    for t in src:
        cv.text(40, y, t, size=15, color=MUTED)
        y += 24
    cv.h = int(y + 24)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wiring.svg").write_text(cv.to_svg(), encoding="utf-8")
    cv.to_png(out_dir / "wiring.png")
    return {"harnesses": len(hs), "rows": n_rows, "height": cv.h, "slots_drawn": len(slot_px), "positions": has_pos}
