#!/usr/bin/env python3
"""Merge library/modules-a.json + modules-b.json -> library/modules.json and library/README.md (foundation).

Run: python3 -I merge_modules.py      (pure Python; no deps)

What the merge does
  * validates: JSON array, unique ids, required keys present;
  * dedupes: identical ids collapse (first wins, the other is reported);
  * normalises: one key set for every object (missing keys added empty), canonical `role` from a fixed vocabulary
    (the library agent's coarse role is kept as `category`), `ref_prefix`, `face_mm`, `depth_mm`,
    `harness_clearance_mm`, `electrical.supply_rail`, `electrical.i2c_addr`, `electrical.current_basis`;
  * binds every module to backplane-v1 (`backplane_v1`: port type, allowed slots, cable, supported/why) and rewrites
    `backplane_harness` to that scheme; the library agent's original proposal is kept as `backplane_harness_library`;
  * makes `aperture` consistent with the role (`aperture.feature_allowed`, `aperture.required`);
  * keeps `evidence` and `uncertain` verbatim (merge notes go to `merge_notes`, never into `uncertain`).

Packs (CATALOG-PLAN 0.5): every `library/packs/<name>.json` ({"pack": name, "rows": [...], "prompt_rules": [...],
"known_harness_gaps": [...]}; `enabled.json` and the `examples` folder are skipped) adds rows after the three sources.
A pack row carries its own `merge` block instead of the tables below: {role, prefix, placement "feature"|"internal"|
"none", designable, port {port, slots, cable, supported, why}, prewire, unconnected [module pins left open on purpose]}.
The merged row keeps `merge` and gets `"pack": name`; a generated harness row used only by pack rows gets the pack(s)
too, so `gstudio.library.Library` hides it with its pack. A pack row never replaces an existing id: a duplicate id, an
unknown slot, a cable that does not match its port, a role the port does not accept or a module pin that maps to no
port pin stops the merge.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
SRC = [LIB / "modules-a.json", LIB / "modules-b.json", LIB / "modules-foundation.json"]
OUT = LIB / "modules.json"
README = LIB / "README.md"
BACKPLANE = LIB / "backplane-v1.json"
PACKS = LIB / "packs"
PLACEMENTS = ("feature", "internal", "none")

KEYS = ["id", "catalog_row", "catalog_id", "role", "name", "reference_product", "dims_mm", "pcb_thickness_mm", "mount", "pins",
        "module_connector", "backplane_harness", "aperture", "electrical", "firmware", "kicad", "sourcing_in",
        "price_inr_planning", "evidence", "uncertain"]

# canonical role per id (explicit: the library's coarse roles mix buttons, encoders and end-stops under "input")
ROLE = {
    "CORE_ESP32S3_DEVKITC1_N8R8": "core", "BACKPLANE_S3_V0_CUSTOM": "backplane",
    "USBC_RECEPTACLE_GCT_USB4105": "power_in", "USBC_BREAKOUT_ADAFRUIT_4090": "power_in",
    "ADAPTER_RPI_15W_USBC_IN": "adapter", "CABLE_USBC_C2C_1M": "usb_cable",
    "CABLE_JSTPH_4P_300MM": "harness", "CABLE_JSTPH_3P_300MM": "harness",
    "OLED_096_SSD1306_I2C": "display", "OLED_130_SH1106_I2C": "display", "LCD_200_ST7789_240X320_WAVESHARE": "display",
    "LED_WS2812B_1PX_MODULE": "led", "LED_RING_12_WS2812_50MM": "led", "MIC_INMP441_I2S": "mic", "AMP_MAX98357A_I2S": "amp",
    "SPK_40MM_4R_3W": "speaker", "SPK_28MM_8R_2W": "speaker", "BUZZER_PASSIVE_TRANSISTOR_MODULE": "buzzer",
    "BUTTON_TACTILE_KY004": "button", "BUTTON_ARCADE_30MM_SNAPIN": "button", "MICROSD_SPI_MODULE_5V": "storage",
    "STRAIN_TIE_NYLON66_100X2_5": "ties", "SCREW_SET_M2_M2_5_SHCS_A2": "screws", "INSERT_HEATSET_M2_5X4_STD": "inserts",
    "INSERT_HEATSET_M3X5_7_STD": "inserts", "FEET_BUMPON_SJ5302_CLEAR": "feet", "CORE_CARTRIDGE_FRAME_V0": "cartridge",
    "SPK_GASKET_FOAM_25X3_ADH": "gasket", "DIFFUSER_PRINTED_NATURAL_PETG": "diffuser", "ENCODER_KY040_PUSH": "encoder",
    "TOUCH_TTP223_SMALL_15X11": "touch", "LIGHT_BH1750_GY302_I2C": "light_sensor", "LIGHT_LDR_LM393_4PIN": "light_sensor",
    "TEMPHUM_AHT20_ADAFRUIT_4566": "temp_humidity", "BARO_BMP280_GY_3V3_I2C": "pressure", "TOF_VL53L0X_POLOLU_2490": "distance",
    "PIR_AM312_MINI": "motion", "PIR_HCSR501_STD": "motion", "IMU_MPU6050_GY521": "imu", "HALL_KY003_A3144": "hall",
    "RTC_DS3231_ZS042": "rtc", "SERVO_SG90_TOWERPRO": "servo", "GEARMOTOR_N20_6V_LOWRPM": "motor",
    "MOTOR_DRV_DRV8833_POLOLU_2130": "motor_driver", "ENDSTOP_OMRON_SS01GL": "endstop", "FAN_3010_5V_GUARDED": "fan",
    "SERVO_BRACKET_SG90_ACRYLIC": "servo_bracket", "SERVO_HORN_KIT_SG90_SUPPLIED": "servo_horn",
    "MAGNET_NDFEB_D8X5_N35": "magnet", "SHELL_PANEL_FASTENED_V0": "panel", "TEST_JIG_TAGCONNECT_TC2030_NL": "test_interface",
    "LABEL_QR_POLYESTER_50X25_PACK": "label",
    # foundation rows (modules-foundation.json): REVIEW-1 BLK-5 card, MAJ-12 per-length screws and the purchased insert
    "STORAGE_MICROSD_32GB_A1": "storage_card", "SCREW_M2_5X5_SHCS_A2": "screws", "SCREW_M2_5X8_SHCS_A2": "screws",
    "SCREW_M2_5X12_SHCS_A2": "screws", "SCREW_M2X6_SHCS_A2": "screws", "INSERT_HEATSET_M2_5X4_MEC8005": "inserts",
    "FEET_RUBBER_8X2_5_SUNROM_4436": "feet",  # integration: Indian retail feet (REVIEW-1 MIN-25)
}
RENAME = {"BACKPLANE_S3_V0_CUSTOM": "BACKPLANE_V1"}

PREFIX = {"core": "U", "backplane": "BP", "power_in": "PWR", "adapter": "PSU", "usb_cable": "CBL", "harness": "W",
          "display": "DISP", "led": "LED", "mic": "MIC", "amp": "AMP", "speaker": "SPK", "buzzer": "BZ", "button": "BTN",
          "storage": "SD", "storage_card": "CARD", "encoder": "ENC", "touch": "TCH", "light_sensor": "ALS", "temp_humidity": "ENV", "pressure": "BARO",
          "distance": "TOF", "motion": "PIR", "imu": "IMU", "hall": "HAL", "rtc": "RTC", "servo": "SRV", "motor": "M",
          "motor_driver": "DRV", "endstop": "SW", "fan": "FAN"}

# roles that sit behind an aperture on a face (may be a spec feature) vs internal-only
FEATURE_ROLES = {"display", "led", "mic", "speaker", "buzzer", "button", "encoder", "touch", "light_sensor", "temp_humidity",
                 "pressure", "distance", "motion", "fan"}
INTERNAL_ROLES = {"core", "backplane", "power_in", "amp", "storage", "imu", "hall", "rtc", "servo", "motor", "motor_driver",
                  "endstop"}

# backplane-v1 binding: (port type, slots, cable family/pins, supported, why)
IO3 = ["J_IO1", "J_IO2", "J_IO3"]
I2C3 = ["J_I2C1", "J_I2C2", "J_I2C3"]


def port_for(m: dict, role: str) -> dict:
    mid = m["id"]
    bus = str((m.get("electrical") or {}).get("bus") or "").lower()
    if role == "core":
        return {"port": "core", "slots": ["U1"], "cable": None, "supported": True}
    if role == "backplane":
        return {"port": "backplane", "slots": [], "cable": None, "supported": True}
    if mid == "USBC_RECEPTACLE_GCT_USB4105":
        return {"port": "on_board", "slots": ["J_PWR"], "cable": None, "supported": True,
                "why": "soldered on the backplane as J_PWR (backplane-v1 uses the HRO TYPE-C-31-M-12 footprint; GCT USB4105 is the alternate)"}
    if mid == "USBC_BREAKOUT_ADAFRUIT_4090":
        return {"port": "none", "slots": [], "cable": None, "supported": False,
                "why": "fallback breakout; backplane-v1 has the receptacle on the board"}
    if role in ("adapter", "usb_cable"):
        return {"port": "in_box", "slots": [], "cable": None, "supported": True}
    if role == "amp":
        return {"port": "socket", "slots": ["A1"], "cable": None, "supported": True}
    if role == "storage":
        return {"port": "socket", "slots": ["S1"], "cable": None, "supported": True}
    if role == "storage_card":
        return {"port": "card_slot", "slots": [], "cable": None, "supported": True, "why": "goes in the S1 microSD module's card slot"}
    if role == "speaker":
        return {"port": "speaker", "slots": ["J_SPK"], "cable": {"family": "JST-PH 2.0", "pins": 2}, "supported": True}
    if role == "mic":
        return {"port": "i2s_mic", "slots": ["J_MIC"], "cable": {"family": "JST-PH 2.0", "pins": 6}, "supported": True}
    if role == "led":
        return {"port": "led", "slots": ["J_LED"], "cable": {"family": "JST-PH 2.0", "pins": 3}, "supported": True}
    if role == "servo":
        return {"port": "led", "slots": ["J_LED"], "cable": {"family": "JST-PH 2.0", "pins": 3}, "supported": True,
                "why": "J_LED is the only 5 V port: 5 V supply and a 5 V PWM signal from the buffered GPIO2. A design can have an "
                       "addressable LED or a servo, not both. Needs a PH-3 to servo-plug adapter."}
    if role == "display":
        if "i2c" in bus:
            return {"port": "i2c", "slots": I2C3, "cable": {"family": "JST-PH 2.0", "pins": 4}, "supported": True}
        return {"port": "spi_display", "slots": ["J_DISP"], "cable": {"family": "JST-PH 2.0", "pins": 8}, "supported": True}
    if role in ("light_sensor", "temp_humidity", "pressure", "distance", "imu", "rtc") and "i2c" in bus:
        return {"port": "i2c", "slots": I2C3, "cable": {"family": "JST-PH 2.0", "pins": 4}, "supported": True}
    if mid == "PIR_HCSR501_STD":
        return {"port": "none", "slots": [], "cable": None, "supported": False,
                "why": "needs >= 4.5 V supply; backplane-v1 IO ports supply 3.3 V only. Use PIR_AM312_MINI (2.7-12 V)."}
    if mid == "HALL_KY003_A3144":
        return {"port": "io", "slots": IO3, "cable": {"family": "JST-PH 2.0", "pins": 3}, "supported": True,
                "why": "3.3 V port only, so its on-board pull-up goes to 3.3 V and never puts 5 V on a GPIO. The A3144 is "
                       "specified from 4.5 V: switching at 3.3 V is not guaranteed (physical check still needed)."}
    if role in ("button", "touch", "buzzer", "motion", "light_sensor", "endstop"):
        return {"port": "io", "slots": IO3, "cable": {"family": "JST-PH 2.0", "pins": 3}, "supported": True}
    if role == "encoder":
        return {"port": "none", "slots": [], "cable": None, "supported": False,
                "why": "needs 3 GPIOs on one 5-pin cable; backplane-v1 has no free 3-signal port (proposed for backplane-v2)"}
    if role in ("motor", "motor_driver", "fan"):
        return {"port": "none", "slots": [], "cable": None, "supported": False,
                "why": "needs a switched 5 V output or 4 control GPIOs; not on backplane-v1 (P2 rows)"}
    if role == "harness":
        return {"port": "harness", "slots": list(m.get("slots") or []), "cable": None, "supported": True,
                **({"proposed_row": True} if m.get("proposed_row") else {})}
    return {"port": "none", "slots": [], "cable": None, "supported": True, "why": "mechanical or consumable; no electrical port"}


def i2c_addrs(bus: str) -> tuple[list[str], list[str]]:
    used, alt = [], []
    for part in re.split(r"\(", bus):
        hexes = re.findall(r"0x[0-9A-Fa-f]{2}", part)
        if part.lower().startswith("alt"):
            alt += hexes
        else:
            used += hexes
    norm = lambda h: "0x" + h[2:].upper()
    return [norm(h) for h in used], [norm(h) for h in alt]


def supply_rail(m: dict, role: str, pv1: dict) -> str | None:
    slots = pv1.get("slots") or []
    if not slots or role in ("core", "backplane"):
        return "+5V" if role == "core" else None
    if slots[0] in ("A1", "S1", "J_LED"):
        return "+5V"
    if slots[0] in ("J_SPK", "J_PWR"):
        return None
    return "+3V3"


FACE_OVERRIDE = {"PIR_AM312_MINI": ((12.0, 12.0), 27.0, "dims_mm x is the axial length (lens to pin ends)"),
                 "SPK_GASKET_FOAM_25X3_ADH": (None, None, "consumable strip")}


# ---------------------------------------------------------------------------------------------- factory pre-wiring
_LEAD_NOTE = ("The factory solders a {fam} {n}-pin lead (stock: {stock}_<len> with the module-end housing cut off; "
              "length = the design's harness length class, 100 mm default) to the module before assembly. It plugs into {slot}. "
              "The maker never solders.")
PREWIRE = {
    "SPK_40MM_4R_3W": {"by": "factory", "lead": "JST-PH 2.0 2p 100 mm", "pads": ["+ eyelet", "- eyelet"], "slot": "J_SPK",
                       "note": _LEAD_NOTE.format(fam="JST-PH", n=2, stock="CABLE_JSTPH_2P", slot="J_SPK")},
    "SPK_28MM_8R_2W": {"by": "factory", "lead": "JST-PH 2.0 2p 100 mm", "pads": ["+", "-"], "slot": "J_SPK",
                       "note": _LEAD_NOTE.format(fam="JST-PH", n=2, stock="CABLE_JSTPH_2P", slot="J_SPK")},
    # J_LED is JST-XH 2.5 (REVIEW-1 MAJ-3): the LED lead is an XHP-3 lead, so it cannot mate a 3V3 PH IO port
    "LED_WS2812B_1PX_MODULE": {"by": "factory", "lead": "JST-XH 2.5 3p 100 mm", "pads": ["GND", "5V", "DIN"], "slot": "J_LED",
                               "note": _LEAD_NOTE.format(fam="JST-XH", n=3, stock="CABLE_JSTXH_3P", slot="J_LED") + " DOUT stays unconnected."},
    "LED_RING_12_WS2812_50MM": {"by": "factory", "lead": "JST-XH 2.5 3p 100 mm", "pads": ["GND", "5V", "DI"], "slot": "J_LED",
                                "note": _LEAD_NOTE.format(fam="JST-XH", n=3, stock="CABLE_JSTXH_3P", slot="J_LED") + " DO stays unconnected."},
    "AMP_MAX98357A_I2S": {"by": "factory", "lead": None, "pads": ["7-pin header (pins down)", "VO+ pin", "VO- pin"], "slot": "A1",
                          "note": "The factory solders the 7-pin header pins-down and two 2.54 mm pins into the VO+/VO- terminal pads "
                                  "(instead of the screw terminal) so the amp plugs straight into A1. The maker never solders."},
}
# module-connector corrections taken from the module's own library text (REVIEW-1 BLK-2); the source files stay verbatim
CONNECTOR_FIX = {
    "BUTTON_ARCADE_30MM_SNAPIN": {
        "type": "spade_2.8",
        "type_basis": "library row 24 pins[].notes: 'microswitch common, 2.8 mm (.110 in) spade on Sanwa-type switches' and "
                      "location 'microswitch spade terminals at the back' (was 'wire_leads')"},
    "MIC_INMP441_I2S": {
        "rows": [["L/R", "WS", "SCK"], ["SD", "VDD", "GND"]],
        "rows_basis": "REVIEW-1 BLK-2 fix: two rows of three pins on opposite edges (library row 20, Components101), housing A "
                      "L/R, WS, SCK and housing B SD, VDD, GND. Physical positions still to confirm on a sample (physical check)."},
}
# real pre-wired listings found 2026-11-01; flagged when they are not the same physical part as the reference
EXTRA_SOURCING = {
    "SPK_40MM_4R_3W": [{
        "vendor": "Pimoroni (UK)", "url": "https://shop.pimoroni.com/products/mini-speaker-4-3w-with-jst-ph-connector",
        "price_inr_seen": None, "seen_on": "2026-11-01", "status": "page_read", "variant": "prewired_jst_ph", "same_part": False,
        "gst": None, "pack_size": None,
        "notes": "COM1606 'Mini Speaker 4 ohm (3W) with JST-PH Connector': 40x40x20 mm per the seller (the PUI reference is "
                 "40 mm OD, 17.5 mm overall), about 80 mm of 20 AWG lead with a 2-pin JST-PH plug. Price not shown on the page "
                 "(a search snippet said GBP 2.50); the page shows 'Notify me'. Ships from the UK; no Indian seller found. "
                 "Different body: needs its own module entry and a measured sample before the CAD can use it."}],
}


# ---------------------------------------------------------------------------------------------- foundation harness cables
# REVIEW-1 BLK-2: every design harness row names ONE module-end termination family and its exact position table.
#   backplane end = the port's own housing (PHR-n on JST-PH ports, XHP-n on the JST-XH J_LED port)
#   module end    = jst_ph | jst_xh | dupont_2.54 | spade_2.8, with housings[{housing, positions[{pos, port_pin, module_pin,
#                   wire_colour}]}]
# Rows are generated from library/backplane-v1.json ports (base rows, same housing both ends) and from the modules that bind
# to those ports (Dupont and spade module ends, one row per distinct position table). None has a retail listing for the
# exact double-ended harness, so each carries a `factory_make` entry (made by our factory to its drawing; sku = drawing).
HARNESS_LENGTHS = (100, 150, 200, 300)
CABLE_FAMILIES = ("jst_ph", "jst_xh", "dupont_2.54", "spade_2.8")
FAMILY_OF_CONNECTOR = {"jst_ph_2.0": "jst_ph", "jst_ph": "jst_ph", "jst_xh_2.5": "jst_xh", "jst_xh": "jst_xh",
                       "header_2.54": "dupont_2.54", "dupont_2.54": "dupont_2.54", "spade_2.8": "spade_2.8"}
PORT_FAMILY = {"JST-PH 2.0": "jst_ph", "JST-XH 2.5": "jst_xh"}
FAM_CODE = {"jst_ph": "JSTPH", "jst_xh": "JSTXH"}
FAM_LABEL = {"jst_ph": "JST-PH 2.0", "jst_xh": "JST-XH 2.5"}
PITCH_MM = {"jst_ph": 2.0, "jst_xh": 2.5}
HOUSING = {"jst_ph": "PHR-{n} (JST PH 2.0 mm)", "jst_xh": "XHP-{n} (JST XH 2.5 mm)",
           "dupont_2.54": "Dupont 1x{n} female housing (2.54 mm)", "spade_2.8": "2.8 mm (0.110 in) female insulated spade"}
CRIMP = {"jst_ph": "JST SPH-002T-P0.5S-class crimp (or compatible)", "jst_xh": "JST SXH-001T-P0.6-class crimp (or compatible)",
         "dupont_2.54": "Dupont 2.54 mm female crimp terminal", "spade_2.8": "2.8 mm female insulated spade crimp"}
# single-ended PH pigtails in the library (open tinned other end): factory stock for Dupont / spade module ends
PIGTAIL = {("jst_ph", 3): "CABLE_JSTPH_3P_300MM", ("jst_ph", 4): "CABLE_JSTPH_4P_300MM"}
# wire colours by port net: the same scheme as gstudio.electrical (black GND, red 5V, orange 3V3, fixed signal colours)
POWER_COLOUR = {"GND": "black", "+5V": "red", "+3V3": "orange"}
FIXED_COLOUR = {"I2C_SDA": "blue", "I2C_SCL": "yellow", "SPI_SCK": "yellow", "SPI_MOSI": "blue", "DISP_CS": "white",
                "DISP_DC": "green", "DISP_RST": "purple", "DISP_BL": "grey", "I2S_MIC_SCK": "yellow", "I2S_MIC_WS": "green",
                "I2S_MIC_SD": "blue", "LED_DIN_5V": "green", "IO1_SIG": "white", "IO2_SIG": "white", "IO3_SIG": "white",
                "SPK_P": "white", "SPK_N": "grey"}
CATALOG = {("jst_ph", 2): ("06b", None), ("jst_ph", 3): ("07", "CABLE_3PIN_A"), ("jst_xh", 3): ("07", None),
           ("jst_ph", 4): ("06", "CABLE_4PIN_A"), ("jst_ph", 6): ("06c", None), ("jst_ph", 8): ("06d", None)}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9/+\-]", "", str(s).upper())


def wire_colour(sp: dict) -> str:
    net = sp.get("net")
    if sp.get("kind") == "strap":
        return "black"
    if sp.get("kind") == "power" or net == "GND":
        return POWER_COLOUR.get(net, "red")
    return FIXED_COLOUR.get(net, "white")


def port_groups(bp: dict) -> list[dict]:
    """backplane-v1 ports grouped by (family, pin count, pin names): one base harness per group."""
    groups: dict[tuple, dict] = {}
    for s in bp["slots"]:
        if s.get("kind") != "port":
            continue
        fam = PORT_FAMILY[s["connector"]]
        key = (fam, s["pins_count"], tuple(p["name"] for p in s["pins"]))
        g = groups.setdefault(key, {"family": fam, "n": s["pins_count"], "slots": [], "pins": s["pins"]})
        g["slots"].append(s["id"])
    return list(groups.values())


def map_wires(m: dict, slot: dict) -> dict:
    """module pin -> slot pin, by name (slot pin name + `accepts`), the same greedy rule as gstudio.electrical."""
    mc = m.get("module_connector") or {}
    order = list(mc.get("pin_order") or [p.get("name") for p in (m.get("pins") or []) if p.get("name")])
    lib_wires = [w.get("module_pin") for w in (m.get("backplane_harness") or {}).get("wires", []) if w.get("module_pin")]
    out: dict[str, dict] = {}
    taken: set = set()
    for mp in (lib_wires or order):
        for sp in slot["pins"]:
            if sp["pin"] in taken:
                continue
            if _norm(mp) in [_norm(sp["name"])] + [_norm(a) for a in sp.get("accepts", [])]:
                taken.add(sp["pin"])
                out[mp] = sp
                break
    for sp in slot["pins"]:
        if sp["pin"] in taken or sp.get("kind") != "strap":
            continue
        for mp in order:
            if mp not in out and _norm(mp) in [_norm(a) for a in sp.get("accepts", [])]:
                out[mp] = sp
    return out


def module_end_layout(m: dict, slot: dict) -> tuple[str | None, list[list[tuple]]]:
    """(family, housings) for a module on a port; each housing is [(pos, module_pin|None, slot_pin dict|None)] in physical
    order. Dupont / PH / XH: one housing per header row, spanning the used pins; spade: one terminal per used pin."""
    mc = m.get("module_connector") or {}
    fam = FAMILY_OF_CONNECTOR.get(str(mc.get("type") or ""))
    wires = map_wires(m, slot)
    order = list(mc.get("pin_order") or [p.get("name") for p in (m.get("pins") or []) if p.get("name")])
    housings: list[list[tuple]] = []
    if fam == "spade_2.8":
        for mp in order:
            if mp in wires:
                housings.append([(1, mp, wires[mp])])
        return fam, housings
    for row in (mc.get("rows") or [order]):
        idx = [i for i, mp in enumerate(row) if mp in wires]
        if not idx:
            continue
        lo, hi = min(idx), max(idx)
        housings.append([(k - lo + 1, row[k] if row[k] in wires else None, wires.get(row[k])) for k in range(lo, hi + 1)])
    return fam, housings


def _positions(h: list[tuple], use_port_names: bool = False) -> list[dict]:
    out = []
    for pos, mp, sp in h:
        if sp is None:
            out.append({"pos": pos, "port_pin": None, "port_pin_name": None, "net": None, "module_pin": mp, "wire_colour": None,
                        "empty": True})
        else:
            out.append({"pos": pos, "port_pin": sp["pin"], "port_pin_name": sp["name"], "net": sp.get("net"),
                        "module_pin": sp["name"] if use_port_names else mp, "wire_colour": wire_colour(sp)})
    return out


def _perm(housings: list[list[dict]]) -> list[int]:
    return [p["port_pin"] or 0 for h in housings for p in h["positions"]]


def _cable_row(*, cid, name, fam_port, n, slots, port_pins, L, mod_family, housings, pinning, drawing, fits, row, cat_id,
               extra_uncertain=()) -> dict:
    flat = [p["port_pin"] for h in housings for p in h["positions"] if p.get("port_pin")]
    crossed = flat != sorted(flat)
    made = pinning == "drawing"
    pig = PIGTAIL.get((fam_port, n)) if mod_family in ("dupont_2.54", "spade_2.8") else None
    port_housing = HOUSING[fam_port].format(n=n)
    mod_desc = "; ".join(h["housing"] for h in housings)
    steps = ([f"take {pig} (single-ended {FAM_LABEL[fam_port]} {n}-pin pigtail, open tinned other end) and cut to {L} mm"] if pig else
             [f"cut {sum(1 for h in housings for p in h['positions'] if p.get('port_pin'))} wires to {L} mm and crimp the {port_housing} end"])
    steps += [f"crimp the module end ({CRIMP[mod_family]}) and seat each wire at the housing position in the table",
              "100% continuity and pin-map test against this drawing before packing"]
    return {
        "id": cid, "catalog_row": row, "catalog_id": cat_id, "role": "cable", "name": name,
        "reference_product": {"maker": "Gadget Studio factory (made to drawing)", "mpn_or_model": f"{drawing} (our harness drawing), {L} mm",
                              "product_url": None, "datasheet_url": None},
        "dims_mm": {"x": L, "y": round(PITCH_MM[fam_port] * (n - 1) + 3.9, 2), "z": 4.5,
                    "notes": "x = wire length (length class); y = backplane-end housing width pitch x (pins - 1) + 3.9 (estimate, same "
                             "rule as library rows 06/07); z = housing height estimate"},
        "pcb_thickness_mm": None, "mount": {"type": "none", "holes": [], "hole_origin": "n/a", "notes": "tie to printed anchors"},
        "pins": [{"name": p["name"], "func": "wire"} for p in port_pins],
        "module_connector": {"type": "jst_xh_2.5" if fam_port == "jst_xh" else "jst_ph_2.0", "pin_order": [p["name"] for p in port_pins],
                             "location": f"backplane end: {port_housing}; module end: {mod_desc}"},
        "backplane_end": {"family": fam_port, "housing": port_housing, "slots": list(slots),
                          "positions": [{"pos": p["pin"], "port_pin_name": p["name"]} for p in port_pins]},
        "module_end": {"family": mod_family, "housings": housings},
        "pinning": pinning, "made_to_drawing": made, "crossed": crossed, "straight_through": not made,
        "drawing": drawing, "sku": f"{drawing}-{L}", "sku_kind": "drawing", "length_mm": L, "fits": sorted(fits),
        "design_harness": True,
        "backplane_harness": {"backplane_connector": f"{FAM_LABEL[fam_port]} {n}p ({'/'.join(slots)})", "wires": [], "length_mm": L},
        "aperture": {"needs": "none", "size_mm": {"w": 0, "h": 0, "d": 0}, "notes": "internal"},
        "electrical": {"supply_v": "n/a", "logic_v": "n/a", "current_ma_typ": 0, "current_ma_max": 0, "bus": "n/a (harness)"},
        "firmware": {"arduino_libraries": [], "driver_notes": ""},
        "kicad": {"backplane_footprint": "NONE", "notes": f"backplane end mates the slot header ({'/'.join(slots)})"},
        "sourcing_in": [],
        "factory_make": {
            "by": "factory", "drawing": drawing, "made_to_drawing": made, "crossed": crossed,
            "backplane_end": {"housing": port_housing, "crimp": CRIMP[fam_port]},
            "module_end": {"family": mod_family, "housings": [h["housing"] for h in housings], "crimp": CRIMP[mod_family]},
            "stock": pig, "steps": steps,
            "test": "continuity and pin-map test of every unit against the drawing's position table",
            "price_basis": "blueprint §3.3 rows 06/07 planning range 30-100 INR per harness (estimate; no harness quote yet)",
        },
        "price_inr_planning": [30, 100],
        "evidence": ["blueprint §3.3 rows 06 (keyed 4-pin module cable) and 07 (keyed 3-pin actuator cable): planning range 30-100 INR",
                     "backplane-end pin order from library/backplane-v1.json slot pins; module-end positions from the fitting modules' "
                     "module_connector.pin_order (or rows) mapped by pin name"],
        "uncertain": (["pin count not in the blueprint catalog: proposed row " + row + "; planning price borrowed from rows 06/07"]
                      if cat_id is None and row != "07" else [])
                     + ["no retail listing for this exact double-ended harness: made by our factory to the drawing; price is the "
                        "planning range only", "housing dimensions are estimates; wire gauge not chosen"] + list(extra_uncertain),
        "slots": list(slots), "proposed_row": cat_id is None and fam_port == "jst_ph" and n in (2, 6, 8),
    }


def _fits_base(m: dict, g: dict, slot_of: dict) -> bool:
    """True when a module on one of the group's ports uses the base row: factory pre-wired, or the same housing family as
    the port with a straight position table."""
    pv1 = m.get("backplane_v1") or {}
    if not pv1.get("supported") or not set(pv1.get("slots") or []) & set(g["slots"]) or not pv1.get("cable"):
        return False
    if m.get("prewire"):
        return True
    fam, hs = module_end_layout(m, slot_of[g["slots"][0]])
    return fam == g["family"] and len(hs) == 1 and [p[2]["pin"] if p[2] else 0 for p in hs[0]] == list(range(1, g["n"] + 1))


def foundation_cables(mods: list[dict], bp: dict, notes: list[str]) -> list[dict]:
    """Base harnesses (same housing both ends, straight-through) for every port group, the straight Dupont 1xN rows for the
    3- and 4-pin PH ports, and one row per distinct module-end position table needed by a library module."""
    out: dict[str, dict] = {}
    groups = port_groups(bp)
    by_slot = {sid: g for g in groups for sid in g["slots"]}
    slot_of = {s["id"]: s for s in bp["slots"]}

    def add(row):
        if row["id"] in out:
            raise SystemExit(f"cable id clash {row['id']}")
        out[row["id"]] = row

    variants: dict[tuple, dict] = {}  # (family, n, port names, kind, layout) -> {id stem, housings, fits}
    for m in mods:
        pv1 = m.get("backplane_v1") or {}
        if not pv1.get("supported") or not pv1.get("cable") or m.get("prewire"):
            continue
        slot = slot_of[pv1["slots"][0]]
        g = by_slot[slot["id"]]
        fam, hs = module_end_layout(m, slot)
        if fam is None or not hs:
            notes.append(f"{m['id']}: module connector {(m.get('module_connector') or {}).get('type')!r} has no harness family; "
                         "electrical falls back to the base harness made to drawing")
            continue
        housings = [{"housing": HOUSING[fam].format(n=len(h)), "positions": _positions(h)} for h in hs]
        perm = _perm(housings)
        straight = len(housings) == 1 and perm == list(range(1, g["n"] + 1))
        if fam == g["family"] and straight:
            continue  # base row fits
        if fam == g["family"]:
            kind = "X"
        elif fam == "spade_2.8":
            kind = f"SPADE28"
        elif len(housings) == 1:
            kind = f"DUPONT1X{len(hs[0])}"
        else:
            kind = "DUPONT" + "_".join(f"1X{len(h)}" for h in hs) if len({len(h) for h in hs}) > 1 else f"DUPONT{len(hs)}X1X{len(hs[0])}"
        if fam == "dupont_2.54" and straight:
            continue  # the straight Dupont row below fits
        key = (g["family"], g["n"], tuple(sorted(g["slots"])), kind, tuple(perm))
        v = variants.setdefault(key, {"g": g, "fam": fam, "kind": kind, "perm": perm, "hs": hs, "fits": [], "first": m})
        v["fits"].append(m["id"])
    # base rows + straight Dupont rows
    for g in groups:
        fam, n = g["family"], g["n"]
        row, cat_id = CATALOG[(fam, n)]
        code = FAM_CODE[fam]
        base_h = [{"housing": HOUSING[fam].format(n=n), "positions": _positions([(p["pin"], p["name"], p) for p in g["pins"]], True)}]
        fits = [m["id"] for m in mods if _fits_base(m, g, slot_of)]
        for L in HARNESS_LENGTHS:
            add(_cable_row(cid=f"CABLE_{code}_{n}P_{L}", name=f"{FAM_LABEL[fam]} {n}-pin harness, {HOUSING[fam].format(n=n).split(' (')[0]} both ends, {L} mm",
                           fam_port=fam, n=n, slots=g["slots"], port_pins=g["pins"], L=L, mod_family=fam, housings=base_h,
                           pinning="straight_through", drawing=f"GS-H-{code[3:]}{n}-{code[3:]}{n}", fits=fits, row=row, cat_id=cat_id,
                           extra_uncertain=["factory pre-wired modules use this row with the module-end housing cut off; the "
                                            "factory solders the wires to the module pads (see the module's prewire)"]))
        if fam == "jst_ph" and n in (3, 4):
            dh = [{"housing": HOUSING["dupont_2.54"].format(n=n), "positions": _positions([(p["pin"], p["name"], p) for p in g["pins"]], True)}]
            for L in HARNESS_LENGTHS:
                add(_cable_row(cid=f"CABLE_{code}_{n}P_DUPONT1X{n}_{L}",
                               name=f"JST-PH 2.0 {n}-pin to Dupont 1x{n} female harness, {L} mm, straight-through",
                               fam_port=fam, n=n, slots=g["slots"], port_pins=g["pins"], L=L, mod_family="dupont_2.54", housings=dh,
                               pinning="straight_through", drawing=f"GS-H-PH{n}-D1X{n}", fits=[], row=row, cat_id=cat_id))
    stems: set = set()
    for v in variants.values():
        g, fam, kind, perm = v["g"], v["fam"], v["kind"], v["perm"]
        n, code = g["n"], FAM_CODE[g["family"]]
        housings = [{"housing": HOUSING[fam].format(n=len(h)), "positions": _positions(h)} for h in v["hs"]]
        row, cat_id = CATALOG[(g["family"], n)]
        ptxt = ",".join(str(p or "-") for p in perm)
        stem = f"CABLE_{code}_{n}P_{kind}"
        always = kind.startswith("DUPONT1X") or kind == "X"  # a crossed single housing: the position table is in the id
        suffix = f"_X{''.join(str(p) for p in perm)}" if (always or stem in stems) else ""
        stems.add(stem)
        drawing = f"GS-H-{code[3:]}{n}-{kind.replace('DUPONT', 'D').replace('SPADE28', 'SP28')}" + (suffix.replace("_", "-") if suffix else "")
        if fam == "spade_2.8":
            what = f"{len(housings)} x 2.8 mm female spade"
        elif len(housings) == 1:
            what = f"Dupont 1x{len(housings[0]['positions'])} female"
        elif len({len(h["positions"]) for h in housings}) == 1:
            what = f"{len(housings)} x Dupont 1x{len(housings[0]['positions'])} female"
        else:
            what = " + ".join(f"Dupont 1x{len(h['positions'])}" for h in housings) + " female"
        extra = []
        if v["first"]["id"] == "MIC_INMP441_I2S":
            extra.append((v["first"]["module_connector"] or {}).get("rows_basis", ""))
        for L in HARNESS_LENGTHS:
            add(_cable_row(cid=f"{stem}_{L}{suffix}",
                           name=f"{FAM_LABEL[g['family']]} {n}-pin to {what} harness, {L} mm, made to drawing {drawing} "
                                f"(module-end positions = port pins {ptxt})",
                           fam_port=g["family"], n=n, slots=g["slots"], port_pins=g["pins"], L=L, mod_family=fam, housings=housings,
                           pinning="drawing", drawing=drawing, fits=v["fits"], row=row, cat_id=cat_id, extra_uncertain=extra))
    return list(out.values())


# ---------------------------------------------------------------------------------------------- sourcing (REVIEW-1 MAJ-24, MIN-25)
UPDATES = LIB / "sourcing-updates.json"
REFS_INDEX = LIB / "references" / "index.json"   # curated, dated corrections and new listings (each names its snapshot); sources stay verbatim
# vendor -> (domains its URLs must be on, fulfilment country, retail?)
VENDORS = {
    "electropi": (("electropi.in",), "IN", True), "robocraze": (("robocraze.com",), "IN", True),
    "robo bazar": (("robobazar.in",), "IN", True), "robu": (("robu.in",), "IN", True), "hubtronics": (("hubtronics.in",), "IN", True),
    "digikey india": (("digikey.in",), "US", True), "digikey": (("digikey.in", "digikey.com"), "US", True), "thinkrobotics": (("thinkrobotics.com",), "IN", True),
    "fabtolab": (("fabtolab.com",), "IN", True), "probots": (("probots.co.in",), "IN", True), "evelta": (("evelta.com",), "IN", True),
    "quartz components": (("quartzcomponents.com",), "IN", True), "amazon.in": (("amazon.in",), "IN", True),
    "indiamart": (("indiamart.com",), "IN", False), "thingbits": (("thingbits.in",), "IN", True), "ibots": (("ibots.in",), "IN", True),
    "rytronics": (("rytronics.in",), "IN", True), "kitsguru": (("kitsguru.com",), "IN", True), "robocomp": (("robocomp.in",), "IN", True),
    "pimoroni": (("pimoroni.com",), "GB", True), "robodo": (("robodo.in",), "IN", True), "rees52": (("rees52.com",), "IN", True),
    "dna technology": (("dnatechindia.com",), "IN", True), "zbotic": (("zbotic.in",), "IN", True),
    "sunrom": (("sunrom.com",), "IN", True),
    "industrybuying": (("industrybuying.com",), "IN", True), "moglix": (("moglix.com",), "IN", True),
    "electronicscomp": (("electronicscomp.com",), "IN", True),
}
SKU_RES = [re.compile(r"\b(PRO\d{3,5}|TH\d{4}|SBC\d{3,5}|TIFEC\d{3,5}|COM\d{4})\b"), re.compile(r"\bSKU[:\s]+([A-Z0-9][A-Za-z0-9-]{3,})"),
           re.compile(r"\bRobu SKU (R\d{4,8})\b")]
OOS_RE = re.compile(r"out of stock|sold out|backorder|notify me|out-of-stock", re.I)
INS_RE = re.compile(r"\bin stock\b|\d+ in stock", re.I)


def vendor_key(v: str) -> str | None:
    low = str(v or "").lower()
    return next((k for k in VENDORS if low.startswith(k)), None)


def host_of(url: str | None) -> str:
    h = re.sub(r"^https?://", "", str(url or "")).split("/")[0].lower()
    return re.sub(r"^(www|m|shop)\.", "", h)


def load_updates() -> dict:
    return json.loads(UPDATES.read_text(encoding="utf-8")) if UPDATES.exists() else {"updates": [], "add": [], "drop": []}


def _match(row: dict, mt: dict) -> bool:
    return all((str(row.get(k) or "").lower().startswith(str(v).lower()) if k == "vendor" else str(v) in str(row.get(k) or ""))
               for k, v in mt.items())


def apply_updates(m: dict, upd: dict, notes: list[str]) -> None:
    rows = m.setdefault("sourcing_in", [])
    for d in upd.get("drop", []):
        if d["module"] == m["id"]:
            keep = [r for r in rows if not _match(r, d["match"])]
            if len(keep) != len(rows):
                m.setdefault("merge_notes", []).append(f"sourcing row {d['match']} dropped: {d['why']}")
            rows[:] = keep
    for u in upd.get("updates", []):
        if u["module"] != m["id"]:
            continue
        hits = [r for r in rows if _match(r, u["match"])]
        if not hits:
            notes.append(f"sourcing update for {m['id']} {u['match']} matched no row")
        for r in hits:
            r.update(u["set"])
    for a in upd.get("add", []):
        if a["module"] == m["id"]:
            rows.append(dict(a["row"]))
    for f in upd.get("module_fields", []):
        if f["module"] == m["id"]:
            for k, v in f["set"].items():
                m.setdefault("merge_notes", []).append(f"{k} set by sourcing-updates.json: {f.get('why', '')}")
                m[k] = v


def sourcing_pass(m: dict) -> list[str]:
    """Backfill sku / stock / checked_at / fulfilment_country / retail from each row's own text, lint URL host vs vendor,
    and mark the chosen row with a structured selection_reason. Returns lint issues."""
    issues = []
    rp = m.get("reference_product") or {}
    for r in m.get("sourcing_in") or []:
        txt = f"{r.get('notes') or ''}"
        if not r.get("sku"):
            sku, kind = None, None
            for rx in SKU_RES:
                mm = rx.search(txt)
                if mm:
                    sku, kind = mm.group(1), "vendor_sku"
                    break
            if sku is None and "digikey" in host_of(r.get("url")):
                parts = str(r.get("url")).rstrip("/").split("/")
                if len(parts) >= 3 and "detail" in parts:
                    sku, kind = parts[parts.index("detail") + 2], "mpn"
            if sku is None and r.get("url") and r.get("url") == rp.get("product_url"):
                mm = SKU_RES[0].search(str(rp.get("mpn_or_model") or ""))
                if mm:
                    sku, kind = mm.group(1), "vendor_sku"
            if sku:
                r["sku"], r["sku_kind"], r["sku_basis"] = sku, kind, "parsed from the row's notes/url (library text)"
            else:
                r["sku"] = None
        if not r.get("stock"):
            if re.search(r"stock state unclear", txt, re.I):
                r["stock"] = "unknown"
            elif OOS_RE.search(txt):
                r["stock"] = "out_of_stock"
            elif INS_RE.search(txt):
                r["stock"] = "in_stock"
            else:
                r["stock"] = "unknown"
            r.setdefault("stock_basis", "parsed from the row's notes" if r["stock"] != "unknown" else "not recorded")
        if not r.get("checked_at"):
            r["checked_at"] = r.get("seen_on")  # ISO 8601 date; time of day not recorded by the library agents
            r["checked_at_precision"] = "date"
        vk = vendor_key(r.get("vendor"))
        if vk is None:
            issues.append(f"{m['id']}: unknown vendor {r.get('vendor')!r} (add it to VENDORS)")
            r.setdefault("fulfilment_country", None)
            continue
        doms, country, retail = VENDORS[vk]
        r.setdefault("fulfilment_country", country)
        r["retail"] = retail
        if country != "IN":
            r.setdefault("import_risk", f"ships from {country}: customs duty, courier and lead time are not in the quote")
        h = host_of(r.get("url"))
        r["host_ok"] = bool(h) and any(h == d or h.endswith("." + d) for d in doms)
        if r.get("url") and not r["host_ok"]:
            issues.append(f"{m['id']}: {r.get('vendor')} row links to {h}, not {'/'.join(doms)}")
        if re.match(r"\s*DIFFERENT\b", txt):
            r.setdefault("same_part", False)
    rows = m.get("sourcing_in") or []
    if rows:
        def price(r):
            v = r.get("price_inr_seen")
            return float(v) if isinstance(v, (int, float)) else None

        def score(r):
            return (not r.get("host_ok", True), r.get("same_part") is False, not r.get("retail", True), not r.get("url"),
                    r.get("stock") == "out_of_stock", str(r.get("status")) != "page_read", price(r) is None,
                    r.get("fulfilment_country") != "IN")
        ranked = sorted(range(len(rows)), key=lambda i: (score(rows[i]), i))
        best = rows[ranked[0]]
        for r in rows:
            r.pop("selected", None)
        best["selected"] = True
        others = [rows[i] for i in ranked[1:]]
        why = []
        if best.get("same_part") is not False and any(o.get("same_part") is False for o in others):
            why.append("same part as the reference (others are different parts)")
        if best.get("stock") != "out_of_stock" and any(o.get("stock") == "out_of_stock" for o in others):
            why.append("in stock or stock unknown where others are out of stock")
        if str(best.get("status")) == "page_read" and any(str(o.get("status")) != "page_read" for o in others):
            why.append("price read on the vendor's page (others: search snippets)")
        if best.get("fulfilment_country") == "IN" and any(o.get("fulfilment_country") != "IN" for o in others):
            why.append("ships from India")
        if not why:
            why.append("library order (the library agent's preference); no ranking rule separates the rows" if others else "only listing")
        best["selection_reason"] = {"rule": "host matches vendor > same part > retail > has URL > not out of stock > page read > "
                                            "price seen > ships from IN > library order",
                                    "why": why, "alternatives": len(others)}
        if best.get("fulfilment_country") not in (None, "IN"):
            best["selection_reason"]["why"].append(f"no Indian listing of the same part: {best.get('import_risk')}")
    return issues


def finish_cable(m: dict) -> None:
    """Module-end data for the non-design cable rows: single-ended PH pigtails (factory stock) and the in-box USB-C cable."""
    if m.get("design_harness"):
        return
    if m.get("role") == "harness":
        m["design_harness"] = False
        m["module_end"] = {"family": "bare_wire", "housings": [],
                           "note": "open tinned end: factory stock for made-to-drawing harnesses, never a design harness"}
        sel = next((r for r in m.get("sourcing_in") or [] if r.get("selected")), None)
        m["sku"] = (sel or {}).get("sku")
    elif m.get("role") == "usb_cable":
        m["module_end"] = {"family": "usb_c", "housings": [], "note": "USB-C plug both ends; ships in the box"}
        sel = next((r for r in m.get("sourcing_in") or [] if r.get("selected")), None)
        m["sku"] = (sel or {}).get("sku")


def backplane_row_from_json(m: dict, bp: dict) -> None:
    """REVIEW-1 MIN-24: the BACKPLANE_V1 row is regenerated from library/backplane-v1.json (pins, buses, holes, power,
    evidence, uncertain); the library agent's PROPOSED allocation (BACKPLANE_S3_V0_CUSTOM) is dropped, not kept."""
    pins = []
    for sl in bp["slots"]:
        if sl["kind"] not in ("port", "module_socket", "power_in"):
            continue
        for pn in sl["pins"]:
            pins.append({"name": f"{sl['id']}.{pn['name']}", "slot": sl["id"], "pin": pn["pin"], "net": pn.get("net"),
                         "gpio": pn.get("gpio") if pn.get("gpio") is not None else pn.get("source_gpio"),
                         "func": pn.get("kind", "signal"), **({"via": pn["via"]} if pn.get("via") else {})})
    m["pins"] = pins
    buses = []
    for b in bp["buses"]:
        if b["type"] == "gpio":
            buses.append("gpio (" + ", ".join(f"{k} GPIO{v}" for k, v in b["slots"].items()) + ")")
        else:
            gp = [f"{k} {v}" for k, v in b.items() if k in ("sda", "scl", "sck", "mosi", "miso", "bclk", "ws", "dout", "din", "enable", "gpio", "dp", "dn")]
            buses.append(f"{b['type']} ({', '.join(gp)})")
    pw = bp["power"]
    m["electrical"] = {"supply_v": "5 (USB-C VBUS)", "logic_v": "3.3", "current_ma_typ": 0.0, "current_ma_max": float(pw["limits"]["limit_5v_ma"]),
                       "bus": " | ".join(buses), "supply_rail": None, "current_basis": "library",
                       "power": {"rails": pw["rails"], "devkit_isolation": pw["devkit_isolation"]["decision"],
                                 "limits": pw["limits"]}}
    m["mount"] = {"type": "holes", "holes": [{"x": h["at"][0], "y": h["at"][1], "d": h["d"]} for h in bp["board"]["holes"]],
                  "hole_origin": "board lower-left corner, top view (library/backplane-v1.json frame)",
                  "notes": f"{len(bp['cartridge']['holes'])} x M2.5 on printed bosses with heat-set inserts; "
                           f"board top {bp['cartridge']['board_top_above_floor']} mm above the inner floor"}
    m["reference_product"] = {"maker": "Gadget Studio (own design)", "mpn_or_model": f"{bp['id']} {bp['version']} (library/backplane-v1.json)",
                              "product_url": None, "datasheet_url": None}
    m["evidence"] = list(bp["evidence"])
    m["uncertain"] = list(bp["uncertain"])
    m["module_connector"] = {"type": "none", "pin_order": [], "location": "ports and sockets per library/backplane-v1.json slots"}
    m.setdefault("merge_notes", []).append("pins, electrical, mount, evidence and uncertain regenerated from backplane-v1.json "
                                           "(REVIEW-1 MIN-24); the proposed BACKPLANE_S3_V0_CUSTOM allocation is not kept")


BRAND = re.compile(r"\bQuarktex\b")


def fix_branding(m: dict) -> None:
    """No company branding inside the product data: our own parts are 'Gadget Studio' parts."""
    rp = m.get("reference_product")
    if isinstance(rp, dict) and BRAND.search(str(rp.get("maker") or "")):
        rp["maker"] = BRAND.sub("Gadget Studio", rp["maker"])
        m.setdefault("merge_notes", []).append("reference_product.maker: company name replaced by 'Gadget Studio' (no branding in product data)")


# ---------------------------------------------------------------------------------------------- packs (CATALOG-PLAN 0.5)
def load_packs(packs_dir: Path = PACKS) -> list[tuple[str, dict]]:
    """[(name, data)] for every packs/<name>.json, sorted by file name; enabled.json and sub-folders are skipped."""
    out = []
    if not packs_dir.is_dir():
        return out
    for f in sorted(packs_dir.glob("*.json")):
        if f.name == "enabled.json" or not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            raise SystemExit(f"packs/{f.name}: a pack file is {{\"pack\": \"{f.stem}\", \"rows\": [...], \"prompt_rules\": [...]}}")
        name = data.get("pack") or f.stem
        if name != f.stem:
            raise SystemExit(f"packs/{f.name}: \"pack\" is {name!r}; it must equal the file name {f.stem!r}")
        if name == "enabled" or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise SystemExit(f"packs/{f.name}: pack names are lower-case words ([a-z][a-z0-9_]*), not 'enabled'")
        out.append((name, data))
    return out


def check_pack_row(m: dict, bp: dict) -> list[str]:
    """Hard errors for one pack row's `merge` block against backplane-v1 (CATALOG-PLAN risk 2: no silent library errors)."""
    mid, mg = m.get("id"), m.get("merge")
    if not isinstance(mg, dict):
        return [f"{mid}: a pack row needs a merge block {{role, prefix, placement, designable, port, prewire}}"]
    errs = []
    role = mg.get("role")
    if not isinstance(role, str) or not role:
        errs.append(f"{mid}: merge.role missing")
    if not isinstance(mg.get("prefix"), str) or not re.fullmatch(r"[A-Z]{1,5}", mg.get("prefix") or ""):
        errs.append(f"{mid}: merge.prefix must be 1-5 capital letters (instance refs are <prefix><n>)")
    if mg.get("placement") not in PLACEMENTS:
        errs.append(f"{mid}: merge.placement must be one of {', '.join(PLACEMENTS)}")
    if not isinstance(mg.get("designable", False), bool):
        errs.append(f"{mid}: merge.designable must be true or false")
    if mg.get("designable") and mg.get("placement") == "none":
        errs.append(f"{mid}: a designable row is placed as a feature or internal, not 'none'")
    port = mg.get("port")
    if not isinstance(port, dict) or not {"port", "slots", "cable", "supported"} <= set(port):
        return errs + [f"{mid}: merge.port must be {{port, slots, cable, supported, why}}"]
    slots = {s["id"]: s for s in bp["slots"]}
    pslots = port.get("slots") or []
    if not isinstance(pslots, list):
        errs.append(f"{mid}: merge.port.slots must be a list")
        pslots = []
    for sid in pslots:
        if sid not in slots:
            errs.append(f"{mid}: merge.port.slots names {sid}, which backplane-v1 does not have")
    cable = port.get("cable")
    if port.get("supported") and cable:
        for sid in [s for s in pslots if s in slots]:
            s = slots[sid]
            if s.get("kind") != "port":
                errs.append(f"{mid}: {sid} is a {s.get('kind')}, not a cabled port")
                continue
            if cable.get("family") != s.get("connector") or int(cable.get("pins") or 0) != int(s.get("pins_count") or 0):
                errs.append(f"{mid}: cable {cable.get('family')} {cable.get('pins')}p does not match {sid} "
                            f"({s.get('connector')} {s.get('pins_count')}p)")
            if role and role not in (s.get("accepts_roles") or []):
                errs.append(f"{mid}: {sid} does not accept role {role!r} (accepts {s.get('accepts_roles')}); the role list is in "
                            "tools/gen_backplane_v1.py (owner E: send a hand-off)")
        if pslots and pslots[0] in slots and slots[pslots[0]].get("kind") == "port":
            wires = map_wires(m, slots[pslots[0]])
            want = [w.get("module_pin") for w in (m.get("backplane_harness") or {}).get("wires", []) if w.get("module_pin")]
            want = want or list((m.get("module_connector") or {}).get("pin_order") or [p.get("name") for p in m.get("pins") or []
                                                                                        if p.get("name")])
            if not want:
                errs.append(f"{mid}: a cabled row needs module_connector.pin_order or pins[] names")
            open_ok = set(mg.get("unconnected") or [])
            lost = [p for p in want if p not in wires and p not in open_ok]
            if lost:
                errs.append(f"{mid}: module pin(s) {lost} map to no pin of {pslots[0]} (names and 'accepts' aliases in "
                            "backplane-v1.json); rename them, ask E for an alias, or list them in merge.unconnected")
    elif port.get("supported") and pslots:
        errs.append(f"{mid}: a supported port with slots needs merge.port.cable (or slots [] for parts with no wire)")
    if not port.get("supported") and not port.get("why"):
        errs.append(f"{mid}: an unsupported port needs merge.port.why")
    fm = mg.get("face_mm")
    if fm is not None and not (isinstance(fm, list) and len(fm) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in fm)):
        errs.append(f"{mid}: merge.face_mm is [w, h] in mm (the in-shell envelope of a part that sits outside)")
    dm = mg.get("depth_mm")
    if dm is not None and not (isinstance(dm, (int, float)) and dm >= 0):
        errs.append(f"{mid}: merge.depth_mm is a number >= 0")
    pw = mg.get("prewire")
    if pw is not None and (not isinstance(pw, dict) or not {"by", "lead", "pads", "slot", "note"} <= set(pw)):
        errs.append(f"{mid}: merge.prewire is null or {{by, lead, pads, slot, note}}")
    if mg.get("placement") == "feature" and (m.get("aperture") or {}).get("needs", "none") == "none":
        errs.append(f"{mid}: a feature row needs aperture.needs (the opening the CAD cuts)")
    if not isinstance(m.get("catalog_row"), str) or not m.get("catalog_row"):
        errs.append(f"{mid}: catalog_row must be a non-empty string (e.g. \"P-<pack>-1\")")
    return errs


def merge() -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    rows: list[dict] = []
    seen: dict[str, str] = {}
    for f in SRC:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"{f} is not a JSON array")
        for m in data:
            missing = [k for k in KEYS if k not in m]
            if missing:
                notes.append(f"{m.get('id')}: missing keys {missing} (added empty)")
            if m["id"] in seen:
                notes.append(f"duplicate id {m['id']} in {f.name} (kept the one from {seen[m['id']]})")
                continue
            seen[m["id"]] = f.name
            m = dict(m)
            m["source_file"] = f.name
            rows.append(m)
    bp = json.loads(BACKPLANE.read_text(encoding="utf-8"))
    # packs come after the three sources, so every core row and generated cable id stays as it was
    pack_errs: list[str] = []
    taken = {x.upper() for x in list(seen) + list(RENAME.values())}  # Library.get() also resolves ids case-insensitively
    for name, data in load_packs():
        for m in data["rows"]:
            mid = m.get("id") if isinstance(m, dict) else None
            if not mid:
                pack_errs.append(f"packs/{name}.json: a row without an id")
                continue
            if mid.upper() in taken:
                pack_errs.append(f"packs/{name}.json: id {mid} already exists ({seen.get(mid, 'a core row')}); "
                                 "a pack row never replaces a row")
                continue
            pack_errs += [f"packs/{name}.json: {e}" for e in check_pack_row(m, bp)]
            missing = [k for k in KEYS if k not in m]
            if missing:
                notes.append(f"{mid} (pack {name}): missing keys {missing} (added empty)")
            taken.add(mid.upper())
            seen[mid] = f"packs/{name}.json"
            m = dict(m)
            m["source_file"] = f"packs/{name}.json"
            m["pack"] = name
            rows.append(m)
    if pack_errs:
        raise SystemExit("pack rows rejected:\n  - " + "\n  - ".join(pack_errs))
    upd = load_updates()
    lint: list[str] = []
    out = []
    pending = list(rows)
    cables_done = False
    while pending:
        m = pending.pop(0)
        fix_branding(m)
        if m["id"] in CONNECTOR_FIX:
            mc = dict(m.get("module_connector") or {})
            fx = CONNECTOR_FIX[m["id"]]
            if "type" in fx and mc.get("type") != fx["type"]:
                m.setdefault("merge_notes", []).append(f"module_connector.type {mc.get('type')!r} -> {fx['type']!r}: {fx['type_basis']}")
            mc.update(fx)
            m["module_connector"] = mc
        oid = m["id"]
        mg = m.get("merge") if m.get("pack") else None  # a pack row brings its own role, prefix, placement, port and prewire
        role = mg["role"] if mg else ROLE.get(oid)
        if role is None:
            raise SystemExit(f"no canonical role for {oid}: add it to ROLE")
        for k in KEYS:
            m.setdefault(k, [] if k in ("pins", "sourcing_in", "evidence", "uncertain") else {})
        m["category"] = m.get("role")
        m["role"] = role
        if oid in RENAME:
            m["aliases"] = [oid]
            m["id"] = RENAME[oid]
        else:
            m["aliases"] = []
        m["ref_prefix"] = mg["prefix"] if mg else PREFIX.get(role)
        # geometry convenience
        d = m.get("dims_mm") or {}
        x, y, z = d.get("x"), d.get("y"), d.get("z")
        face, depth = ((x, y) if x is not None and y is not None else None), z
        if oid in FACE_OVERRIDE:
            fo, do, why = FACE_OVERRIDE[oid]
            face, depth = fo, do
            m.setdefault("merge_notes", []).append(f"face_mm/depth_mm overridden: {why}")
        if mg and (mg.get("face_mm") or mg.get("depth_mm") is not None):
            # a pack row whose part sits outside the shell (cable exit): face/depth = the in-shell envelope (c-to-e 1)
            face = tuple(mg["face_mm"]) if mg.get("face_mm") else face
            depth = mg["depth_mm"] if mg.get("depth_mm") is not None else depth
            m.setdefault("merge_notes", []).append("face_mm/depth_mm from the pack row's merge block (in-shell envelope)")
        m["face_mm"] = list(face) if face else None
        m["depth_mm"] = depth
        mc = (m.get("module_connector") or {}).get("type", "")
        m["harness_clearance_mm"] = 10.0 if mc in ("header_2.54", "wire_leads", "jst_ph_2.0", "jst_xh_2.5", "solder_pads", "spade_2.8") else 0.0
        # requires_soldering = the person following BUILD_GUIDE must solder (coordinator 2026-11-01: we ship built devices).
        # Factory work is recorded separately: `prewire` (leads and pins fitted before assembly) and `factory_soldering`.
        prewire = (mg.get("prewire") if mg else PREWIRE.get(m["id"]))
        if prewire:
            m["prewire"] = prewire
            m["requires_soldering"], m["factory_soldering"] = False, True
            m["requires_soldering_basis"] = "the factory pre-wires it (see prewire); the maker never solders"
            for row in EXTRA_SOURCING.get(m["id"], []):
                m.setdefault("sourcing_in", []).append(dict(row))
        elif role in ("power_in", "test_interface", "backplane") and m["id"] != "USBC_BREAKOUT_ADAFRUIT_4090":
            m["requires_soldering"], m["factory_soldering"] = False, True
            m["requires_soldering_basis"] = "soldered during backplane PCB assembly, not by the maker"
        # soldering: only from explicit library text (pipeline request 2026-11-01); otherwise null = unknown
        mc_loc = " ".join(str((m.get("module_connector") or {}).get(k, "")) for k in ("type", "location")).lower()
        mount_n = str((m.get("mount") or {}).get("notes", "")).lower()
        if "prewire" in m or m.get("factory_soldering"):
            pass
        elif mc == "solder_pads" or "solder 2 leads" in mc_loc or "solder" in mc_loc:
            m["requires_soldering"], m["requires_soldering_basis"] = True, "module connector is solder pads/eyelets (library text)"
        elif mc in ("jst_ph_2.0", "jst_xh_2.5") or role in ("harness", "usb_cable", "adapter") or "spade" in mc_loc:
            m["requires_soldering"], m["requires_soldering_basis"] = False, "plug-in connector or crimped spade (library text)"
        elif role in ("screws", "inserts", "feet", "ties", "gasket", "label", "magnet", "diffuser", "cartridge", "panel", "storage_card",
                      "servo_bracket", "servo_horn", "backplane", "power_in", "test_interface"):
            m["requires_soldering"], m["requires_soldering_basis"] = False, "mechanical/consumable or PCB-assembled part"
        else:
            m["requires_soldering"], m["requires_soldering_basis"] = None, "unknown: the library does not say whether the header comes fitted"

        apply_updates(m, upd, notes)
        for row in m.get("sourcing_in") or []:
            txt = (str(row.get("notes", "")) + " " + str(row.get("status", ""))).lower()
            if re.search(r"incl\.?\s*(18%\s*)?gst|inclusive of gst|including gst|incl gst", txt):
                row.setdefault("gst", "incl")
            elif re.search(r"excl\.?\s*(18%\s*)?gst|exclusive of gst|excluding gst|\+\s*gst", txt):
                row.setdefault("gst", "excl")
            else:
                row.setdefault("gst", None)
            mm = re.search(r"pack of (\d+)|(\d+)\s*(?:pcs|pieces|pc)\b", txt + " " + str(row.get("name", "")).lower())
            row.setdefault("pack_size", int(mm.group(1) or mm.group(2)) if mm else None)
        lint += sourcing_pass(m)
        finish_cable(m)
        # backplane binding
        pv1 = {k: v for k, v in mg["port"].items()} if mg else port_for(m, role)
        m["backplane_v1"] = pv1
        # electrical normalisation
        e = dict(m.get("electrical") or {})
        e["supply_rail"] = supply_rail(m, role, pv1)
        used, alt = i2c_addrs(str(e.get("bus") or ""))
        if used or alt or pv1.get("port") == "i2c":
            e["i2c_addr"] = used
            e["i2c_addr_alt"] = alt
        unc = " ".join(map(str, m.get("uncertain") or [])).lower()
        # conservative: any mention of current in `uncertain` labels the figures "estimate"
        e["current_basis"] = "estimate" if ("current" in unc or "ma" in unc.split()) else ("library" if (e.get("current_ma_max") or 0) > 0 else "none")
        for k in ("current_ma_typ", "current_ma_max"):
            v = e.get(k)
            e[k] = float(v) if isinstance(v, (int, float)) else 0.0
        m["electrical"] = e
        # aperture consistency
        a = dict(m.get("aperture") or {})
        a.setdefault("needs", "none")
        is_feature = (mg.get("placement") == "feature") if mg else role in FEATURE_ROLES
        a["feature_allowed"] = is_feature
        a["required"] = is_feature
        if role == "touch" and a.get("needs") == "none":
            a["needs"] = "touch_zone"
            a.setdefault("merge_note", "touch pads read through a thin non-metallic wall; wall thickness to be test-printed")
        if is_feature and a.get("needs") == "none":
            notes.append(f"{m['id']}: feature role {role} but aperture.needs == none")
        m["aperture"] = a
        # harness rewritten to backplane-v1; keep the library proposal verbatim
        bh = m.get("backplane_harness") or {}
        cable = pv1.get("cable")
        if pv1.get("slots") and cable:
            new = {"backplane_connector": f"{cable['family']} {cable['pins']}p ({'/'.join(pv1['slots'])})",
                   "wires": bh.get("wires", []), "length_mm": bh.get("length_mm") or 150,
                   "mapping": "electrical.py maps module pins to slot pins by name (see backplane-v1 slot pin 'accepts')"}
            if new["backplane_connector"] != bh.get("backplane_connector"):
                m["backplane_harness_library"] = bh
            m["backplane_harness"] = new
        elif pv1.get("port") == "socket":
            m["backplane_harness_library"] = bh
            m["backplane_harness"] = {"backplane_connector": f"socket {pv1['slots'][0]} on the backplane", "wires": [], "length_mm": 0}
        out.append(m)
        if not pending and not cables_done:
            cables_done = True
            pack_of = {x["id"]: x.get("pack") for x in out}
            for c in foundation_cables(out, bp, notes):
                if c["id"] in seen:
                    notes.append(f"generated cable id {c['id']} already exists in {seen[c['id']]}; kept the library one")
                    continue
                seen[c["id"]] = "foundation"
                c["source_file"] = "foundation (tools/merge_modules.py)"
                ROLE[c["id"]] = "harness"
                # a made-to-drawing row that only pack rows use hides with its pack(s); base rows never do (fallback leads)
                fits = c.get("fits") or []
                if c.get("pinning") == "drawing" and fits and all(pack_of.get(f) for f in fits):
                    ps = sorted({pack_of[f] for f in fits})
                    c["pack"] = ps[0] if len(ps) == 1 else ps
                pending.append(c)
    # backplane entry follows backplane-v1.json
    if BACKPLANE.exists():
        bp = json.loads(BACKPLANE.read_text(encoding="utf-8"))
        for m in out:
            if m["role"] == "backplane":
                w, h = bp["board"]["size"]
                m["name"] = "Standard backplane PCB backplane-v1 (ours)"
                m["dims_mm"] = {"x": w, "y": h, "z": 17.6, "notes": "board outline from library/backplane-v1.json; z = 1.6 PCB + seated DevKitC "
                                "envelope 16.0 (estimate). Supersedes the 90 x 55 proposal of BACKPLANE_S3_V0_CUSTOM."}
                m["face_mm"] = [w, h]
                m["depth_mm"] = 17.6
                m["kicad"] = {"backplane_footprint": "NONE", "notes": "see library/backplane-v1.json parts[] for every footprint"}
                m.setdefault("merge_notes", []).append("renamed from BACKPLANE_S3_V0_CUSTOM; geometry now from backplane-v1.json")
                backplane_row_from_json(m, bp)
            if m["id"] == "USBC_RECEPTACLE_GCT_USB4105":
                slot = next(x for x in bp["slots"] if x["id"] == "J_PWR")
                k = dict(m.get("kicad") or {})
                if k.get("backplane_footprint") != slot["footprint"]:
                    m.setdefault("merge_notes", []).append(f"kicad.backplane_footprint set to the J_PWR slot footprint {slot['footprint']}")
                    k["backplane_footprint"] = slot["footprint"]
                k["alt_footprint"] = slot.get("alt_footprint")
                m["kicad"] = k
    # REVIEW-1 MIN-23: the saved sources (library/references/index.json, sha256 per file) each module's data rests on
    refs_ix = REFS_INDEX.exists() and json.loads(REFS_INDEX.read_text(encoding="utf-8")) or {"files": []}
    for m in out:
        files = [{"path": "references/" + f["path"], "sha256": f["sha256"], "url": f.get("url"), "fetched_at": f.get("fetched_at"),
                  "kind": f.get("kind"), "covers": f.get("covers", [])}
                 for f in refs_ix["files"] if m["id"] in (f.get("used_by") or [])]
        if files:
            m["evidence_files"] = sorted(files, key=lambda f: f["path"])
    notes += [f"lint: {x}" for x in lint]
    order = {r: i for i, r in enumerate(sorted({m["catalog_row"] for m in out}))}
    out.sort(key=lambda m: (order[m["catalog_row"]], m["id"]))
    return out, notes


def readme(mods: list[dict], notes: list[str]) -> str:
    L = ["# Gadget Studio module library", "",
         "`modules.json` is the merged library (foundation). Only these modules may appear in a design spec. Sources: "
         "`modules-a.json` (rows 01-07, 14-18, 20-24, 37) and `modules-b.json` (rows 08-13, 19, 25-33, 36, 38, 41-50); "
         "their `*-notes.md` explain every choice. Regenerate with `python3 -I tools/merge_modules.py` after either source changes, "
         "and the backplane with `python3 -I tools/gen_backplane_v1.py` (run that first).", "",
         "Optional packs (`packs/<name>.json`, CATALOG-PLAN 0.5) add rows marked `pack`; a row whose pack is off "
         "(`packs/enabled.json`, env `GS_PACKS_ENABLED` / `GS_PACKS_OFF`) is invisible to `gstudio.library.Library`. "
         + (f"Pack rows in this file: {', '.join(sorted({m['id'] + ' (' + str(m['pack']) + ')' for m in mods if m.get('merge')}))}."
            if any(m.get("merge") for m in mods) else "No pack rows in this file."), "",
         "Prices are blueprint §3.3 planning ranges (INR, often excluding GST and labour), not quotes. Every unverified number "
         "is listed in that module's `uncertain`. `v1` = how the module connects on `backplane-v1.json`.", "",
         "| id | row | role | name | dims mm (x × y × z) | aperture | v1 slot | price ₹ |", "|---|---|---|---|---|---|---|---|"]
    for m in mods:
        d = m.get("dims_mm") or {}
        dims = " × ".join("?" if d.get(k) is None else f"{d.get(k):g}" for k in ("x", "y", "z"))
        pv1 = m["backplane_v1"]
        slot = "/".join(pv1.get("slots") or []) or pv1.get("port")
        if not pv1.get("supported"):
            slot = "not on v1"
        pr = m.get("price_inr_planning") or []
        price = f"{pr[0]}-{pr[1]}" if len(pr) == 2 else "?"
        name = str(m.get("name", "")).replace("|", "/")
        L.append(f"| `{m['id']}` | {m['catalog_row']} | {m['role']} | {name} | {dims} | {m['aperture'].get('needs')} | {slot} | {price} |")
    L += ["", "## Schema (merged)", "",
          "Every object has the library agents' keys (`id, catalog_row, catalog_id, role, name, reference_product, dims_mm, "
          "pcb_thickness_mm, mount, pins, module_connector, backplane_harness, aperture, electrical, firmware, kicad, sourcing_in, "
          "price_inr_planning, evidence, uncertain`) plus:", "",
          "- `role`: canonical role (vocabulary in `tools/merge_modules.py`); the agents' coarse role is kept in `category`.",
          "- `aliases`: former ids that `Library.get()` still resolves.",
          "- `ref_prefix`: instance ref prefix (DISP, SPK, MIC, BTN, LED, AMP, SD, U, BP, ...).",
          "- `face_mm` [w, h], `depth_mm`, `harness_clearance_mm`: mounting envelope used by the fit rules.",
          "- `backplane_v1`: `{port, slots[], cable, supported, why?}`.",
          "- `electrical.supply_rail`, `electrical.i2c_addr[]`, `electrical.i2c_addr_alt[]`, `electrical.current_basis` "
          "(`estimate` when the module's `uncertain` mentions current).",
          "- `aperture.feature_allowed`, `aperture.required`.",
          "- `requires_soldering`: does the maker (the person following BUILD_GUIDE) solder? true/false/null = unknown, plus "
          "`requires_soldering_basis`. Factory work is separate: `prewire` {by, lead, pads, slot, note} and `factory_soldering`. "
          "`sourcing_in[].variant`/`same_part` flag pre-wired listings that are a different physical part. `sourcing_in[].gst` "
          "(`incl`/`excl`/null, parsed from the row's notes) and `sourcing_in[].pack_size` (parsed, else null).",
          "- `backplane_harness_library`: the library agent's original harness proposal when backplane-v1 differs.", "",
          "## Connector policy (backplane-v1) and harness rows", "",
          "Ports are keyed JST-PH 2.0 (3.3 V) except `J_LED`, which is JST-XH 2.5 (B3B-XH-A, 5 V supply and 5 V data through "
          "the 74AHCT1G125 buffer, silkscreen 'LED 5V'; REVIEW-1 MAJ-3). Rule checked by `tools/gen_backplane_v1.py`: two ports "
          "with the same connector family and pin count carry the same supply, so no lead fits a port at the wrong voltage.", "",
          "Every design harness row (`design_harness: true`, generated here from the backplane ports and the modules on them) "
          "names ONE module-end family (`jst_ph`, `jst_xh`, `dupont_2.54` or `spade_2.8`) and its position table "
          "(`module_end.housings[].positions[]`: port pin, module pin, wire colour), plus `sku`, `drawing` and a `factory_make` "
          "entry (made by our factory to the drawing; no retail listing exists for these exact double-ended leads). Rows:", "",
          "| id pattern | module end | used by |", "|---|---|---|",
          "| `CABLE_JSTPH_<n>P_<len>` | PHR-n, same order | J_SPK speaker lead (factory pre-wired), J_DISP Waveshare LCD |",
          "| `CABLE_JSTXH_3P_<len>` | XHP-3, same order | J_LED leads (factory pre-wired to the LED pads) |",
          "| `CABLE_JSTPH_{3,4}P_DUPONT1X{3,4}_<len>` | Dupont 1xn, straight-through | modules whose header order equals the port |",
          "| `..._DUPONT1X<k>_<len>_X<order>` | Dupont 1xk, made to drawing | header modules whose order differs (e.g. X2143 = OLED VCC,GND,SCL,SDA) |",
          "| `CABLE_JSTPH_6P_DUPONT2X1X3_<len>` | 2 x Dupont 1x3, made to drawing | INMP441 (L/R,WS,SCK + SD,VDD,GND) |",
          "| `CABLE_JSTPH_3P_SPADE28_<len>` | 2 x 2.8 mm female spade | arcade button microswitch COM/NO |",
          "",
          "len = 100/150/200/300 mm. `CABLE_JSTPH_{3,4}P_300MM` are single-ended Evelta pigtails kept as factory stock "
          "(`design_harness: false`). `gstudio.electrical.select_cable` picks the row whose position table matches the module.",
          "| 02a | 2 x 1x22, 1x7, 1x6, 2 x 1x1 female 2.54 sockets | DevKitC, amp and microSD sockets on the backplane |", "",
          "## Rules worth knowing", "",
          "- I2C clash: `IMU_MPU6050_GY521` and `RTC_DS3231_ZS042` both default to 0x68. Normalize rejects the pair unless the "
          "IMU is strapped to 0x69 (not modelled yet).",
          "- `RTC_DS3231_ZS042` carries a coin cell (blueprint: no batteries at launch). Normalize rejects it in child-directed "
          "designs and warns otherwise.",
          "- `HALL_KY003_A3144` maps to a 3.3 V port only (its pull-up then stays at 3.3 V); switching at 3.3 V is not guaranteed.",
          "- Not on backplane-v1: `ENCODER_KY040_PUSH`, `PIR_HCSR501_STD`, `GEARMOTOR_N20_6V_LOWRPM`, "
          "`MOTOR_DRV_DRV8833_POLOLU_2130`, `FAN_3010_5V_GUARDED`, `USBC_BREAKOUT_ADAFRUIT_4090`.",
          "- `SERVO_SG90_TOWERPRO` uses `J_LED` (the only 5 V port), so a design has an addressable LED or a servo, not both.", ""]
    if notes:
        L += ["## Merge report", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(L)


def main() -> int:
    mods, notes = merge()
    ids = [m["id"] for m in mods]
    assert len(ids) == len(set(ids))
    OUT.write_text(json.dumps(mods, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    README.write_text(readme(mods, notes), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(mods)} modules; README rows {len(mods)}; notes {len(notes)}")
    for n in notes:
        print("  -", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
