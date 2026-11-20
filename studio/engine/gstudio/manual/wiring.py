"""build.json `wiring` section: parts, connections and wiring steps, from electrical.json + backplane-v1.

Every connection is `<part>.<pin>` to `<part>.<slot>.<pin>` with its kind, cable, wire colour and both
the physical pin number and the logical name / ESP32-S3 GPIO in `note` (QB-17). `parts[].item` uses the
purchased name (build.json purchased[].name when the pipeline has written it, else the library name)
so the join to the purchase list holds (QB-18).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .data import Comp, ManualData, Pin, first, kebab
from .figs import TOP_34, PanelSpec, camera_hint
from . import words as W
from .model import ROLE_NOUN, Assembly, anchors_for, role_of_ref

PART_KIND = W.RoleTable({"core": "controller", "backplane": "board", "amp": "board", "sd": "board", "display": "board",
             "mic": "board", "led": "board", "sensor": "board", "speaker": "component", "button": "component",
             "buzzer": "component", "touch": "component", "power": "power"}, "part_kind")


@dataclass
class Harness:
    ref: str
    role: str
    slot: str
    cable: str
    length_mm: Optional[int]
    wires: list[dict]
    comp: Optional[Comp]
    feature: Optional[str]
    name: str


def slot_pin(data: ManualData, slot: str, n) -> Optional[Pin]:
    bp = data.backplane
    if not bp or slot not in bp.slots:
        return None
    return next((p for p in bp.slots[slot].pins if str(p.n) == str(n)), None)


def gpio_for(data: ManualData, net: Optional[str], pin: Optional[Pin], wire: Optional[dict] = None) -> Optional[int]:
    if wire and isinstance(wire.get("gpio"), int):
        return wire["gpio"]
    if pin and pin.gpio is not None:
        return pin.gpio
    if pin and pin.source_gpio is not None:
        return pin.source_gpio
    g = (data.electrical.get("gpio") or {})
    for key in (net, pin.net if pin else None, pin.name if pin else None):
        if key and key in g and isinstance(g[key], int):
            return g[key]
    paths = data.electrical.get("gpio_paths") or {}
    for key in (net, pin.net if pin else None):
        if key and isinstance(paths.get(key), dict) and isinstance(paths[key].get("gpio"), int):
            return paths[key]["gpio"]
    return None


def via_for(data: ManualData, net: Optional[str], pin: Optional[Pin], wire: Optional[dict] = None) -> Optional[str]:
    """The buffer between the GPIO and a pin, as its reference designator ("U3"), when the data names one."""
    txt = (wire or {}).get("via") or (pin.via if pin else None)
    if not txt:
        paths = data.electrical.get("gpio_paths") or {}
        for key in (net, pin.net if pin else None):
            if key and isinstance(paths.get(key), dict) and paths[key].get("via"):
                txt = paths[key]["via"]
                break
    if not txt:
        return None
    import re

    m = re.match(r"\s*([A-Z]{1,3}\d+)\b", str(txt))
    return m.group(1) if m else str(txt)


def gpio_text(data: ManualData, net: Optional[str], pin: Optional[Pin], wire: Optional[dict] = None) -> Optional[str]:
    """'GPIO8', or 'GPIO2 via U3' for a buffered net (MIN-30)."""
    g = gpio_for(data, net, pin, wire)
    if g is None:
        return None
    v = via_for(data, net, pin, wire)
    return f"GPIO{g}" + (f" via {v}" if v else "")


def harnesses(data: ManualData) -> list[Harness]:
    core_ref = (data.electrical.get("core") or {}).get("ref")
    hs = []
    for inst in data.instances:
        ref, slot = inst.get("ref"), inst.get("slot")
        h = inst.get("harness") or {}
        if not ref or not slot or ref == core_ref:
            continue
        kind = data.backplane.slots[slot].kind if data.backplane and slot in data.backplane.slots else ""
        if kind in ("socket", "power") or slot.upper() == "J_PWR":
            continue  # plugged straight into the board, or the board's own USB-C input: no cable
        if not h or not (h.get("wires") or []):
            continue
        comp = data.comp_for_ref(ref)
        role = W.row_role(data.lib.get(inst.get("module"))) or role_of_ref(ref) or str(inst.get("role") or "module")
        hs.append(Harness(ref=ref, role=role, slot=slot, cable=str(h.get("cable") or ""),
                          length_mm=h.get("length_mm"), wires=list(h.get("wires") or []), comp=comp,
                          feature=inst.get("feature"), name=data.module_name(comp or ref)))
    hs.sort(key=lambda x: x.slot)
    return hs


def socket_modules(data: ManualData) -> list[dict]:
    out = []
    for inst in data.instances:
        slot = inst.get("slot")
        if not slot or inst.get("harness"):
            continue
        kind = data.backplane.slots[slot].kind if data.backplane and slot in data.backplane.slots else ("socket" if slot in ("U1", "A1", "S1") else "")
        if kind == "socket":
            out.append(inst)
    out.sort(key=lambda i: (i.get("slot") != "U1", i.get("slot")))
    return out


def purchased_item(data: ManualData, module_id: Optional[str], fallback: str) -> str:
    for p in data.purchased():
        if module_id and p.get("module_id") == module_id and p.get("name"):
            return str(p["name"])
    return data.lib.name(module_id, fallback) if module_id else fallback


def wiring_parts(data: ManualData, A: Assembly) -> list[dict]:
    """One row per electrical instance: name and module_id from electrical.json, size from the component that carries
    that same library module_id (never an accessory sharing its ref, BLK-6)."""
    parts = []
    seen = set()
    bp = A.backplane
    core = data.electrical.get("core") or {}

    def add(pid: str, role: str, module_id: Optional[str], comp: Optional[Comp], name: str):
        if pid in seen:
            return
        seen.add(pid)
        item = purchased_item(data, module_id, name)
        row = {"id": pid, "name": name, "kind": PART_KIND.get(role, "board"), "item": item, "module_id": module_id}
        if comp and comp.bbox and (not module_id or comp.module_id == module_id):
            row["sizeMm"] = [round(v, 1) for v in comp.size]
        parts.append(row)

    def inst_name(inst: dict, mid: Optional[str]) -> str:
        nm = data.lib.name(mid) if mid else ""
        return nm if nm and nm != mid else str(inst.get("name") or inst.get("ref"))

    if core.get("ref"):
        c = data.comp_for_ref(core["ref"]) or A.core
        mid = core.get("module") or (c.module_id if c else None)
        add(core["ref"], "core", mid, c, inst_name(core, mid))
    if bp:
        inst = data.instance(bp.ref or "BP1")
        mid = inst.get("module") or bp.module_id
        add(bp.ref or "BP1", "backplane", mid, bp, inst_name(inst, mid) if inst else data.module_name(bp))
    for inst in data.instances:
        ref = inst.get("ref")
        if not ref or ref in seen:
            continue
        role = W.row_role(data.lib.get(inst.get("module"))) or role_of_ref(ref) or str(inst.get("role") or "")
        if role == "backplane":
            continue
        mid = inst.get("module")
        add(ref, role, mid, data.comp_for_ref(ref), inst_name(inst, mid))
    return parts


def cable_row(data: ManualData, ref: str) -> Optional[dict]:
    return next((p for p in data.purchased() if p.get("kind") == "cable" and ref in (p.get("refs") or [])), None)


def _conn_kind(via: str, ref: str, data: ManualData) -> str:
    v = (via or "").lower()
    if "socket" in v or "header" in v:
        return "socket"
    if "jst" in v or "cable" in v or "harness" in v:
        return "cable"
    return "wire"


def wiring_connections(data: ManualData, A: Assembly) -> list[dict]:
    conns: list[dict] = []
    bp_ref = (A.backplane.ref if A.backplane and A.backplane.ref else "BP1")
    raw = data.electrical.get("connections") or []
    covered = set()
    for c in raw:
        if not isinstance(c, dict) or not c.get("from") or not c.get("to"):
            continue
        frm, to = str(c["from"]), str(c["to"])
        ref = frm.split(".", 1)[0]
        covered.add(ref)
        parts = to.split(".")
        pin = slot_pin(data, parts[1], parts[2]) if len(parts) >= 3 else None
        via = c.get("via") or ""
        crow = cable_row(data, ref) if c.get("kind") == "cable" else None
        if crow and crow.get("name"):
            via = str(crow["name"])  # the exact purchased row, so the wiring joins the parts list (BLK-3)
        wire = {"gpio": c.get("gpio"), "via": c.get("through") or c.get("buffer")}
        row = _conn_row(data, frm, to, via, c.get("color"), c.get("net") or (pin.net if pin else None), pin,
                        parts[1] if len(parts) >= 2 else None, parts[2] if len(parts) >= 3 else None, wire=wire)
        if c.get("kind") in ("wire", "cable", "socket"):
            row["kind"] = c["kind"]
        if crow and crow.get("sku"):
            row["sku"] = crow["sku"]
        if crow and crow.get("module_id"):
            row["cable_id"] = crow["module_id"]
        conns.append(row)
    for h in harnesses(data):
        if h.ref in covered:
            continue
        row_p = cable_row(data, h.ref)
        via = str(row_p.get("name")) if row_p and row_p.get("name") else (h.cable or "JST-PH cable")
        for w in sorted(h.wires, key=lambda w: w.get("slot_pin") or 0):
            pin = slot_pin(data, h.slot, w.get("slot_pin"))
            frm = f"{h.ref}.{w.get('module_pin')}"
            to = f"{bp_ref}.{h.slot}.{w.get('slot_pin')}"
            r = _conn_row(data, frm, to, via, w.get("color"), w.get("net") or (pin.net if pin else None), pin, h.slot,
                          w.get("slot_pin"), wire=w)
            r["kind"] = "cable"
            if row_p and row_p.get("sku"):
                r["sku"] = row_p["sku"]
            if row_p and row_p.get("module_id"):
                r["cable_id"] = row_p["module_id"]
            conns.append(r)
    for inst in socket_modules(data):
        ref, slot = inst["ref"], inst["slot"]
        if ref in covered:
            continue
        sl = data.backplane.slots.get(slot) if data.backplane else None
        fp = (sl.label or sl.footprint) if sl is not None else None
        pins = list(sl.pins) if sl is not None else []
        if not pins:
            conns.append({"from": f"{ref}.header", "to": f"{bp_ref}.{slot}", "kind": "socket", "via": fp or "board socket",
                          "note": f"Plugs straight into socket {slot}; no cable."})
            continue
        for pin in pins:  # one row per socket pin (BLK-3 / QB-17): the module's pin name, the socket contact, net and GPIO
            mp = str(pin.n) if not str(pin.n).isdigit() else (pin.name or str(pin.n))
            r = _conn_row(data, f"{ref}.{mp}", f"{bp_ref}.{slot}.{pin.n}", fp or "board socket", None, pin.net, pin, slot, pin.n)
            r["kind"] = "socket"
            r["note"] = (r["note"] + "; " if r["note"] else "") + "plugs straight into the socket, no cable"
            conns.append(r)
    return conns


def _conn_row(data, frm, to, via, color, net, pin: Optional[Pin], slot, n, wire: Optional[dict] = None) -> dict:
    g = gpio_for(data, net, pin, wire)
    gt = gpio_text(data, net, pin, wire)
    bits = []
    if slot and n is not None:
        bits.append(f"{slot} pin {n}" + (f" = {pin.name}" if pin and pin.name else ""))
    if net:
        bits.append(f"net {net}")
    if gt is not None:
        bits.append(f"ESP32-S3 {gt}")
    row = {"from": frm, "to": to, "kind": _conn_kind(via, frm.split('.')[0], data), "via": via}
    if color:
        row["color"] = color
    if net:
        row["net"] = net
    if g is not None:
        row["gpio"] = g
    row["note"] = "; ".join(bits)
    return row


def wiring_steps(data: ManualData, A: Assembly, bboxes: dict) -> list[dict]:
    steps = []
    bp = A.backplane
    bp_ref = bp.ref if bp and bp.ref else "BP1"
    hs = harnesses(data)
    socks = socket_modules(data)
    used = sorted({h.slot for h in hs} | {i["slot"] for i in socks})
    unused = sorted(set(data.backplane.slots) - set(used) - {"J_PWR"}) if data.backplane else []
    unused = [s for s in unused if not s.upper().startswith("TP")]
    pop = data.populated_refs()
    if pop is not None:  # only ports that exist on the routed board (MIN-30)
        unused = [s for s in unused if s in pop]
    from .cables import module_ends

    ends = module_ends(data)
    cart = [c.name for c in A.cartridge]
    bp_anchors = anchors_for(A, bp_ref)

    def cam(names: list[str]):
        names = [n for n in names if n in bboxes]
        if not names:
            return None
        return camera_hint(PanelSpec(subtitle="", show={n: "focus" for n in names}, focus=names, view_dir=TOP_34), bboxes)

    text = (f"The backplane carries the core board in U1" + (", " + ", ".join(f"the {ROLE_NOUN.get(role_of_ref(i['ref']) or '', 'module')} in {i['slot']}" for i in socks if i['slot'] != 'U1') if socks else "")
            + " and the USB-C power input (J_PWR). Every plug-in module has its own keyed port. "
            + (f"This build uses {', '.join(h.slot for h in hs)}. " if hs else "")
            + (f"Ports {', '.join(unused)} stay empty. " if unused else "")
            + "Disconnect USB-C power before plugging or unplugging anything.")
    steps.append({"id": "meet-backplane", "title": "Meet the backplane", "text": text, "highlight": cart,
                  "labels": [k for k in bp_anchors if k.split(".", 1)[1] in used or "PWR" in k.upper()],
                  "camera": cam(cart), "visuals": [{"path": "manual/wiring.png",
                                                    "alt": "Top view of the backplane with the used ports highlighted and each module's cable drawn to its port, plus the pin table.",
                                                    "caption": "Diagram from the saved slot map and electrical data, not a photo. Check the port names printed on your board."}]})
    for inst in socks:
        ref, slot = inst["ref"], inst["slot"]
        c = data.comp_for_ref(ref) or (A.core if slot == "U1" else None)
        role = role_of_ref(ref) or "module"
        fp = data.backplane.slots[slot].footprint if data.backplane and slot in data.backplane.slots else None
        steps.append({"id": f"seat-{kebab(role)}", "title": f"Seat the {ROLE_NOUN.get(role, 'module')} in {slot}",
                      "text": f"{data.module_name(c or ref)} ({ref}) plugs straight into socket {slot}" + (f" ({fp})" if fp else "") + ". No cable.",
                      "highlight": [n for n in [c.name if c else None, bp.name if bp else None] if n],
                      "labels": [k for k in bp_anchors if k.split(".", 1)[1] == slot] + list(anchors_for(A, ref)),
                      "camera": cam([n for n in [c.name if c else None, bp.name if bp else None] if n])})
    for h in hs:
        row_p = cable_row(data, h.ref)
        if row_p and row_p.get("name"):
            h = Harness(**{**h.__dict__, "cable": str(row_p["name"])})
        wires = sorted(h.wires, key=lambda w: w.get("slot_pin") or 0)
        order = []
        for w in wires:
            pin = slot_pin(data, h.slot, w.get("slot_pin"))
            gt = gpio_text(data, w.get("net") or (pin.net if pin else None), pin, w)
            order.append(f"pin {w.get('slot_pin')} {w.get('color') or ''} {w.get('module_pin')}".replace("  ", " ")
                         + (f" ({gt})" if gt else ""))
        noun = ROLE_NOUN.get(h.role, h.role)
        route = next((r for r in data.routes if (r.to or "").startswith(h.ref + ".") or (r.frm or "").startswith(h.ref + ".")), None)
        cable_comps = [c.name for c in A.cables if c.name == (route.component if route else None) or (route and c.name == route.id)]
        labels = [k for k in ([route.frm, route.to] if route else []) if k and k in data.anchors]
        if not labels:
            labels = [k for k in data.anchors if k == f"{bp_ref}.{h.slot}" or k.startswith(h.ref + ".")]
        hl = [n for n in [h.comp.name if h.comp else None, bp.name if bp else None] if n] + cable_comps
        key = kebab(h.feature) if h.feature else kebab(h.ref)
        cable = h.cable or f"{len(wires)}-wire cable"
        steps.append({
            "id": f"wire-{key}", "title": f"Connect the {noun} to {h.slot}",
            "text": f"Plug the {cable} from the {h.name} ({h.ref}) into port {h.slot}. The plug is keyed; push it straight in until it seats."
                    + (f" From pin 1: " + "; ".join(order) + "." if order else "")
                    + (" At the module end: " + ends[h.ref].instruction(h.name).replace("**", "") if h.ref in ends else ""),
            "highlight": hl, "labels": labels, "camera": cam(hl),
        })
    pwr = [k for k in bp_anchors if "PWR" in k.upper()]
    steps.append({"id": "power-input", "title": "Power comes in at J_PWR",
                  "text": "A USB-C 5 V supply plugs into J_PWR at the rear edge of the backplane; the board feeds the core board from it. "
                          "Plug it in only after everything else is connected and the gadget is closed.",
                  "highlight": [bp.name] if bp else [], "labels": pwr, "camera": cam([bp.name] if bp else [])})
    return steps


def build_wiring(data: ManualData, A: Assembly, bboxes: dict) -> dict:
    return {
        "runtime": "esp32-arduino",
        "board": "esp32-s3-devkitc-1",
        "code": "firmware/src/main.cpp",
        "parts": wiring_parts(data, A),
        "connections": wiring_connections(data, A),
        "steps": wiring_steps(data, A, bboxes),
    }
