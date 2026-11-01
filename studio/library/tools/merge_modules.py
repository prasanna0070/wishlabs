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
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
SRC = [LIB / "modules-a.json", LIB / "modules-b.json"]
OUT = LIB / "modules.json"
README = LIB / "README.md"
BACKPLANE = LIB / "backplane-v1.json"

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
}
RENAME = {"BACKPLANE_S3_V0_CUSTOM": "BACKPLANE_V1"}

PREFIX = {"core": "U", "backplane": "BP", "power_in": "PWR", "adapter": "PSU", "usb_cable": "CBL", "harness": "W",
          "display": "DISP", "led": "LED", "mic": "MIC", "amp": "AMP", "speaker": "SPK", "buzzer": "BZ", "button": "BTN",
          "storage": "SD", "encoder": "ENC", "touch": "TCH", "light_sensor": "ALS", "temp_humidity": "ENV", "pressure": "BARO",
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
        return {"port": "harness", "slots": [], "cable": None, "supported": True}
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
    out = []
    for m in rows:
        oid = m["id"]
        role = ROLE.get(oid)
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
        m["ref_prefix"] = PREFIX.get(role)
        # geometry convenience
        d = m.get("dims_mm") or {}
        x, y, z = d.get("x"), d.get("y"), d.get("z")
        face, depth = ((x, y) if x is not None and y is not None else None), z
        if oid in FACE_OVERRIDE:
            fo, do, why = FACE_OVERRIDE[oid]
            face, depth = fo, do
            m.setdefault("merge_notes", []).append(f"face_mm/depth_mm overridden: {why}")
        m["face_mm"] = list(face) if face else None
        m["depth_mm"] = depth
        mc = (m.get("module_connector") or {}).get("type", "")
        m["harness_clearance_mm"] = 10.0 if mc in ("header_2.54", "wire_leads", "jst_ph_2.0", "solder_pads") else 0.0
        # soldering: only from explicit library text (pipeline request 2026-11-01); otherwise null = unknown
        mc_loc = " ".join(str((m.get("module_connector") or {}).get(k, "")) for k in ("type", "location")).lower()
        mount_n = str((m.get("mount") or {}).get("notes", "")).lower()
        if mc == "solder_pads" or "solder 2 leads" in mc_loc or "solder" in mc_loc:
            m["requires_soldering"], m["requires_soldering_basis"] = True, "module connector is solder pads/eyelets (library text)"
        elif mc in ("jst_ph_2.0",) or role in ("harness", "usb_cable", "adapter") or "spade" in mc_loc:
            m["requires_soldering"], m["requires_soldering_basis"] = False, "plug-in connector or crimped spade (library text)"
        elif role in ("screws", "inserts", "feet", "ties", "gasket", "label", "magnet", "diffuser", "cartridge", "panel",
                      "servo_bracket", "servo_horn", "backplane", "power_in", "test_interface"):
            m["requires_soldering"], m["requires_soldering_basis"] = False, "mechanical/consumable or PCB-assembled part"
        else:
            m["requires_soldering"], m["requires_soldering_basis"] = None, "unknown: the library does not say whether the header comes fitted"
        if m["id"] == "AMP_MAX98357A_I2S":
            m["requires_soldering"], m["requires_soldering_basis"] = True, "backplane-v1 A1: header pins-down + two VO pins soldered by the factory"
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
        # backplane binding
        pv1 = port_for(m, role)
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
        a["feature_allowed"] = role in FEATURE_ROLES
        a["required"] = role in FEATURE_ROLES
        if role == "touch" and a.get("needs") == "none":
            a["needs"] = "touch_zone"
            a.setdefault("merge_note", "touch pads read through a thin non-metallic wall; wall thickness to be test-printed")
        if role in FEATURE_ROLES and a.get("needs") == "none":
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
            if m["id"] == "USBC_RECEPTACLE_GCT_USB4105":
                k = dict(m.get("kicad") or {})
                k["alt_footprint"] = k.get("backplane_footprint")
                k["backplane_footprint"] = "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12"
                k["notes"] = (k.get("notes") or "") + " | backplane-v1 places the HRO TYPE-C-31-M-12 (courtyard read on the VM); GCT USB4105 is the alternate."
                m["kicad"] = k
    order = {r: i for i, r in enumerate(sorted({m["catalog_row"] for m in out}))}
    out.sort(key=lambda m: (order[m["catalog_row"]], m["id"]))
    return out, notes


def readme(mods: list[dict], notes: list[str]) -> str:
    L = ["# Gadget Studio module library", "",
         "`modules.json` is the merged library (foundation). Only these modules may appear in a design spec. Sources: "
         "`modules-a.json` (rows 01-07, 14-18, 20-24, 37) and `modules-b.json` (rows 08-13, 19, 25-33, 36, 38, 41-50); "
         "their `*-notes.md` explain every choice. Regenerate with `python3 -I tools/merge_modules.py` after either source changes, "
         "and the backplane with `python3 -I tools/gen_backplane_v1.py` (run that first).", "",
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
          "- `requires_soldering` (true/false/null = unknown) + `requires_soldering_basis`; `sourcing_in[].gst` "
          "(`incl`/`excl`/null, parsed from the row's notes) and `sourcing_in[].pack_size` (parsed, else null).",
          "- `backplane_harness_library`: the library agent's original harness proposal when backplane-v1 differs.", "",
          "## Connector policy (backplane-v1) and proposed catalog rows", "",
          "One family, JST-PH 2.0 vertical, keyed. All PH ports carry 3.3 V except `J_LED` (5 V supply, 5 V data through the "
          "74AHCT1G125 buffer), which is labelled 'LED 5V'. Every 3-pin IO module in this library accepts 3.3-5 V and its output "
          "would only meet the buffer, so a mis-plug into `J_LED` does not reach a GPIO.", "",
          "Blueprint rows 06 (4-pin) and 07 (3-pin) cover I2C and IO cables. backplane-v1 also needs these cable rows "
          "(proposed, not yet in the blueprint catalog):", "",
          "| proposed row | cable | used by |", "|---|---|---|",
          "| 06b | JST-PH 2-pin speaker lead, 100-300 mm | `J_SPK` (speaker) |",
          "| 06c | JST-PH 6-pin to Dupont 1x6, 100-300 mm | `J_MIC` (INMP441) |",
          "| 06d | JST-PH 8-pin to JST-PH 8-pin, same order, 100-300 mm | `J_DISP` (Waveshare 2in LCD) |",
          "| 06e | JST-PH 3-pin to servo 3-way 2.54 plug adapter | `J_LED` when it drives a servo |",
          "| 06f | JST-PH 5-pin / 6-pin | encoder / DRV8833: not on backplane-v1, kept for backplane-v2 |",
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
