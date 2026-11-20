"""Electrical stage (ARCHITECTURE §7): slot allocation, harness wires, nets, buses, GPIO table, power budget.

    from gstudio.electrical import run, build_electrical, assign_slots
    ctx.electrical = run(ctx)             # writes <out>/electrical.json (gadget-electrical/1) and returns it
    build_electrical(spec, lib) -> dict   # pure function, no files (used by tests and /validate)
    assign_slots(instances, lib) -> (mapping ref->slot, errors)   # used by gstudio.normalize

Inputs: a normalized Spec (spec.derived filled by gstudio.normalize), the Library (modules.json + backplane-v1.json).
Every wire maps a module pin to a backplane slot pin by name (slot pin `accepts` aliases), never by position.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from .errors import StageError
from .version import BACKPLANE_ID, ELECTRICAL_SCHEMA, ENGINE_VERSION

if TYPE_CHECKING:
    from .context import BuildContext
    from .library import Library
    from .spec import Spec

# wire colours: black GND, red 5V, orange 3V3, then signals in pin order
SIGNAL_COLOURS = ["yellow", "green", "blue", "white", "purple", "grey", "brown"]
POWER_COLOUR = {"GND": "black", "+5V": "red", "+3V3": "orange"}
# fixed colour per signal function so the same signal is the same colour in every product
FIXED = {"I2C_SDA": "blue", "I2C_SCL": "yellow", "SPI_SCK": "yellow", "SPI_MOSI": "blue", "DISP_CS": "white", "DISP_DC": "green",
         "DISP_RST": "purple", "DISP_BL": "grey", "I2S_MIC_SCK": "yellow", "I2S_MIC_WS": "green", "I2S_MIC_SD": "blue",
         "LED_DIN_5V": "green", "IO1_SIG": "white", "IO2_SIG": "white", "IO3_SIG": "white",
         # speaker leads are amplifier outputs (BTL), never ground: no black, no red
         "SPK_P": "white", "SPK_N": "grey"}

PORT_ORDER = {"i2c": ["J_I2C1", "J_I2C2", "J_I2C3"], "io": ["J_IO1", "J_IO2", "J_IO3"]}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9/+\-]", "", str(s).upper())


# ------------------------------------------------------------------------------------------- slot allocation
def assign_slots(instances: list[dict], lib: "Library") -> tuple[dict[str, str], list[dict]]:
    """Give every instance a backplane slot from its module's backplane_v1.slots. Returns (ref -> slot, errors).
    Shared-capacity slots (3 x I2C, 3 x IO) fill in order; everything else holds one instance."""
    used: dict[str, str] = {}
    out: dict[str, str] = {}
    errs: list[dict] = []
    for inst in instances:
        ref, mid = inst["ref"], inst["module"]
        if inst.get("source") == "core":
            out[ref] = "U1"
            continue
        if inst.get("role") in ("backplane",):
            continue
        m = lib.get(mid)
        pv1 = m.get("backplane_v1") or {}
        slots = list(pv1.get("slots") or [])
        if not pv1.get("supported"):
            errs.append({"path": inst.get("path", "features"), "code": "unsupported_module",
                         "msg": f"{mid} cannot be connected on backplane-v1: {pv1.get('why', 'no port')}",
                         "fix": "choose another module of the same role from the library table"})
            continue
        if not slots:
            continue
        free = [s for s in slots if s not in used]
        if not free:
            holders = ", ".join(f"{used[s]} on {s}" for s in slots)
            errs.append({"path": inst.get("path", "features"), "code": "slot_full",
                         "msg": f"no free port for {ref} ({mid}): {holders}",
                         "fix": f"remove one of them; backplane-v1 has {len(slots)} port(s) of this kind"})
            continue
        used[free[0]] = ref
        out[ref] = free[0]
    return out, errs


# ------------------------------------------------------------------------------------------- helpers
def _slot_pin_for(module_pin: str, slot: dict, taken: set) -> Optional[dict]:
    mp = _norm(module_pin)
    for p in slot["pins"]:
        if p["pin"] in taken:
            continue
        names = [_norm(p["name"])] + [_norm(a) for a in p.get("accepts", [])]
        if mp in names:
            return p
    return None


def _power_net(slot: dict) -> Optional[str]:
    for p in slot["pins"]:
        if p.get("kind") == "power" and p["net"] in ("+3V3", "+5V"):
            return p["net"]
    return None


def _cable_label(h: Optional[dict]) -> str:
    if not h:
        return ""
    return h.get("cable") or ""


def _harness_for(spec: "Spec", ref: str) -> Optional[dict]:
    d = spec.derived
    if not d:
        return None
    for h in d.harness:
        if h.ref == ref:
            return h.model_dump()
    return None


def harness_wires(m: dict, slot: dict) -> list[dict]:
    """Module pin -> slot pin wires for a cabled module, by name (slot pin `accepts` aliases), never by position."""
    mod_pins = [p.get("name") for p in (m.get("pins") or []) if p.get("name")]
    order = (m.get("module_connector") or {}).get("pin_order") or mod_pins
    # wires listed by the library for this module (subset actually used), else every module pin we can map
    lib_wires = [w.get("module_pin") for w in (m.get("backplane_harness") or {}).get("wires", []) if w.get("module_pin")]
    candidates = lib_wires or order
    wires: list[dict] = []
    taken: set = set()
    sig_i = 0
    for mp in candidates:
        sp = _slot_pin_for(mp, slot, taken)
        if sp is None:
            continue
        taken.add(sp["pin"])
        net = sp.get("net")
        if sp.get("kind") == "power" or net == "GND":
            colour = POWER_COLOUR.get(net, "red")
        elif sp.get("kind") == "strap":
            colour = "black"
        else:
            colour = FIXED.get(net) or SIGNAL_COLOURS[sig_i % len(SIGNAL_COLOURS)]
            sig_i += 1
        w = {"module_pin": mp, "slot_pin": sp["pin"], "slot_pin_name": sp["name"], "net": net, "color": colour,
             "gpio": sp.get("gpio") if sp.get("gpio") is not None else sp.get("source_gpio")}
        if sp.get("via"):
            w["via"] = sp["via"]  # driven through a buffer: the GPIO is upstream of this part
        wires.append(w)
    # straps that live only on the backplane side (e.g. J_MIC LR -> GND) still need a wire if the module has the pin
    mapped = {w["slot_pin"] for w in wires}
    for sp in slot["pins"]:
        if sp["pin"] in mapped or sp.get("kind") != "strap":
            continue
        for mp in order:
            if _norm(mp) in [_norm(a) for a in sp.get("accepts", [])]:
                wires.append({"module_pin": mp, "slot_pin": sp["pin"], "slot_pin_name": sp["name"], "net": sp["net"],
                              "color": "black", "gpio": None, "note": "strap"})
    return wires


def module_end_plan(m: dict, slot: dict, wires: list[dict]) -> dict:
    """How the harness meets the module: family, housings (physical order, with the port pin at every position),
    module_end_order (the module's own pin order), port_order and whether the wires cross (REVIEW-1 BLK-3 / MAJ-4)."""
    from .library import module_end_family

    mc = m.get("module_connector") or {}
    order = list(mc.get("pin_order") or [p.get("name") for p in (m.get("pins") or []) if p.get("name")])
    fam = module_end_family(m)
    by_mp = {w["module_pin"]: w for w in wires}
    housing_name = {"dupont_2.54": "Dupont 1x{n} female housing (2.54 mm)", "jst_ph": "PHR-{n} (JST PH 2.0 mm)",
                    "jst_xh": "XHP-{n} (JST XH 2.5 mm)", "spade_2.8": "2.8 mm (0.110 in) female insulated spade"}

    def pos(k, mp):
        w = by_mp.get(mp) if mp else None
        if w is None:
            return {"pos": k, "module_pin": mp, "port_pin": None, "slot_pin_name": None, "net": None, "wire_colour": None, "empty": True}
        return {"pos": k, "module_pin": mp, "port_pin": w["slot_pin"], "slot_pin_name": w["slot_pin_name"], "net": w["net"],
                "wire_colour": w["color"]}

    housings: list[dict] = []
    if fam == "factory_prewired":
        pads = list((m.get("prewire") or {}).get("pads") or [])
        housings = [{"housing": "factory solder joints on the module pads", "positions": [pos(k + 1, mp) for k, mp in enumerate(
            [w["module_pin"] for w in wires])], "pads": pads}]
    elif fam == "spade_2.8":
        housings = [{"housing": housing_name[fam], "positions": [pos(1, mp)]} for mp in order if mp in by_mp]
    elif fam is not None:
        for row in (mc.get("rows") or [order]):
            idx = [i for i, mp in enumerate(row) if mp in by_mp]
            if not idx:
                continue
            lo, hi = min(idx), max(idx)
            housings.append({"housing": housing_name[fam].format(n=hi - lo + 1),
                             "positions": [pos(k - lo + 1, row[k]) for k in range(lo, hi + 1)]})
    flat = [p["port_pin"] for h in housings for p in h["positions"] if p.get("port_pin")]
    crossed = bool(flat) and flat != sorted(flat) and fam != "factory_prewired"
    return {"family": fam, "housings": housings, "module_end_order": order,
            "port_order": [p["name"] for p in slot["pins"]], "crossed": crossed,
            "perm": [p["port_pin"] or 0 for h in housings for p in h["positions"]]}


def select_cable(lib: "Library", m: dict, slot: dict, length_mm: int, wires: Optional[list[dict]] = None) -> dict:
    """Pick the library harness row for a module on a port (REVIEW-1 BLK-3): same port, same length class, the
    module-end family of the module's connector, and the exact position table. Factory pre-wired modules take the
    port's base row (module end cut off and soldered at the factory). Without an exact row the base row is used as a
    made-to-drawing lead and `match` says 'fallback'."""
    from .library import port_family

    wires = harness_wires(m, slot) if wires is None else wires
    plan = module_end_plan(m, slot, wires)
    pf = port_family(slot) or "jst_ph"
    n = slot["pins_count"]
    base_id = f"CABLE_{'JSTXH' if pf == 'jst_xh' else 'JSTPH'}_{n}P_{int(length_mm)}"
    row, match, why = None, "exact", ""
    if plan["family"] == "factory_prewired":
        row = lib.get(base_id) if lib.has(base_id) else None
        why = "factory pre-wired module: the port's base lead with the module-end housing cut off and soldered to the pads"
    elif plan["family"] is not None:
        want = [[p["port_pin"] for p in h["positions"]] for h in plan["housings"]]
        for r in lib.harness_rows(slot["id"], int(length_mm)):
            me = r.get("module_end") or {}
            if me.get("family") != plan["family"]:
                continue
            have = [[p.get("port_pin") for p in h.get("positions", [])] for h in me.get("housings", [])]
            if have == want:
                row = r
                break
        why = f"module connector {(m.get('module_connector') or {}).get('type')} -> {plan['family']} module end, positions {plan['perm']}"
    if row is None:
        match = "fallback"
        row = lib.get(base_id) if lib.has(base_id) else None
        why = (f"no library harness row with a {plan['family'] or 'known'} module end for {m['id']} "
               f"(connector {(m.get('module_connector') or {}).get('type')!r}); the factory makes this lead to drawing")
    made = bool(row and row.get("made_to_drawing")) or match == "fallback"
    return {
        "module": row["id"] if row else None,
        "name": row.get("name") if row else None,
        "sku": row.get("sku") if row else None,
        "drawing": (row.get("drawing") if row else None) if match == "exact" else f"GS-H-{m['id']}-{slot['id']}",
        "match": match,
        "why": why,
        "module_end_family": plan["family"],
        "module_end_housings": plan["housings"],
        "module_end_order": plan["module_end_order"],
        "port_order": plan["port_order"],
        "crossed": plan["crossed"],
        "made_to_drawing": made,
        "straight_through": not made,
        "factory_prewired": plan["family"] == "factory_prewired",
        "stocked": bool(row and row.get("sourcing_in")),
        "factory_make": bool(row and row.get("factory_make")) or match == "fallback",
    }


# ------------------------------------------------------------------------------------------- main builder
def build_electrical(spec: "Spec", lib: "Library") -> dict:
    if spec.derived is None or not spec.derived.instances:
        raise StageError("electrical needs a normalized spec (run gstudio.normalize first)", {})
    bp = lib.backplane
    slots = {s["id"]: s for s in bp["slots"]}
    gpio_map = dict(bp["gpio"])
    instances_out: list[dict] = []
    connections: list[dict] = []
    nets: dict[str, set] = {}
    warnings: list[str] = []
    slots_used: list[str] = ["J_PWR", "U1"]

    def net_add(name: Optional[str], node: str) -> None:
        if name:
            nets.setdefault(name, set()).add(node)

    i2c_devices: list[dict] = []
    for inst in spec.derived.instances:
        m = lib.get(inst.module)
        slot_id = inst.slot
        rec: dict[str, Any] = {"ref": inst.ref, "module": m["id"], "role": inst.role, "name": m.get("name", ""),
                               "feature": inst.feature, "slot": slot_id, "source": inst.source}
        if inst.source == "core" or inst.role in ("backplane", "power_in", "adapter", "usb_cable"):
            instances_out.append(rec)
            continue
        if not slot_id:
            instances_out.append(rec)
            continue
        slot = slots[slot_id]
        if slot_id not in slots_used:
            slots_used.append(slot_id)
        harness = _harness_for(spec, inst.ref)
        wires = []
        taken: set = set()
        sig_i = 0
        if slot["kind"] == "module_socket":
            # plugs straight into the backplane: pin names match the socket map 1:1
            for sp in slot["pins"]:
                node_mod = f"{inst.ref}.{sp['name']}"
                node_bp = f"BP1.{slot_id}.{sp['pin']}"
                net_add(sp.get("net"), node_mod)
                net_add(sp.get("net"), node_bp)
                if sp.get("net"):
                    connections.append({"from": node_mod, "to": node_bp, "via": f"socket {slot_id}", "kind": "socket",
                                        "color": None, "net": sp["net"]})
            rec["harness"] = None
            rec["pins"] = {sp["name"]: {k: v for k, v in (("net", sp.get("net")), ("gpio", sp.get("gpio")), ("slot_pin", sp["pin"]))
                                        if v is not None} for sp in slot["pins"] if sp.get("net")}
        else:
            wires = harness_wires(m, slot)
            # required slot pins must be wired: power + every signal the slot exists for (roles decide which)
            need_power = _power_net(slot)
            if need_power and not any(w["net"] == need_power for w in wires) and inst.role not in ("button", "endstop"):
                warnings.append(f"{inst.ref} ({m['id']}): no module pin matched the {need_power} supply of {slot_id}")
            if not any(w["net"] == "GND" for w in wires) and inst.role not in ("speaker",):
                warnings.append(f"{inst.ref} ({m['id']}): no module pin matched GND on {slot_id}")
            length = (harness or {}).get("length_mm") or 150
            cable = f"{slot['connector']} {slot['pins_count']}p {length} mm"
            sel = select_cable(lib, m, slot, length, wires)
            if harness and harness.get("module") and sel["module"] and harness["module"] != sel["module"]:
                warnings.append(f"{inst.ref}: spec.derived.harness names {harness['module']} but the module end needs {sel['module']}")
            if sel["match"] == "fallback":
                warnings.append(f"{inst.ref} ({m['id']}): {sel['why']}")
            rec["harness"] = {"cable": f"{slot['connector']} {slot['pins_count']}p", "length_mm": length, "wires": wires,
                              "module_end": (m.get("module_connector") or {}).get("type", ""),
                              "cable_module": sel["module"], "cable_name": sel["name"], "sku": sel["sku"], "drawing": sel["drawing"],
                              "match": sel["match"], "module_end_family": sel["module_end_family"],
                              "module_end_housings": sel["module_end_housings"], "module_end_order": sel["module_end_order"],
                              "port_order": sel["port_order"], "crossed": sel["crossed"], "made_to_drawing": sel["made_to_drawing"],
                              "straight_through": sel["straight_through"], "factory_prewired": sel["factory_prewired"],
                              "factory_make": sel["factory_make"], "selection": sel["why"]}
            for w in wires:
                node_mod = f"{inst.ref}.{w['module_pin']}"
                node_bp = f"BP1.{slot_id}.{w['slot_pin']}"
                net_add(w["net"], node_mod)
                net_add(w["net"], node_bp)
                conn = {"from": node_mod, "to": node_bp, "via": cable, "kind": "cable", "color": w["color"], "net": w["net"],
                        "via_module": sel["module"], "via_name": sel["name"], "via_sku": sel["sku"]}
                if w.get("gpio") is not None:
                    conn["gpio"] = w["gpio"]
                if w.get("via"):
                    conn["through"] = w["via"]
                    conn["note"] = f"GPIO{w['gpio']:02d} via {w['via']}"
                connections.append(conn)
            rec["pins"] = {w["module_pin"]: {k: v for k, v in (("net", w["net"]), ("gpio", w.get("gpio")), ("via", w.get("via")),
                                                                ("slot_pin", w["slot_pin"])) if v is not None} for w in wires}
        # I2C devices
        e = m.get("electrical") or {}
        if slot_id and slot_id.startswith("J_I2C"):
            for a in e.get("i2c_addr") or []:
                i2c_devices.append({"ref": inst.ref, "addr": a, "module": m["id"]})
        instances_out.append(rec)

    # backplane-side nets: populated slots, support parts by group, the U1 header pins that carry a used net
    groups = {"power", "usb", "test"}
    if "J_LED" in slots_used:
        groups.add("led")
    if any(sid.startswith("J_I2C") for sid in slots_used):
        groups.add("i2c")
    if "A1" in slots_used:
        groups.add("audio")
    for sid in slots_used:
        if sid in ("U1",):
            continue
        for sp in slots[sid]["pins"]:
            net_add(sp.get("net"), f"BP1.{sid}.{sp['pin']}")
    support = []
    for part in bp["parts"]:
        if part.get("group") in groups and part.get("pins"):
            support.append({"ref": part["ref"], "group": part["group"], "value": part.get("value", ""), "footprint": part["footprint"]})
            for pad, net in part["pins"].items():
                net_add(net, f"BP1.{part['ref']}.{pad}")
    wanted = set(nets) | set(bp.get("always_nets", []))
    core_socket_pins = []  # one row per DevKitC header pin that carries a used net (REVIEW-1 BLK-3: no bare "U1.header" row)
    for p in slots["U1"]["pins"]:
        if p.get("net") in wanted:
            net_add(p["net"], f"U1.{p['pin']}")
            net_add(p["net"], f"BP1.{p['socket']}.{p['socket_pin']}")
            core_socket_pins.append({"from": f"U1.{p['pin']}", "to": f"BP1.{p['socket']}.{p['socket_pin']}", "net": p["net"],
                                     "devkit_label": p.get("devkit_label"), "gpio": p.get("gpio"), "via": f"socket {p['socket']}"})
    if "A1" in slots_used and "J_SPK" not in slots_used:
        warnings.append("A1 amp fitted without a speaker on J_SPK")
    if "J_SPK" in slots_used and "A1" not in slots_used:
        warnings.append("speaker on J_SPK without the A1 amplifier")

    # buses ------------------------------------------------------------------------------------
    buses = []
    addr_seen: dict[str, str] = {}
    clashes = []
    for dv in i2c_devices:
        if dv["addr"] in addr_seen and addr_seen[dv["addr"]] != dv["ref"]:
            clashes.append(f"I2C address {dv['addr']} used by {addr_seen[dv['addr']]} and {dv['ref']}")
        addr_seen.setdefault(dv["addr"], dv["ref"])
    if i2c_devices:
        buses.append({"type": "i2c", "sda": gpio_map["I2C_SDA"], "scl": gpio_map["I2C_SCL"], "freq_hz": 400000,
                      "pullups": "4.7 k on the backplane (R4, R5)", "devices": i2c_devices, "clashes": clashes})
    if "A1" in slots_used:
        buses.append({"type": "i2s_tx", "port": 0, "bclk": gpio_map["I2S_SPK_BCLK"], "ws": gpio_map["I2S_SPK_LRC"],
                      "dout": gpio_map["I2S_SPK_DIN"], "enable": gpio_map["AMP_SD"], "devices": [{"ref": _ref_on(spec, "A1")}]})
    if "J_MIC" in slots_used:
        buses.append({"type": "i2s_rx", "port": 1, "bclk": gpio_map["I2S_MIC_SCK"], "ws": gpio_map["I2S_MIC_WS"],
                      "din": gpio_map["I2S_MIC_SD"], "channel": "left", "devices": [{"ref": _ref_on(spec, "J_MIC")}]})
    spi_dev = []
    if "S1" in slots_used:
        spi_dev.append({"ref": _ref_on(spec, "S1"), "cs": gpio_map["SD_CS"]})
    if "J_DISP" in slots_used:
        spi_dev.append({"ref": _ref_on(spec, "J_DISP"), "cs": gpio_map["DISP_CS"], "dc": gpio_map["DISP_DC"],
                        "rst": gpio_map["DISP_RST"], "bl": gpio_map["DISP_BL"]})
    if spi_dev:
        buses.append({"type": "spi", "host": "FSPI", "sck": gpio_map["SPI_SCK"], "mosi": gpio_map["SPI_MOSI"],
                      "miso": gpio_map["SPI_MISO"], "devices": spi_dev})
    if "J_LED" in slots_used:
        r = _ref_on(spec, "J_LED")
        kind = "servo_pwm" if r.startswith("SRV") else "ws2812"
        buses.append({"type": kind, "gpio": gpio_map["LED_DIN"], "level_shift": "74AHCT1G125 to 5 V", "devices": [{"ref": r}]})

    # gpio table: only signals present on this design's board ------------------------------------
    sys_sigs = {"UART0_TX", "UART0_RX", "BOOT_IO0", "USB_DP", "USB_DN"}
    gpio = {}
    for sig, g in gpio_map.items():
        n = "LED_DIN_3V3" if sig == "LED_DIN" else sig
        if n in nets or sig in sys_sigs:
            gpio[sig] = g
    gpio_paths = {}
    for sid in slots_used:
        for sp in slots[sid]["pins"]:
            if sp.get("source_gpio") is not None and sp.get("net") in nets:
                gpio_paths[sp["net"]] = {"gpio": sp["source_gpio"], "via": sp.get("via", ""),
                                         "signal": next((k for k, v in gpio_map.items() if v == sp["source_gpio"]), None)}
    # sanity: unique and legal
    reserved = {int(k) for k in bp.get("gpio_reserved", {})}
    vals = [g for s, g in gpio.items() if s not in sys_sigs]
    dup = sorted({g for g in vals if vals.count(g) > 1})
    bad = sorted({g for g in vals if g in reserved})
    if dup or bad:
        raise StageError("GPIO plan is inconsistent", {"duplicates": dup, "reserved_used": bad})

    power = power_budget(spec, lib)
    # REVIEW-1 MAJ-5 / MIN-2 (pipeline's pure functions): what the +5V limit rests on and the voltage-drop budget
    try:
        from .order.power import drop_budget, supply_basis

        adapter_row = next((lib.get(i.module) for i in spec.derived.instances if i.role == "adapter"), None)
        power["supply"] = supply_basis(power, bp["power"]["limits"], adapter_row)
        power["drop_budget"] = drop_budget(power, bp, lambda mid: lib.get(mid) if lib.has(mid) else None, instances_out, adapter_row)
    except Exception as exc:  # optional: the checks stage computes the same dicts
        warnings.append(f"power supply basis / drop budget not computed here: {exc}")
    doc = {
        "schema": ELECTRICAL_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "backplane": BACKPLANE_ID,
        "core": {"ref": "U1", "module": spec.electronics.core},
        "instances": instances_out,
        "slots_used": slots_used,
        "gpio": gpio,
        "gpio_paths": gpio_paths,
        "buses": buses,
        "nets": [{"name": k, "nodes": sorted(v)} for k, v in sorted(nets.items())],
        "connections": connections,
        "core_socket_pins": core_socket_pins,
        "support_parts": support,
        "power": power,
        "warnings": warnings + clashes,
    }
    return doc


def _ref_on(spec: "Spec", slot: str) -> str:
    for i in spec.derived.instances:
        if i.slot == slot:
            return i.ref
    return ""


# ------------------------------------------------------------------------------------------- power
def effective_caps(spec: "Spec") -> dict:
    """Firmware output caps for this design: behaviour.settings as validated by gstudio.firmware.settings (normalize stores
    the validated settings), else asked from validate_settings directly. Missing values mean no cap (100 %)."""
    st = dict(spec.behaviour.settings or {})
    src = "behaviour.settings (validated by gstudio.firmware.settings)"
    if "volume_max" not in st or "led_max_percent" not in st:
        try:
            from .firmware.settings import validate_settings  # light module (pydantic only)

            try:
                eff, _ = validate_settings(spec.behaviour.template, st, child_directed=spec.audience.child_directed)
            except TypeError:
                eff, _ = validate_settings(spec.behaviour.template, st)
            if isinstance(eff, dict):
                st = {**eff, **st}
                src = "gstudio.firmware.settings.validate_settings defaults"
        except Exception:
            src = "none found: uncapped"
    vol = st.get("volume_max")
    led = st.get("led_max_percent")
    return {"volume_max": float(vol) if isinstance(vol, (int, float)) else None,
            "led_max_percent": float(led) if isinstance(led, (int, float)) else None, "source": src}


def power_budget(spec: "Spec", lib: "Library") -> dict:
    """Two figures: worst case uncapped (every module at its max) and the effective peak with the firmware caps applied
    (LED current scales with led_max_percent; amp max current scales with (volume_max/100)^2, an estimate). `ok` uses
    the capped figure."""
    lim = lib.backplane["power"]["limits"]
    caps = effective_caps(spec)
    rows = []
    tot = {"+5V": [0.0, 0.0, 0.0], "+3V3": [0.0, 0.0, 0.0]}  # typ, max, max_capped
    for inst in spec.derived.instances:
        m = lib.get(inst.module)
        e = m.get("electrical") or {}
        rail = e.get("supply_rail")
        if inst.source == "core":
            rail = "+3V3"  # the ESP32-S3 draws from the DevKitC LDO
        if rail not in ("+5V", "+3V3"):
            continue
        typ, mx = float(e.get("current_ma_typ") or 0), float(e.get("current_ma_max") or 0)
        basis = "datasheet" if e.get("current_basis") == "library" else ("estimate" if e.get("current_basis") == "estimate" else "none")
        if mx == 0 and inst.role not in ("button", "endstop"):
            basis = "unknown"
        if inst.role in ("servo", "servo_continuous"):
            if mx > 0:  # the row's own figures (motion pack rows carry class planning values, labelled estimate)
                if basis in ("none", "unknown"):
                    basis = "estimate"
            else:  # no figure in the row (SG90 row: 0): a conservative planning figure, labelled
                typ, mx, basis = 150.0, 700.0, "estimate (no datasheet figure; SG90-class stall planning value)"
        capped, cap = mx, None
        if inst.role == "led" and caps["led_max_percent"] is not None:
            capped = max(typ * min(1.0, caps["led_max_percent"] / 100.0), mx * caps["led_max_percent"] / 100.0)
            cap = f"led_max_percent {caps['led_max_percent']:g} (estimate: LED current scales with brightness)"
        elif inst.role == "amp" and caps["volume_max"] is not None:
            capped = max(typ, mx * (caps["volume_max"] / 100.0) ** 2)
            cap = f"volume_max {caps['volume_max']:g} (estimate: amp max current x (volume/100)^2)"
        row = {"ref": inst.ref, "module": m["id"], "rail": rail, "ma_typ": typ, "ma_max": mx, "ma_max_capped": round(capped, 1),
               "basis": basis}
        if cap:
            row["cap"] = cap
        rows.append(row)
        t = tot[rail]
        t[0] += typ
        t[1] += mx
        t[2] += capped
    # +3V3 comes out of the DevKitC LDO, whose input is +5V: the 3V3 load is part of the 5V total
    t5 = [tot["+5V"][i] + tot["+3V3"][i] for i in range(3)]
    t3 = tot["+3V3"]
    ok5_unc = t5[1] <= lim["limit_5v_ma"]
    ok5 = t5[2] <= lim["limit_5v_ma"]
    ok3 = t3[2] <= lim["limit_3v3_ma"]
    notes = []
    if not ok5_unc:
        notes.append(f"Uncapped worst case {t5[1]:.0f} mA exceeds the {lim['limit_5v_ma']} mA budget (90% of the 1.5 A fuse hold); "
                     f"with the firmware caps the peak is {t5[2]:.0f} mA" + (" (within budget)." if ok5 else " (still over budget)."))
    if not ok5:
        notes.append("Even with the firmware caps the 5 V peak is over budget: lower led_max_percent or volume_max, or drop a module.")
    if not ok3:
        notes.append(f"Peak 3.3 V draw {t3[2]:.0f} mA exceeds the {lim['limit_3v3_ma']} mA LDO budget.")
    notes.append("Capped figures are estimates (LED current x brightness, amp current x volume squared); peaks are summed "
                 "worst-case, which real use rarely reaches. Figures marked 'estimate' are not datasheet values.")
    return {
        "rails": [{"name": "+5V", "source": "USB-C VBUS via F1 (PTC 1.5 A hold)", "limit_ma": lim["limit_5v_ma"]},
                  {"name": "+3V3", "source": "DevKitC on-board LDO SGM2212-3.3 (800 mA rating)", "limit_ma": lim["limit_3v3_ma"]}],
        "caps": caps,
        "budget": rows,
        "total_ma_typ": round(t5[0], 1),
        "total_ma_max": round(t5[1], 1),
        "total_ma_max_capped": round(t5[2], 1),
        "rail_3v3_ma_typ": round(t3[0], 1),
        "rail_3v3_ma_max": round(t3[2], 1),
        "limit_ma": lim["limit_5v_ma"],
        "ok": bool(ok5 and ok3),
        "ok_uncapped": bool(ok5_unc and t3[1] <= lim["limit_3v3_ma"]),
        "ok_5v": bool(ok5),
        "ok_3v3": bool(ok3),
        "firmware_caps_required": not ok5_unc,
        "notes": " ".join(notes),
    }


# ------------------------------------------------------------------------------------------- stage entry
def run(ctx: "BuildContext") -> dict:
    ctx.progress("electrical", "allocating slots and wiring", 5)
    doc = build_electrical(ctx.spec, ctx.lib)
    p = ctx.path("electrical.json")
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    ctx.electrical = doc
    ctx.artifacts["electrical.json"] = str(p)
    for w in doc["warnings"]:
        ctx.warn("electrical", w)
    ctx.progress("electrical", "electrical.json written", 100)
    return {"slots_used": doc["slots_used"], "instances": len(doc["instances"]), "nets": len(doc["nets"]),
            "power_ok": doc["power"]["ok"], "total_ma_max": doc["power"]["total_ma_max"],
            "total_ma_max_capped": doc["power"]["total_ma_max_capped"], "warnings": doc["warnings"]}


__all__ = ["run", "build_electrical", "assign_slots", "power_budget", "effective_caps", "harness_wires", "module_end_plan",
           "select_cable"]
