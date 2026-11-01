"""Board design model: backplane-v1.json + electrical.json -> the parts, nets and placement of ONE design's board.

Every design gets the same outline, holes and slot positions (library/backplane-v1.json, owned by foundation).
Only populated slots and the support circuits they need are placed. Support parts keep FIXED reference
designators and positions from backplane-v1 `parts[]`, so the factory always sees the same core assembly. The
pad-to-net map of each support part is defined here (SUPPORT), because backplane-v1 describes support parts by
value and role only.

Board coordinates: mm, origin at the board's lower-left corner, top view, +X right (robot's left), +Y rear.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import StageError

# ----------------------------------------------------------------------------- data classes


@dataclass
class Part:
    ref: str
    value: str
    symbol: str                        # KiCad lib_id ("Device:R") or "gstudio:<name>" for generated symbols
    footprint: str                     # "Lib:Name"
    at: tuple[float, float]            # board coords
    rot: float = 0.0                   # degrees CCW, top view
    side: str = "top"                  # top | bottom
    pads: dict[str, str] = field(default_factory=dict)       # pad number -> net ("" = not connected)
    pin_names: dict[str, str] = field(default_factory=dict)  # pad number -> function name
    block: str = "core"                # schematic block: power | core | audio | storage | ports | test | mech
    label: str | None = None           # silkscreen port label
    slot: str | None = None            # backplane slot this part realises
    facing: str = ""                   # front | rear | left | right | up
    anchor: str | None = None          # auto placement: ref to sit next to (None = fixed position)
    offset: tuple[float, float] | None = None
    bom: dict = field(default_factory=dict)    # description, mpn, ...
    in_bom: bool = True
    on_board: bool = True
    board_only: bool = False           # footprint without a schematic symbol (fiducials, NPTH posts)
    exclude_pos: bool = False          # not in pick-and-place
    gen_symbol: dict | None = None     # {"name", "title", "pins": [(pad, name)], "side": "left|right"}


@dataclass
class Design:
    name: str
    slug: str
    revision: str
    date: str
    engine_version: str
    backplane_id: str
    backplane_version: str
    width: float
    height: float
    thickness: float
    corner_radius: float
    parts: list[Part]
    keepouts: list[dict]
    notes: list[str]
    omitted_slots: list[str]
    used_slots: list[str]
    electrical_nets: dict[str, list[str]]      # from electrical.json (raw nodes)
    splits: dict[str, str]                     # "REF.PAD" -> net it moved to because of a series part
    design_id: str
    devkit: dict | None = None                 # devkit outline data for silk/fab
    deviations: list[str] = field(default_factory=list)
    waivers: list[dict] = field(default_factory=list)

    def part(self, ref: str) -> Part:
        for p in self.parts:
            if p.ref == ref:
                return p
        raise KeyError(ref)

    def nets(self) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = {}
        for p in self.parts:
            if p.board_only:
                continue
            for pad, net in p.pads.items():
                if net:
                    out.setdefault(net, [])
                    if (p.ref, pad) not in out[net]:
                        out[net].append((p.ref, pad))
        return {k: sorted(v, key=lambda rp: (rp[0], _natkey(rp[1]))) for k, v in sorted(out.items())}


def _natkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


# ----------------------------------------------------------------------------- net classes

POWER_NETS = {"GND", "VBUS", "+5V", "+3V3"}


def net_class(net: str) -> str:
    return "Power" if net in POWER_NETS else "Default"


# ----------------------------------------------------------------------------- backplane loader

def _xy(v: Any) -> tuple[float, float]:
    if isinstance(v, dict):
        return float(v.get("x", 0.0)), float(v.get("y", 0.0))
    return float(v[0]), float(v[1])


def load_backplane(src: str | Path | dict) -> dict:
    """Load library/backplane-v1.json (gadget-backplane/1) into a lookup structure."""
    bp = json.loads(Path(src).read_text()) if not isinstance(src, dict) else src
    if bp.get("schema") != "gadget-backplane/1":
        raise StageError("backplane definition is not gadget-backplane/1", {"schema": bp.get("schema")})
    board = bp["board"]
    W, H = (float(v) for v in board["size"])
    slots = {s["id"]: s for s in bp["slots"]}
    parts = {p["ref"]: p for p in bp["parts"]}
    for tp in bp.get("test_pads", []):
        parts.setdefault(tp["ref"], tp)
    return {"id": bp["id"], "version": bp.get("version", ""), "raw": bp, "width": W, "height": H,
            "thickness": float(board.get("thickness", 1.6)), "corner_radius": float(board.get("corner_radius", 1.0)),
            "holes": board.get("holes", []), "slots": slots, "parts": parts,
            "net_classes": bp.get("net_classes", {}), "waivers": bp.get("drc_waivers", [])}


def slot_pin_pads(bp: dict, sid: str) -> list[dict]:
    """Physical (part ref, pad) for every pin of a slot: [{pin, name, net, gpio, ref, pad}]."""
    s = bp["slots"][sid]
    out = []
    for p in s.get("pins", []):
        ref = p.get("socket") or sid
        pad = p.get("socket_pin")
        if pad is None:
            pad = p["pin"] if ref == sid or str(p["pin"]).isdigit() else 1
        name = p.get("name") or p.get("devkit_label") or str(p["pin"])
        out.append({"pin": str(p["pin"]), "name": str(name), "net": p.get("net") or "", "gpio": p.get("gpio"),
                    "ref": ref, "pad": str(pad), "label": p.get("devkit_label")})
    return out


# ----------------------------------------------------------------------------- support circuits

ALWAYS = "always"

SUPPORT: dict[str, dict] = {
    # USB-C sink + protection (always fitted: J_PWR and U1 are in every design)
    "R1": {"symbol": "Device:R", "value": "5.1k 1%", "pads": {"1": "CC1", "2": "GND"}, "block": "power", "when": ALWAYS,
           "bom": {"description": "Resistor 5.1 kOhm 1% 0603 (USB-C CC1 sink pull-down)", "mpn": "generic 0603 5.1k 1%"}},
    "R2": {"symbol": "Device:R", "value": "5.1k 1%", "pads": {"1": "CC2", "2": "GND"}, "block": "power", "when": ALWAYS,
           "bom": {"description": "Resistor 5.1 kOhm 1% 0603 (USB-C CC2 sink pull-down)", "mpn": "generic 0603 5.1k 1%"}},
    "F1": {"symbol": "Device:Polyfuse", "value": "PTC 1.5A hold", "pads": {"1": "VBUS", "2": "+5V"}, "block": "power",
           "when": ALWAYS, "bom": {"description": "Resettable PTC fuse 1812, I_hold 1.5 A, V_max >= 6 V",
                                   "mpn": "class only (backplane-v1: PTC 1.5 A hold, >= 6 V); choose MPN"}},
    "D1": {"symbol": "Diode:SMAJ5.0A", "value": "SMAJ5.0A", "pads": {"1": "+5V", "2": "GND"}, "block": "power",
           "when": ALWAYS, "bom": {"description": "TVS 5.0 V standoff, unidirectional, SMA (cathode to +5V)",
                                   "mpn": "SMAJ5.0A class (backplane-v1); choose maker"}},
    "C1": {"symbol": "Device:C", "value": "10uF 25V", "pads": {"1": "+5V", "2": "GND"}, "block": "power", "when": ALWAYS,
           "bom": {"description": "MLCC 10 uF 25 V X5R 0805 (+5V bulk)", "mpn": "generic 0805 10uF 25V X5R"}},
    "C2": {"symbol": "Device:C", "value": "100nF", "pads": {"1": "+5V", "2": "GND"}, "block": "power", "when": ALWAYS,
           "bom": {"description": "MLCC 100 nF 50 V X7R 0603", "mpn": "generic 0603 100nF 50V X7R"}},
    "U2": {"symbol": "Power_Protection:USBLC6-2SC6", "value": "USBLC6-2SC6",
           "pads": {"1": "USB_DP", "6": "USB_DP", "3": "USB_DN", "4": "USB_DN", "5": "+5V", "2": "GND"},
           "block": "power", "when": ALWAYS,
           "bom": {"description": "USB 2.0 ESD array, 2 lines, SOT-23-6 (flow-through I/O1 = pins 1/6, I/O2 = 3/4)",
                   "mpn": "USBLC6-2SC6"}},
    # LED port: 3.3 V -> 5 V data buffer
    "U3": {"symbol": "74xGxx:74AHCT1G125", "value": "74AHCT1G125",
           "pads": {"1": "GND", "2": "LED_DIN_3V3", "3": "GND", "4": "LED_DIN_BUF", "5": "+5V"}, "block": "ports",
           "when": {"J_LED"}, "bom": {"description": "Single bus buffer, TTL input, 5 V, SOT-23-5 (OE tied low)",
                                      "mpn": "74AHCT1G125 (e.g. SN74AHCT1G125DBVR)"}},
    "R3": {"symbol": "Device:R", "value": "330R", "pads": {"1": "LED_DIN_BUF", "2": "LED_DIN_5V"}, "block": "ports",
           "when": {"J_LED"}, "bom": {"description": "Resistor 330 Ohm 0603 (LED data series)", "mpn": "generic 0603 330R"}},
    "C5": {"symbol": "Device:C", "value": "100nF", "pads": {"1": "+5V", "2": "GND"}, "block": "ports", "when": {"J_LED"},
           "bom": {"description": "MLCC 100 nF 0603 (U3 decoupling)", "mpn": "generic 0603 100nF X7R"}},
    "C4": {"symbol": "Device:C", "value": "22uF 10V", "pads": {"1": "+5V", "2": "GND"}, "block": "ports",
           "when": {"J_LED", "J_SRV"}, "bom": {"description": "MLCC 22 uF 10 V X5R 1206 (J_LED / J_SRV 5 V bulk)",
                                               "mpn": "generic 1206 22uF 10V X5R"}},
    # I2C pull-ups (any I2C port)
    "R4": {"symbol": "Device:R", "value": "4.7k", "pads": {"1": "I2C_SDA", "2": "+3V3"}, "block": "ports",
           "when": {"J_I2C1", "J_I2C2", "J_I2C3"}, "bom": {"description": "Resistor 4.7 kOhm 0603 (I2C SDA pull-up)",
                                                          "mpn": "generic 0603 4.7k"}},
    "R5": {"symbol": "Device:R", "value": "4.7k", "pads": {"1": "I2C_SCL", "2": "+3V3"}, "block": "ports",
           "when": {"J_I2C1", "J_I2C2", "J_I2C3"}, "bom": {"description": "Resistor 4.7 kOhm 0603 (I2C SCL pull-up)",
                                                          "mpn": "generic 0603 4.7k"}},
    # servo signal series resistor: splits SRV_SIG (GPIO side) from the port pin
    "R6": {"symbol": "Device:R", "value": "1k", "pads": {"1": "SRV_SIG", "2": "SRV_SIG_PORT"}, "block": "ports",
           "when": {"J_SRV"}, "split": {("J_SRV", "3"): "SRV_SIG_PORT"},
           "bom": {"description": "Resistor 1 kOhm 0603 (servo signal series)", "mpn": "generic 0603 1k"}},
    # amp
    "R7": {"symbol": "Device:R", "value": "100k", "pads": {"1": "AMP_SD", "2": "GND"}, "block": "audio", "when": {"A1"},
           "bom": {"description": "Resistor 100 kOhm 0603 (AMP_SD pull-down: amp off until firmware enables it)",
                   "mpn": "generic 0603 100k"}},
    "C3": {"symbol": "Device:C", "value": "22uF 10V", "pads": {"1": "+5V", "2": "GND"}, "block": "audio", "when": {"A1"},
           "bom": {"description": "MLCC 22 uF 10 V X5R 1206 (A1 VIN bulk)", "mpn": "generic 1206 22uF 10V X5R"}},
}

# Footprint swaps are applied only when backplane-v1 and the module library disagree (none today: both say HRO).
FOOTPRINT_PREFERENCE: dict[str, str] = {}

BLOCK_OF_SLOT = {"U1": "core", "J_PWR": "power", "A1": "audio", "J_SPK": "audio", "J_MIC": "audio", "S1": "storage"}
GEN_SYMBOLS = {
    "J_U1A": ("DevKitC-1_J1_Socket", "ESP32-S3-DevKitC-1 header J1", "left"),
    "J_U1B": ("DevKitC-1_J3_Socket", "ESP32-S3-DevKitC-1 header J3", "right"),
    "J_A1": ("MAX98357A_Breakout_Socket", "MAX98357A breakout (1x7)", "left"),
    "J_A1P": ("MAX98357A_VO_Plus_Socket", "Amp VO+ pin", "left"),
    "J_A1N": ("MAX98357A_VO_Minus_Socket", "Amp VO- pin", "left"),
    "J_S1": ("MicroSD_Module_Socket", "microSD SPI module (1x6)", "left"),
}
PORT_LABELS = {"J_PWR": "USB-C", "J_SPK": "SPK", "J_MIC": "MIC", "J_DISP": "DISP", "J_I2C1": "I2C1", "J_I2C2": "I2C2",
               "J_I2C3": "I2C3", "J_IO1": "IO1", "J_IO2": "IO2", "J_IO3": "IO3", "J_LED": "LED", "J_SRV": "SERVO",
               "J_S1": "SD", "J_A1": "AMP"}


def _pin_display_name(p: dict) -> str:
    """Devkit header pins read as GPIO numbers; every other module/port pin keeps its function name."""
    lab = p.get("label")
    if lab is None:
        return str(p["name"])
    if p.get("gpio") is not None:
        extra = {"TX": "/TX0", "RX": "/RX0"}.get(str(lab), "")
        return f"GPIO{p['gpio']}{extra}"
    return {"G": "GND", "RST": "EN"}.get(str(lab), str(lab))


# ----------------------------------------------------------------------------- electrical.json adapter

def electrical_nets(electrical: dict) -> dict[str, list[str]]:
    nets = electrical.get("nets") or []
    if isinstance(nets, dict):
        return {k: list(v.get("nodes", v) if isinstance(v, dict) else v) for k, v in nets.items()}
    return {n["name"]: list(n.get("nodes", [])) for n in nets}


def used_slots(electrical: dict, bp: dict) -> list[str]:
    used = [u for u in (electrical.get("slots_used") or []) if u != "BP1"]
    for sid in ("U1", "J_PWR"):
        if sid not in used:
            used.insert(0 if sid == "U1" else 1, sid)
    unknown = [u for u in used if u not in bp["slots"]]
    if unknown:
        raise StageError(f"electrical.json uses slots that {bp['id']} does not define: {unknown}",
                         {"unknown_slots": unknown})
    return used


def resolve_node(node: str, bp: dict) -> tuple[str, str] | None:
    """Electrical node -> (part ref, pad). Accepts 'J_I2C1.3', 'BP1.J_I2C1.3', 'U1.J1-12', 'U1.GPIO8',
    'A1.VO+', 'A1.LRC', 'J_U1A.12', 'TP3' / 'TP3.1'. Returns None for module-side (off-board) nodes."""
    toks = node.split(".")
    if toks[0] == "BP1":
        toks = toks[1:]
    ref = toks[0]
    pin = ".".join(toks[1:]) if len(toks) > 1 else "1"
    if ref in bp["slots"]:
        for p in slot_pin_pads(bp, ref):
            if pin == p["pin"]:
                return p["ref"], p["pad"]
        for p in slot_pin_pads(bp, ref):
            if pin in (p["name"], p.get("label")) or (p["gpio"] is not None and pin in (f"GPIO{p['gpio']}", f"IO{p['gpio']}")):
                return p["ref"], p["pad"]
        raise StageError(f"electrical.json node {node!r}: slot {ref} has no pin {pin!r}", {"node": node})
    if ref in bp["parts"]:
        return ref, pin
    return None


# ----------------------------------------------------------------------------- build the design

def design_id(bp: dict, electrical: dict, meta: dict) -> str:
    blob = json.dumps({"bp": bp["raw"].get("version"), "bp_hash": hashlib.sha256(json.dumps(bp["raw"], sort_keys=True).encode()).hexdigest(),
                       "used": sorted(electrical.get("slots_used") or []), "nets": electrical_nets(electrical),
                       "slug": meta.get("slug")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _support_needed(when, used: set[str]) -> bool:
    return when == ALWAYS or bool(set(when) & used)


GROUP_BLOCK = {"power": "power", "usb": "power", "led": "ports", "i2c": "ports", "audio": "audio", "test": "test"}


def support_groups(used: set[str]) -> set[str]:
    """Support groups fitted for a set of populated slots (same rule as gstudio.electrical)."""
    g = {"power", "usb", "test"}
    if "J_LED" in used:
        g.add("led")
    if any(s.startswith("J_I2C") for s in used):
        g.add("i2c")
    if "A1" in used:
        g.add("audio")
    return g


def _generic_symbol(fp: str) -> str:
    name = fp.split(":", 1)[-1]
    if name.startswith("R_"):
        return "Device:R"
    if name.startswith("C_"):
        return "Device:C"
    if name.startswith("Fuse"):
        return "Device:Polyfuse"
    raise StageError(f"no symbol known for support footprint {fp}", {"footprint": fp})


def _slot_parts(bp: dict, sid: str) -> list[dict]:
    s = bp["slots"][sid]
    refs = s.get("parts") or [sid]
    return [bp["parts"][r] for r in refs if r in bp["parts"]]


def build_design(bp: dict, electrical: dict, meta: dict, lib_footprints: dict | None = None) -> Design:
    used = used_slots(electrical, bp)
    used_set = set(used)
    parts: list[Part] = []
    deviations: list[str] = []
    by_ref: dict[str, Part] = {}

    for sid in used:
        s = bp["slots"][sid]
        pin_pads = slot_pin_pads(bp, sid)
        for bpp in _slot_parts(bp, sid):
            ref = bpp["ref"]
            fp = bpp["footprint"]
            if fp in FOOTPRINT_PREFERENCE and (lib_footprints or {}).get(s.get("module")) == FOOTPRINT_PREFERENCE[fp]:
                deviations.append(f"{ref}: footprint {FOOTPRINT_PREFERENCE[fp]} (the slot module's own footprint, has a "
                                  f"KiCad 3D model) instead of {fp}")
                fp = FOOTPRINT_PREFERENCE[fp]
            mine = [p for p in pin_pads if p["ref"] == ref]
            pads = {p["pad"]: p["net"] for p in mine}
            names = {p["pad"]: _pin_display_name(p) for p in mine}
            gen = None
            if ref in GEN_SYMBOLS:
                nm, title, side = GEN_SYMBOLS[ref]
                gen = {"name": nm, "title": title, "side": side,
                       "pins": sorted(((p["pad"], _pin_display_name(p)) for p in mine), key=lambda t: _natkey(t[0]))}
                symbol = f"gstudio:{nm}"
                value = title
                bom = {"description": f"Female socket 1x{len(mine):02d} 2.54 mm for {title}",
                       "mpn": f"generic 1x{len(mine):02d} 2.54 mm female header, 8.5 mm"}
            elif s["kind"] == "power_in":
                symbol, value = "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C 16P"
                bom = {"description": "USB Type-C receptacle 16P, top mount, horizontal (power + USB 2.0 data)",
                       "mpn": "GCT USB4105-GF-A" if "GCT" in fp else "HRO TYPE-C-31-M-12"}
            else:
                n = int(s.get("pins_count") or len(mine))
                fam = (s.get("connector") or "JST").split()[0]
                symbol = f"Connector_Generic:Conn_01x{n:02d}"
                value = f"{s.get('connector', 'JST')} {n}p {PORT_LABELS.get(ref, ref)}"
                hdr = "B{n}B-PH-K-S" if "PH" in fp else "B{n}B-XH-A"
                bom = {"description": f"{fam} {s.get('connector', '')} {n}-pin vertical header ({s.get('title', '')})",
                       "mpn": "JST " + hdr.format(n=n)}
            p = Part(ref=ref, value=value, symbol=symbol, footprint=fp, at=_xy(bpp["at"]),
                     rot=float(bpp.get("rotation_deg", 0.0)), side=bpp.get("side", "top"), pads=pads, pin_names=names,
                     block=BLOCK_OF_SLOT.get(sid, "ports"), label=PORT_LABELS.get(ref), slot=sid,
                     facing=str(s.get("facing", "")).split()[0] if s.get("facing") else "", bom=bom,
                     exclude_pos=s["kind"] != "power_in", gen_symbol=gen)
            parts.append(p)
            by_ref[ref] = p

    # support parts at their fixed backplane positions; pad->net maps come from backplane-v1 `parts[].pins`
    # (single source of truth, the same data gstudio.electrical uses); SUPPORT only adds symbol/value/BOM text.
    groups = support_groups(used_set)
    splits: dict[str, str] = {}
    for bpp in bp["raw"]["parts"]:
        ref = bpp["ref"]
        if bpp.get("role") != "support":
            continue
        sp = SUPPORT.get(ref, {})
        grp = bpp.get("group")
        needed = (grp in groups) if grp else _support_needed(sp.get("when", ALWAYS), used_set)
        if not needed:
            continue
        pads = {str(k): str(v) for k, v in (bpp.get("pins") or sp.get("pads") or {}).items()}
        if not pads:
            raise StageError(f"{bp['id']} support part {ref} has no pin map", {"ref": ref})
        symbol = sp.get("symbol") or _generic_symbol(bpp["footprint"])
        value = sp.get("value") or str(bpp.get("value", ref)).split(" (")[0]
        bom = dict(sp.get("bom") or {"description": str(bpp.get("value", ""))}, backplane_value=bpp.get("value", ""))
        p = Part(ref=ref, value=value, symbol=symbol, footprint=bpp["footprint"], at=_xy(bpp["at"]),
                 rot=float(bpp.get("rotation_deg", 0.0)), side=bpp.get("side", "top"), pads=pads,
                 block=sp.get("block") or GROUP_BLOCK.get(grp or "", "ports"), bom=bom)
        parts.append(p)
        by_ref[ref] = p
        for (r, pad), net in (sp.get("split") or {}).items():
            if r in by_ref and not bpp.get("pins"):
                by_ref[r].pads[pad] = net
                splits[f"{r}.{pad}"] = net

    # nets from electrical.json override/confirm slot pin nets (except intentional splits)
    enets = electrical_nets(electrical)
    seen: dict[tuple[str, str], str] = {}
    for net, nodes in enets.items():
        for nd in nodes:
            r = resolve_node(nd, bp)
            if r is None or r[0] not in by_ref:
                continue
            if r in seen and seen[r] != net:
                raise StageError(f"electrical.json puts {nd} on two nets: {seen[r]} and {net}", {"node": nd})
            seen[r] = net
            key = f"{r[0]}.{r[1]}"
            if key in splits:
                continue
            by_ref[r[0]].pads[r[1]] = net

    # test pads (always: factory jig), mounting holes, NPTH posts (with S1), fiducials
    for ref in sorted((r for r, v in bp["parts"].items() if v.get("role") == "test_pad"), key=_natkey):
        tp = bp["parts"][ref]
        net = str((tp.get("pins") or {}).get("1") or tp.get("value"))
        parts.append(Part(ref=ref, value=net, symbol="Connector:TestPoint", footprint=tp["footprint"],
                          at=_xy(tp["at"]), side=tp.get("side", "bottom"), pads={"1": net}, block="test", label=net,
                          bom={"description": "Test pad d1.5 mm, bottom (pogo jig, blueprint row 49)"},
                          in_bom=False, exclude_pos=True))
    for ref in sorted((r for r, v in bp["parts"].items() if v.get("role") == "mounting_hole"), key=_natkey):
        h = bp["parts"][ref]
        parts.append(Part(ref=ref, value="M2.5", symbol="Mechanical:MountingHole", footprint=h["footprint"],
                          at=_xy(h["at"]), block="mech", in_bom=False, exclude_pos=True))
    for h in bp["holes"]:
        if h.get("footprint") is None and (not h["id"].startswith("S1_") or "S1" in used_set):
            parts.append(Part(ref=h["id"], value=f"NPTH d{h['d']}", symbol="", footprint=f"gstudio:NPTH_{h['d']:.1f}mm",
                              at=_xy(h["at"]), block="mech", in_bom=False, board_only=True, exclude_pos=True,
                              bom={"description": h.get("note", "NPTH")}))
    W, H = bp["width"], bp["height"]
    for i, (x, y) in enumerate(((7.5, 1.6), (W - 7.5, 1.6), (7.5, H - 1.6))):
        parts.append(Part(ref=f"FID{i+1}", value="Fiducial", symbol="", footprint="Fiducial:Fiducial_1mm_Mask2mm",
                          at=(x, y), block="mech", in_bom=False, board_only=True, anchor="@board", offset=(x, y)))

    # nets that reach only one pad on this board are left unconnected (no-connect), except power rails
    counts: dict[str, int] = {}
    for p in parts:
        for net in p.pads.values():
            if net:
                counts[net] = counts.get(net, 0) + 1
    for p in parts:
        for pad, net in list(p.pads.items()):
            if net and counts[net] < 2 and net not in POWER_NETS:
                p.pads[pad] = ""

    omitted = [sid for sid in bp["slots"] if sid not in used_set]
    u1 = bp["slots"]["U1"]
    keepouts = []
    if u1.get("antenna_plan"):
        x0, x1, y0, y1 = (float(v) for v in u1["antenna_plan"])
        keepouts.append({"name": "antenna keep-out", "rect": [max(x0, 0.0), y0, min(x1, W), y1]})
    devkit = {"pcb_plan": u1.get("devkit_pcb_plan"), "antenna_plan": u1.get("antenna_plan"),
              "usb_end_x": u1.get("usb_end_x")}
    did = design_id(bp, electrical, meta)
    notes = honest_notes(bp, used, omitted, meta, splits, deviations)
    return Design(name=meta.get("name", "Gadget"), slug=meta.get("slug", "gadget"), revision=meta.get("revision", "A"),
                  date=meta.get("date", ""), engine_version=meta.get("engine_version", ""), backplane_id=bp["id"],
                  backplane_version=bp["version"], width=W, height=H, thickness=bp["thickness"],
                  corner_radius=bp["corner_radius"], parts=parts, keepouts=keepouts, notes=notes, omitted_slots=omitted,
                  used_slots=used, electrical_nets=enets, splits=splits, design_id=did, devkit=devkit,
                  deviations=deviations, waivers=bp["waivers"])


def honest_notes(bp: dict, used: list[str], omitted: list[str], meta: dict, splits: dict, deviations: list[str]) -> list[str]:
    notes = [
        f"Generated by Gadget Studio engine {meta.get('engine_version', '')} from {bp['id']} {bp['version']} for this design only.",
        "Only populated slots and their support parts are placed and routed. Omitted on this board: "
        + (", ".join(omitted) if omitted else "none") + ".",
        "U1 = ESP32-S3-DevKitC-1 in two 1x22 sockets (J_U1A = header J1, J_U1B = header J3); pinout from the Espressif user guide.",
        "Devkit 5V pin is fed from +5V. Espressif's v1.1 schematic puts a Schottky (D1/D7) behind each devkit USB port,",
        "  so +5V cannot back-feed a PC; still, never connect the devkit USB and the rear USB-C at the same time.",
        "Rear USB-C: 5.1k CC sink pull-downs, PTC fuse, TVS, bulk caps; D+/D- through U2 (USBLC6-2SC6) to GPIO20/19.",
        "Amp output: VO+/VO- reach J_SPK through J_A1P/J_A1N (2.54 mm pins fitted in the amp's terminal pads; untested).",
        "Antenna: the devkit module antenna overhangs the board's -X edge; a copper keep-out covers the board strip under it.",
        "ERC/DRC/parity are digital checks only. No board has been fabricated or tested. Physical checks still needed.",
        "BOM part numbers marked 'class' or 'generic' still need an MPN; prices are estimates.",
    ]
    for k, v in sorted(splits.items()):
        notes.append(f"{k} is on {v}: a series part sits between it and the GPIO (backplane-v1 lists one net name for both).")
    for d in deviations:
        notes.append("Deviation: " + d)
    return notes


__all__ = ["Part", "Design", "load_backplane", "build_design", "net_class", "POWER_NETS", "resolve_node",
           "electrical_nets", "slot_pin_pads", "SUPPORT"]
