"""Module library entry -> CAD descriptor.

Structured library fields (dims_mm, pcb_thickness_mm, mount.holes, aperture.size_mm) are used directly. Geometry
that the library records only in its `notes` text (glass size, viewing-area origin, flange thickness, header
pin length...) is transcribed below in LIB_DETAIL with the library id as its source. Anything still missing comes
from refs_provisional and the descriptor is marked `provisional`.

Descriptor frame: u = board width (library x), v = board height (library y, up in front view), board FRONT face at
z = 0 facing out of the gadget (+z), back items at z <= -t.
"""

from __future__ import annotations

import copy
import math

import numpy as np
from typing import Optional

from . import refs_provisional as P

try:
    from . import kits_ext  # pack kits (cad/kits_*.py)
except ImportError:  # pragma: no cover - a vendored gadget_cad copy without the registry (export.VENDOR)
    kits_ext = None

# ------------------------------------------------------------------------------------------------ library notes
# Each value is copied from the named library entry's dims/mount/aperture notes (library/modules-*.json, 2026-11-01).
LIB_DETAIL: dict[str, dict] = {
    "OLED_096_SSD1306_I2C": {
        "kind": "display", "pcb_color": P.PCB_BLUE,
        # glass 26.7 x 19.26; module thickness 2.37 = glass + 1.0 PCB; VA 23.94 x 12.06 at (1.68, 5.86) from the PCB
        # top-left (front view); header pins protrude 7.5 behind; rear component area <= 1.5.
        "glass": {"w": 26.7, "h": 19.26, "t": 1.37, "top_from_top": 3.6},
        "va": {"w": 23.94, "h": 12.06, "x0": 1.68, "y0_from_top": 5.86},
        "header": {"pins": 4, "y_from_top": 1.65, "len_back": 7.5},
        "back_parts_h": 1.5,
    },
    "OLED_130_SH1106_I2C": {
        "kind": "display", "pcb_color": P.PCB_BLUE,
        # module 35.4 x 33.5 x 2.7, PCB 1.2; AA 29.42 x 14.70 at x 2.99..32.41, y 7.35..22.05 from top-left;
        # 11.30 including header. Glass size not given in the library: estimate (provisional) below.
        "glass": {"w": 34.5, "h": 23.0, "t": 1.5, "top_from_top": 4.2, "provisional": True},
        "va": {"w": 29.42 + 2.0, "h": 14.70 + 2.0, "x0": 2.99 - 1.0, "y0_from_top": 7.35 - 1.0},
        "header": {"pins": 4, "y_from_top": 1.6, "len_back": 8.6},
        "back_parts_h": 1.5,
    },
    "LCD_200_ST7789_240X320_WAVESHARE": {
        "kind": "display", "pcb_color": P.PCB_BLUE,
        # outline 58 x 35; LCD + PCB 4.0; AA 40.80 x 30.60 spanning x ~10.4..51.0 from the left, y ~2.0..32.6 from top;
        # backlight 47.83 x 34.60; PH2.0 8-pin socket on the back.
        "glass": {"w": 47.83, "h": 34.6, "t": 2.4, "top_from_top": 0.2, "x0": 6.8},
        "va": {"w": 40.8, "h": 30.6, "x0": 10.4, "y0_from_top": 2.0},
        "header": {"pins": 8, "jst": True, "x_from_left": 5.5, "len_back": 6.0},
        "back_parts_h": 1.6,
    },
    "LED_WS2812B_1PX_MODULE": {"kind": "led", "pcb_color": P.PCB_BLACK, "led": {"size": 5.0, "h": 1.6}},
    "LED_RING_12_WS2812_50MM": {"kind": "led_ring", "pcb_color": P.PCB_BLACK, "ring": {"od": 50.0, "id": 35.0}, "leds": 12},
    "MIC_INMP441_I2S": {
        "kind": "mic", "pcb_color": P.PCB_BLUE,
        # bottom-ported: sound hole through the module PCB; mic package 4.72 x 3.76 x 1; two rows of three pins on
        # opposite edges.
        "port_d": 1.0, "mic_pkg": [4.72, 3.76, 1.0], "header_rows": 2,
    },
    "AMP_MAX98357A_I2S": {"kind": "amp", "pcb_color": P.PCB_BLUE},
    "MICROSD_SPI_MODULE_5V": {"kind": "storage", "pcb_color": P.PCB_BLUE},
    "SPK_40MM_4R_3W": {
        "kind": "speaker",
        # PUI AS04004PR: flange OD 40, flange thickness 1.7, 11.3 to the back of the basket, 17.5 overall incl.
        # magnet; magnet OD 23; baffle opening 35.6.
        "speaker": {"d": 40.0, "flange_t": 1.7, "basket_depth": 11.3, "depth": 17.5, "magnet_d": 23.0, "opening": 35.6},
    },
    "SPK_28MM_8R_2W": {
        "kind": "speaker",
        # Same Sky CMS-28528N: body OD 28, ears 37.6 across, holes 3.0 at +/-16; height 5.2 (2.5 at the rim);
        # magnet OD 16; front OD 26.8; inner front ring 25.2.
        "speaker": {"d": 28.0, "flange_t": 2.5, "basket_depth": 5.2, "depth": 5.2, "magnet_d": 16.0, "opening": 25.2,
                    "ears": {"span": 37.6, "holes": [[-16.0, 0.0], [16.0, 0.0]], "hole_d": 3.0, "w": 8.0}},
    },
    "BUTTON_ARCADE_30MM_SNAPIN": {
        "kind": "snap_button",
        # hole 30, bezel 33, 38 overall; latches for panels about 2-3.9 mm; >= 38 mm free depth behind the panel.
        "snap_button": {"hole_d": 30.0, "panel_t": [2.0, 3.9], "flange_d": 33.0, "flange_h": 3.0, "cap_d": 24.0,
                        "cap_h": 2.0, "body_d": 29.5, "body_len": 33.0, "clip_w": 34.0},
    },
    "BUTTON_TACTILE_KY004": {"kind": "tactile", "pcb_color": P.PCB_BLUE, "switch": [6.0, 6.0, 3.5], "stem_d": 3.5, "stem_h": 1.5},
    "BUZZER_PASSIVE_TRANSISTOR_MODULE": {"kind": "sensor", "pcb_color": P.PCB_BLUE},
    "TOUCH_TTP223_SMALL_15X11": {"kind": "touch", "pcb_color": P.PCB_PURPLE},
    "CORE_ESP32S3_DEVKITC1_N8R8": {
        "kind": "core",
        # PCB 62.865 x 25.40; rows at 1.27 and 24.13 across the width (22.86 apart); pin 1 at 1.565 from the antenna
        # end; WROOM-1 18.0 x 25.5 x 3.1 with the antenna overhanging the PCB end by 6.285 (overall 69.15).
        "devkit": {"pcb_l": 62.865, "pcb_w": 25.4, "row_offset": 1.27, "row_spacing": 22.86, "module": [18.0, 25.5, 3.1],
                   "antenna_overhang": 6.285, "pin1_from_antenna_end": 1.565, "pins": 22},
    },
}

ROLE_KIND = {
    "display": "display", "light": "led", "led": "led", "audio_in": "mic", "mic": "mic", "audio_out": "speaker",
    "speaker": "speaker", "amp": "amp", "storage": "storage", "input": "tactile", "button": "tactile", "touch": "touch",
    "sensor": "sensor", "controller": "core", "core": "core", "actuator": "servo", "servo": "servo",
}


def classify(entry: dict) -> str:
    """Kit kind from the library aperture need + role (merged library roles)."""
    needs = (entry.get("aperture") or {}).get("needs", "none")
    role = entry.get("role", "")
    mount = (entry.get("mount") or {}).get("type")
    if needs == "display_window":
        return "display"
    if needs == "grille":
        return "speaker" if role in ("speaker", "audio_out") else "sensor_window"
    if needs == "button_hole":
        return "snap_button" if mount == "panel_snap" else "tactile"
    if needs == "led_window":
        if role in ("led", "light"):
            return "led_ring" if "ring" in entry.get("id", "").lower() else "led"
        return "sensor_window"
    if needs == "mic_port":
        return "mic"
    if needs in ("touch_zone",) or role == "touch":
        return "touch"
    if needs in ("vent", "sensor_window", "window", "lens"):
        return "sensor_window"
    pk = kits_ext.classify_need(needs) if kits_ext is not None else None  # pack kits: before the role fallback
    if pk:
        return pk
    return ROLE_KIND.get(role, "generic")


def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _prov_kind_for(entry: dict) -> Optional[str]:
    """Find the closest provisional descriptor for a library entry (by id/name keywords, then role)."""
    hay = (entry.get("id", "") + " " + entry.get("name", "")).lower()
    for k, d in P.MODULES.items():
        for m in d.get("match", []):
            if m in hay:
                return k
    role = entry.get("role")
    fallback = {"display": "oled096", "light": "ws2812_mini", "audio_in": "inmp441", "audio_out": "speaker40",
                "amp": "max98357a", "storage": "microsd_spi", "input": "tactile12", "sensor": "sensor_small",
                "controller": "esp32s3_devkitc1", "actuator": "sg90"}
    return fallback.get(role)


def describe(entry: dict) -> dict:
    """Library entry (dict) -> CAD descriptor."""
    mid = entry.get("id", "?")
    det = copy.deepcopy(LIB_DETAIL.get(mid, {}))
    pk = _prov_kind_for(entry)
    prov = copy.deepcopy(P.MODULES.get(pk, {})) if pk else {}
    dims = entry.get("dims_mm") or {}
    kind = det.get("kind") or classify(entry)
    if kind == "led" and "ring" in mid.lower():
        kind = "led_ring"
    d: dict = {
        "id": mid, "name": entry.get("name", mid), "kind": kind, "role": entry.get("role"),
        "catalog_row": entry.get("catalog_row"), "provisional_fields": [], "uncertain": list(entry.get("uncertain") or [])[:6],
        "ref_prefix": entry.get("ref_prefix"),
        "mass_g": _num(entry.get("mass_g"), prov.get("mass_g")),
        "mass_source": "library" if entry.get("mass_g") is not None else "estimate",
    }
    x, y, z = _num(dims.get("x")), _num(dims.get("y")), _num(dims.get("z"))
    t = _num(entry.get("pcb_thickness_mm"))
    ppcb = prov.get("pcb", {})
    face = entry.get("face_mm")
    if face and not x:
        x, y = _num(face[0]), _num(face[1])
    if kind in ("display", "led", "led_ring", "mic", "amp", "storage", "tactile", "sensor", "sensor_window", "touch", "generic", "core"):
        w = x if x else ppcb.get("w", 15.0)
        h = y if y else ppcb.get("h", 15.0)
        if kind == "core":
            dv = det.get("devkit", {})
            w, h = dv.get("pcb_l", w), dv.get("pcb_w", h)
        if not x:
            d["provisional_fields"].append("pcb size")
        if t is None:
            t = ppcb.get("t", 1.6)
            d["provisional_fields"].append("pcb thickness")
        d["pcb"] = {"w": w, "h": h, "t": t, "color": det.get("pcb_color", ppcb.get("color", P.PCB_GREEN))}
        d["envelope_z"] = z
    d["holes"] = [[_num(hh.get("x"), 0), _num(hh.get("y"), 0), _num(hh.get("d"), 2.5)] for hh in (entry.get("mount") or {}).get("holes") or []]
    d["mount_type"] = (entry.get("mount") or {}).get("type")
    ap = (entry.get("aperture") or {})
    d["aperture"] = {"needs": ap.get("needs", "none"), **{k: _num(v, 0.0) for k, v in (ap.get("size_mm") or {}).items()}}
    d["harness"] = {k: v for k, v in (entry.get("backplane_harness") or {}).items() if k != "wires"}
    d["connector_type"] = (entry.get("module_connector") or {}).get("type")

    # ------------------------------------------------------------------ kind-specific geometry
    if kind == "display":
        _display(d, det, prov)
    elif kind in ("led", "led_ring"):
        _led(d, det, prov, kind)
    elif kind == "mic":
        _mic(d, det)
    elif kind == "speaker":
        _speaker(d, det, prov, x, z)
    elif kind == "snap_button":
        sb = det.get("snap_button") or prov.get("snap_button") or P.MODULES["arcade30"]["snap_button"]
        if not det.get("snap_button"):
            d["provisional_fields"].append("button geometry")
        d["snap_button"] = sb
        d["connector"] = {"c": [0, 0], "z": -sb["body_len"] - 1.0, "label": "microswitch terminals"}
    elif kind == "tactile":
        sw = det.get("switch", [6.0, 6.0, 3.5])
        d["switch"] = {"w": sw[0], "h": sw[1], "body_h": sw[2], "stem_d": det.get("stem_d", 3.5), "stem_h": det.get("stem_h", 1.5)}
        d["front"] = [{"name": "Switch", "kind": "box", "c": [0, 0.5], "size": [sw[0], sw[1], sw[2]], "color": P.CHIP},
                      {"name": "Stem", "kind": "cyl", "c": [0, 0.5], "d": d["switch"]["stem_d"], "z0": sw[2], "h": d["switch"]["stem_h"], "color": P.CHIP}]
        d["back"] = [{"name": "Header", "kind": "box", "c": [0, -d["pcb"]["h"] / 2 + 1.5], "size": [7.6, 2.5, 8.5], "color": P.HEADER_BLACK}]
        d["connector"] = {"c": [0, -d["pcb"]["h"] / 2 + 1.5], "z": -d["pcb"]["t"] - 8.5, "label": "3-pin header"}
    elif kind == "core":
        d["devkit"] = det.get("devkit") or {"pcb_l": 62.865, "pcb_w": 25.4, "row_offset": 1.27, "row_spacing": 22.86,
                                            "module": [18.0, 25.5, 3.1], "antenna_overhang": 6.285, "pin1_from_antenna_end": 1.565, "pins": 22}
    elif kind in ("amp", "storage"):
        base = P.MODULES["max98357a" if kind == "amp" else "microsd_spi"]
        d["front"] = copy.deepcopy(base.get("front", []))
        d["back"] = copy.deepcopy(base.get("back", []))
        if kind == "amp":
            d["front"] = [{"name": "Chip", "kind": "box", "c": [0, 0.0], "size": [3.0, 3.0, 0.9], "color": P.CHIP},
                          {"name": "Terminal", "kind": "box", "c": [0, d["pcb"]["h"] / 2 - 3.6], "size": [7.6, 6.5, 8.0], "color": (0.15, 0.45, 0.75)}]
            d["back"] = [{"name": "Pins", "kind": "box", "c": [0, -d["pcb"]["h"] / 2 + 2.54], "size": [17.78, 0.64, 6.0], "color": P.METAL}]
        else:
            d["front"] = [{"name": "Socket", "kind": "box", "c": [d["pcb"]["w"] / 2 - 9.0, 0.0], "size": [15.0, 14.5, 1.9], "color": P.METAL},
                          {"name": "Regulator", "kind": "box", "c": [-6.0, 5.0], "size": [6.5, 3.5, 1.6], "color": P.CHIP},
                          {"name": "Shifter", "kind": "box", "c": [-6.0, -5.0], "size": [5.0, 4.0, 1.4], "color": P.CHIP}]
            d["back"] = [{"name": "Pins", "kind": "box", "c": [-d["pcb"]["w"] / 2 + 1.5, 0], "size": [0.64, 15.24, 6.0], "color": P.METAL}]
    else:
        d.setdefault("front", copy.deepcopy(prov.get("front", [])))
        d.setdefault("back", copy.deepcopy(prov.get("back", [{"name": "Header", "kind": "box", "c": [0, -d.get("pcb", {"h": 12})["h"] / 2 + 1.5],
                                                              "size": [10.2, 2.5, 8.5], "color": P.HEADER_BLACK}])))
        pc = d.get("pcb", {"h": 12, "t": 1.6})
        d.setdefault("connector", {"c": [0, -pc["h"] / 2 + 1.5], "z": -pc["t"] - 8.5, "label": "header"})
        if kind == "sensor_window":
            aw, ah = d["aperture"].get("w") or 4.0, d["aperture"].get("h") or 4.0
            d["window"] = {"w": float(np.clip(aw, 3.0, d["pcb"]["w"] - 2)), "h": float(np.clip(ah, 3.0, d["pcb"]["h"] - 2))}
    if kits_ext is not None and kits_ext.is_pack_kind(kind):  # pack kits (cad/kits_*.py) add their own geometry
        d["dims"] = {"x": x, "y": y, "z": z}
        fn = kits_ext.describe_fn(kind)
        if fn is not None:
            fn(d, entry)
    d["provisional"] = bool(d["provisional_fields"])
    return d


def _header_back(det, pcb):
    hd = det.get("header", {})
    pins = hd.get("pins", 4)
    w = pins * 2.54
    v = pcb["h"] / 2 - hd.get("y_from_top", 1.6)
    u = 0.0
    if "x_from_left" in hd:
        u = -pcb["w"] / 2 + hd["x_from_left"]
    if hd.get("jst"):
        item = {"name": "Socket", "kind": "box", "c": [u, 0.0], "size": [4.5, (pins - 1) * 2.0 + 3.9, hd.get("len_back", 6.0)], "color": P.JST_WHITE}
        return item, [u, 0.0], hd.get("len_back", 6.0)
    item = {"name": "Header", "kind": "box", "c": [u, v], "size": [w, 2.5, hd.get("len_back", 8.5)], "color": P.HEADER_BLACK}
    return item, [u, v], hd.get("len_back", 8.5)


def _display(d, det, prov):
    pcb = d["pcb"]
    g = det.get("glass")
    va = det.get("va")
    if g is None:
        pg = prov.get("glass", {"w": pcb["w"] - 1, "h": pcb["h"] * 0.7, "t": 1.5, "c": [0, 0]})
        g = {"w": pg["w"], "h": pg["h"], "t": pg["t"], "c": pg.get("c", [0, 0])}
        d["provisional_fields"].append("glass size")
    else:
        gx0 = g.get("x0", (pcb["w"] - g["w"]) / 2)
        g = {"w": g["w"], "h": g["h"], "t": g["t"], "c": [-pcb["w"] / 2 + gx0 + g["w"] / 2, pcb["h"] / 2 - g["top_from_top"] - g["h"] / 2],
             "provisional": g.get("provisional", False)}
        if g["provisional"]:
            d["provisional_fields"].append("glass size")
    if va is None:
        a = d["aperture"]
        pa = prov.get("active", {"w": a.get("w", 20), "h": a.get("h", 10), "c": [0, 0]})
        va = {"w": a.get("w") or pa["w"], "h": a.get("h") or pa["h"], "c": pa.get("c", [0, 0])}
    else:
        va = {"w": va["w"], "h": va["h"], "c": [-pcb["w"] / 2 + va["x0"] + va["w"] / 2, pcb["h"] / 2 - va["y0_from_top"] - va["h"] / 2]}
    d["glass"] = g
    d["view"] = va
    item, cpos, clen = _header_back(det, pcb)
    d["back"] = [item, {"name": "Parts", "kind": "box", "c": [0, -2.0], "size": [min(14.0, pcb["w"] - 6), 8.0, det.get("back_parts_h", 1.5)], "color": P.CHIP}]
    d["back_clear_h"] = det.get("back_parts_h", 1.5)
    d["connector"] = {"c": cpos, "z": -pcb["t"] - clen, "label": "header" if not det.get("header", {}).get("jst") else "PH2.0 socket"}


def _led(d, det, prov, kind):
    if kind == "led_ring":
        r = det.get("ring") or prov.get("ring") or {"od": 50.0, "id": 35.0}
        n = det.get("leds", 12)
        d["ring"] = {"od": r["od"], "id": r["id"], "leds": n}
        d["pcb"]["w"] = d["pcb"]["h"] = r["od"]
        rl = (r["od"] + r["id"]) / 4
        d["front"] = [{"name": f"Led{i}", "kind": "box", "c": [rl * math.cos(2 * math.pi * i / n), rl * math.sin(2 * math.pi * i / n)],
                       "size": [5.0, 5.0, 1.6], "color": P.LED_WHITE, "rot": 360.0 * i / n} for i in range(n)]
        d["back"] = [{"name": "Lead", "kind": "box", "c": [0, -rl], "size": [7.0, 3.0, 2.5], "color": P.JST_WHITE}]
        d["connector"] = {"c": [0, -rl], "z": -d["pcb"]["t"] - 2.5, "label": "solder pads + XH 3P pigtail"}
    else:
        led = det.get("led", {"size": 5.0, "h": 1.6})
        d["front"] = [{"name": "Led", "kind": "box", "c": [0, 0], "size": [led["size"], led["size"], led["h"]], "color": P.LED_WHITE}]
        d["back"] = [{"name": "Lead", "kind": "box", "c": [0, -2.0], "size": [6.0, 3.0, 2.5], "color": P.JST_WHITE}]
        d["connector"] = {"c": [0, -2.0], "z": -d["pcb"]["t"] - 2.5, "label": "solder pads + XH 3P pigtail"}
        d["led"] = led


def _mic(d, det):
    pcb = d["pcb"]
    pk = det.get("mic_pkg", [4.72, 3.76, 1.0])
    d["port"] = {"c": [0.0, 0.0], "d": det.get("port_d", 1.0)}
    d["back"] = [{"name": "Mic", "kind": "box", "c": [0, 0], "size": [pk[0], pk[1], pk[2]], "color": P.METAL},
                 {"name": "PinsA", "kind": "box", "c": [-pcb["w"] / 2 + 1.5, 0], "size": [2.5, 7.62, 8.5], "color": P.HEADER_BLACK},
                 {"name": "PinsB", "kind": "box", "c": [pcb["w"] / 2 - 1.5, 0], "size": [2.5, 7.62, 8.5], "color": P.HEADER_BLACK}]
    d["back_clear_h"] = pk[2]
    d["connector"] = {"c": [pcb["w"] / 2 - 1.5, 0], "z": -pcb["t"] - 8.5, "label": "2 x 3 header"}


def _speaker(d, det, prov, x, z):
    s = det.get("speaker")
    if s is None:
        ps = prov.get("speaker", P.MODULES["speaker40"]["speaker"])
        dd = x or ps["d"]
        s = {"d": dd, "flange_t": ps.get("rim_t", 1.5), "basket_depth": (z or ps["depth"]) * 0.65, "depth": z or ps["depth"],
             "magnet_d": ps["magnet_d"], "opening": dd * 0.88}
        d["provisional_fields"].append("speaker profile")
    d["speaker"] = s
    d["connector"] = {"c": [s["d"] / 2 - 4.0, 0.0], "z": -s["basket_depth"], "label": "solder eyelets + 2-wire lead"}


# ------------------------------------------------------------------------------------------------ fasteners
PURCHASED_INSERT = "INSERT_HEATSET_M2_5X4_MEC8005"  # normalize.PURCHASED_INSERT (the BOM row)
PURCHASED_FEET = "FEET_RUBBER_8X2_5_SUNROM_4436"  # normalize.PURCHASED_FEET (the BOM row)


def fasteners(lib_get, hw_modules: dict | None = None) -> dict:
    """Fastener/foot/tie geometry, from the library when present (ids per library B), else provisional."""
    f = copy.deepcopy(P.FASTENERS)
    src = {}
    # REVIEW-1 MAJ-12 (integration): the insert and feet the BOM buys are the ones the CAD models. The spec's hardware
    # list (normalize) names them; the library defaults below follow normalize's PURCHASED_* choices.
    hw = dict(hw_modules or {})
    f["hw_modules"] = {k: v for k, v in hw.items() if k and v}  # CAD group -> purchased library row (PURCHASE_<group>.module_id)
    ins_id = next((i for i in (hw.get("HeatSetInserts"), PURCHASED_INSERT, "INSERT_HEATSET_M2_5X4_STD") if i and lib_get(i)), None)
    ins = lib_get(ins_id) if ins_id else None
    if ins:
        dm = ins.get("dims_mm", {})
        hole = ((ins.get("mount") or {}).get("holes") or [{}])[0].get("d", 4.0)
        f["insert"] = {"thread": "M2.5", "od": float(dm.get("x", 4.6)), "length": float(dm.get("z", 4.0)), "hole_d": float(hole),
                       "hole_depth": float(dm.get("z", 4.0)) + 1.0, "boss_d": max(7.2, float(hole) + 3.2), "module": ins_id}
        src["insert"] = (f"library {ins_id} (hole {float(hole):g} mm from the library row"
                         + (", an estimate" if "ESTIMATE" in str(((ins.get("mount") or {}).get("holes") or [{}])[0].get("basis", "")) else "")
                         + "; hole depth = L + 1 mm melt room, design estimate)")
    ft_id = next((i for i in (hw.get("Feet"), PURCHASED_FEET, "FEET_BUMPON_SJ5302_CLEAR") if i and lib_get(i)), None)
    ft = lib_get(ft_id) if ft_id else None
    if ft:
        dm = ft.get("dims_mm", {})
        dd, hh = float(dm.get("x", 7.9)), float(dm.get("z", 2.2))
        f["foot"] = {"d": dd, "h": hh, "seat_d": dd + 0.8, "seat_depth": 0.6,
                     "hemispherical": "hemisph" in str(ft.get("name", "") + dm.get("notes", "")).lower(), "module": ft_id}
        src["foot"] = f"library {ft_id} (seat recess size is a design estimate)"
    sc = lib_get("SCREW_SET_M2_M2_5_SHCS_A2")
    if sc:
        f["screw"] = {"d": 2.5, "clear_d": 2.8, "head_d": 4.5, "head_h": 2.5, "lengths": [5, 8, 10, 12, 16, 20, 25, 30]}
        src["screw"] = "library SCREW_SET_M2_M2_5_SHCS_A2 (head size estimated there)"
    tie = lib_get("STRAIN_TIE_NYLON66_100X2_5")
    if tie:
        f["tie"] = {"w": 2.5, "t": 1.0, "slot_w": 3.6, "slot_h": 1.8}
        src["tie"] = "library STRAIN_TIE_NYLON66_100X2_5 (slot size is a design estimate)"
    gk = lib_get("SPK_GASKET_FOAM_25X3_ADH")
    f["gasket"] = {"t": 3.0 if gk else 1.0, "compressed_t": 2.0 if gk else 0.8}
    if gk:
        src["gasket"] = "library SPK_GASKET_FOAM_25X3_ADH (3 mm foam, modeled compressed to 2.0 mm: estimate)"
    f["sources"] = src
    return f


def pick_screw(clamp_t: float, lengths, hole_depth_min: float, min_engage: float = 3.0) -> tuple[float, float]:
    """Shortest stocked screw that engages >= min_engage mm past the clamped part; the insert hole is deepened to
    tip + 0.5 mm when needed so the screw never bottoms out. Returns (length, hole_depth)."""
    for L in sorted(lengths):
        pen = L - clamp_t
        if pen >= min_engage - 1e-9:
            return float(L), float(max(hole_depth_min, pen + 0.5))
    L = float(max(lengths))
    return L, float(max(hole_depth_min, L - clamp_t + 0.5))


def screw_length(need: float, lengths) -> float:
    """Shortest stocked length >= need (falls back to the longest)."""
    for L in sorted(lengths):
        if L >= need - 1e-6:
            return float(L)
    return float(max(lengths))
