#!/usr/bin/env python3
"""Generate library/backplane-v1.json (foundation). Pure Python, no deps. Run: python3 -I gen_backplane_v1.py

Every coordinate is computed from:
  * the DevKitC-1 v1.1 DXF (Espressif, parsed 2026-11-01): PCB 25.40 x 62.865, header rows at u=1.27 / 24.13,
    pins at v = 7.96 .. 61.30 (pin 1 at the antenna end), antenna overhang to v = 69.15;
  * KiCad 9.0.9 footprint courtyards read on the build VM (/usr/share/kicad/footprints, 2026-11-01);
  * module drawings in library/modules.json (Adafruit 3006 amp board file; Catalex-type microSD = estimates).
Board frame: mm, origin at the board's lower-left corner, TOP view, +x = world +X (robot's left), +y = world +Y (rear).
Rotation: degrees counter-clockwise seen from the top. This is the same number KiCad uses; KiCad position is
(x, H - y) when the board's top-left corner is placed at the KiCad origin.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backplane-v1.json"

# ---------------------------------------------------------------------------------------------- footprints
# local KiCad coordinates (y down), F.CrtYd bbox read on the VM: (xmin, xmax, ymin, ymax)
FP = {
    "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical": (-2.45, 4.45, -2.20, 3.30),
    "Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical": (-2.45, 6.45, -2.20, 3.30),
    "Connector_JST:JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical": (-2.45, 8.45, -2.20, 3.30),
    "Connector_JST:JST_PH_B6B-PH-K_1x06_P2.00mm_Vertical": (-2.45, 12.45, -2.20, 3.30),
    "Connector_JST:JST_PH_B8B-PH-K_1x08_P2.00mm_Vertical": (-2.45, 16.45, -2.20, 3.30),
    "Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical": (-2.95, 7.95, -2.85, 3.90),
    "Connector_PinSocket_2.54mm:PinSocket_1x22_P2.54mm_Vertical": (-1.80, 1.75, -1.80, 55.10),
    "Connector_PinSocket_2.54mm:PinSocket_1x07_P2.54mm_Vertical": (-1.80, 1.75, -1.80, 17.00),
    "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical": (-1.80, 1.75, -1.80, 14.45),
    "Connector_PinSocket_2.54mm:PinSocket_1x01_P2.54mm_Vertical": (-1.80, 1.75, -1.80, 1.75),
    "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12": (-5.32, 5.32, -5.27, 4.15),
    "MountingHole:MountingHole_2.7mm_M2.5": (-2.95, 2.95, -2.95, 2.95),
    "TestPoint:TestPoint_Pad_D1.5mm": (-1.25, 1.25, -1.25, 1.25),
    "Fuse:Fuse_1812_4532Metric": (-2.95, 2.95, -1.95, 1.95),
    "Diode_SMD:D_SMA": (-3.51, 3.50, -1.75, 1.75),
    "Package_TO_SOT_SMD:SOT-23-5": (-2.05, 2.05, -1.84, 1.70),
    "Package_TO_SOT_SMD:SOT-23-6": (-2.05, 2.05, -1.84, 1.70),
    "Resistor_SMD:R_0603_1608Metric": (-1.48, 1.48, -0.73, 0.73),
    "Capacitor_SMD:C_0603_1608Metric": (-1.48, 1.48, -0.73, 0.73),
    "Capacitor_SMD:C_0805_2012Metric": (-1.70, 1.70, -0.98, 0.98),
    "Capacitor_SMD:C_1206_3216Metric": (-2.30, 2.30, -1.15, 1.15),
}
PITCH = {"PH": 2.0, "XH": 2.5}


def xf(x, y, X, Y, rot):
    """local KiCad point -> board frame (y up)."""
    lx, ly = x, -y
    a = math.radians(rot)
    return (round(X + lx * math.cos(a) - ly * math.sin(a), 3), round(Y + lx * math.sin(a) + ly * math.cos(a), 3))


def court(fp, X, Y, rot):
    x0, x1, y0, y1 = FP[fp]
    pts = [xf(x, y, X, Y, rot) for x in (x0, x1) for y in (y0, y1)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [round(min(xs), 2), round(max(xs), 2), round(min(ys), 2), round(max(ys), 2)]


def pin_xy(fp, n, X, Y, rot):
    if "PinSocket" in fp:
        return xf(0.0, 2.54 * (n - 1), X, Y, rot)
    fam = "XH" if "_XH_" in fp else "PH"
    return xf(PITCH[fam] * (n - 1), 0.0, X, Y, rot)


# ---------------------------------------------------------------------------------------------- board
W, H = 62.8, 78.6
T = 1.6
HOLE_IN = 3.5
HOLES = [("H1", HOLE_IN, HOLE_IN), ("H2", W - HOLE_IN, HOLE_IN), ("H3", HOLE_IN, H - HOLE_IN), ("H4", W - HOLE_IN, H - HOLE_IN)]
SCREW_HEAD_D = 4.6  # estimate (library: M2.5 SHCS head ~4.5 estimated); keep-out radius below

# DevKitC placement: devkit frame (u across 0..25.4, v along 0..62.865, v=0 at USB end) -> board (X0 - v, Y0 + u)
DK_PCB_L, DK_PCB_W, DK_ANT_V, DK_ROW_U, DK_PIN1_V, DK_PIN22_V = 62.865, 25.40, 69.15, (1.27, 24.13), 61.30, 7.96
USB_OVERHANG = 1.0  # devkit PCB edge 1 mm past the board's +x edge (robot's left)
X0 = W + USB_OVERHANG
Y0 = 43.0


def dk(u, v):
    return (round(X0 - v, 3), round(Y0 + u, 3))


# ---------------------------------------------------------------------------------------------- GPIO plan
GPIO = {
    "I2C_SDA": 8, "I2C_SCL": 9,
    "SPI_SCK": 12, "SPI_MOSI": 11, "SPI_MISO": 13,
    "SD_CS": 10,
    "DISP_CS": 14, "DISP_DC": 21, "DISP_RST": 18, "DISP_BL": 17,
    "I2S_SPK_BCLK": 15, "I2S_SPK_LRC": 16, "I2S_SPK_DIN": 7, "AMP_SD": 39,
    "I2S_MIC_SCK": 41, "I2S_MIC_WS": 42, "I2S_MIC_SD": 40,
    "IO1_SIG": 4, "IO2_SIG": 5, "IO3_SIG": 6,
    "LED_DIN": 2,
    "USB_DP": 20, "USB_DN": 19,
    "UART0_TX": 43, "UART0_RX": 44,
    "BOOT_IO0": 0,
}
RESERVED = {
    "0": "strapping (boot mode); BOOT button; test pad TP7 only",
    "3": "strapping (JTAG source select)",
    "45": "strapping (VDD_SPI voltage); must stay low at reset",
    "46": "strapping (boot mode / ROM log); weak pull-down",
    "19": "USB D- (native USB); wired to the rear USB-C through U2 ESD",
    "20": "USB D+ (native USB); wired to the rear USB-C through U2 ESD",
    "43": "UART0 TX to the DevKitC USB-UART bridge; test pad TP5",
    "44": "UART0 RX from the DevKitC USB-UART bridge; test pad TP6",
    "35": "Octal PSRAM (R8 modules): not available",
    "36": "Octal PSRAM (R8 modules): not available",
    "37": "Octal PSRAM (R8 modules): not available",
    "38": "on-board RGB LED on DevKitC-1 v1.1",
    "48": "on-board RGB LED on DevKitC-1 v1.0 and many clones",
    "47": "SPICLK_P; left free (VDD_SPI domain on 1.8 V-flash variants; not needed)",
}
J1 = ["+3V3", "+3V3", "EN", "IO1_SIG", "IO2_SIG", "IO3_SIG", "I2S_SPK_DIN", "I2S_SPK_BCLK", "I2S_SPK_LRC", "DISP_BL",
      "DISP_RST", "I2C_SDA", None, None, "I2C_SCL", "SD_CS", "SPI_MOSI", "SPI_SCK", "SPI_MISO", "DISP_CS", "+5V", "GND"]
J1_GPIO = [None, None, None, 4, 5, 6, 7, 15, 16, 17, 18, 8, 3, 46, 9, 10, 11, 12, 13, 14, None, None]
J3 = ["GND", "UART0_TX", "UART0_RX", None, "LED_DIN_3V3", "I2S_MIC_WS", "I2S_MIC_SCK", "I2S_MIC_SD", "AMP_SD",
      None, None, None, None, "BOOT_IO0", None, None, None, "DISP_DC", "USB_DP", "USB_DN", "GND", "GND"]
J3_GPIO = [None, 43, 44, 1, 2, 42, 41, 40, 39, 38, 37, 36, 35, 0, 45, 48, 47, 21, 20, 19, None, None]
J1_NAMES = ["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17", "18", "8", "3", "46", "9", "10", "11", "12", "13", "14", "5V", "G"]
J3_NAMES = ["G", "TX", "RX", "1", "2", "42", "41", "40", "39", "38", "37", "36", "35", "0", "45", "48", "47", "21", "20", "19", "G", "G"]


def check_gpio():
    for sig, g in GPIO.items():
        pass
    sig_gpio = {}
    for names, gp in ((J1, J1_GPIO), (J3, J3_GPIO)):
        for net, g in zip(names, gp):
            if net and g is not None:
                sig_gpio[net] = g
    for sig, g in GPIO.items():
        key = "LED_DIN_3V3" if sig == "LED_DIN" else sig
        assert sig_gpio.get(key) == g, (sig, g, sig_gpio.get(key))
    used = [g for s, g in GPIO.items() if s not in ("USB_DP", "USB_DN", "UART0_TX", "UART0_RX", "BOOT_IO0")]
    assert len(used) == len(set(used)), "duplicate GPIO"
    for g in used:
        assert str(g) not in RESERVED, f"GPIO{g} is reserved"


# ---------------------------------------------------------------------------------------------- slot builder
PH = "Connector_JST:JST_PH_B{n}B-PH-K_1x0{n}_P2.00mm_Vertical"
ALIAS_3V3 = ["3V3", "3.3V", "VCC", "VDD", "VIN", "V+", "+", "VCC_3V3"]
ALIAS_5V = ["5V", "VCC", "VIN", "V+", "+", "VDD", "+5V"]
ALIAS_GND = ["GND", "G", "-", "VSS", "COM"]


def P(n, name, net, aliases=None, kind="signal"):
    g = GPIO.get(net) if net else None
    if net == "LED_DIN_5V":
        g = None
    d = {"pin": n, "name": name, "net": net, "gpio": g, "kind": kind}
    if aliases:
        d["accepts"] = aliases
    return d


SLOTS: list[dict] = []
PARTS: list[dict] = []  # every footprint on the board (support parts included)


def add_part(ref, fp, X, Y, rot, side="top", role="", value="", slot=None, note=""):
    c = court(fp, X, Y, rot) if side == "top" else court(fp, X, Y, rot)
    p = {"ref": ref, "footprint": fp, "at": [round(X, 3), round(Y, 3)], "rotation_deg": rot, "side": side,
         "kicad": {"x": round(X, 3), "y": round(H - Y, 3), "rot": rot}, "courtyard": c}
    if value:
        p["value"] = value
    if role:
        p["role"] = role
    if slot:
        p["slot"] = slot
    if note:
        p["note"] = note
    PARTS.append(p)
    return p


def add_port(sid, fp, X, Y, rot, pins, accepts, title, facing, envelope_top, voltage, cable, note="", pin_count=None):
    part = add_part(sid, fp, X, Y, rot, role="port", slot=sid)
    for p in pins:
        p["at"] = list(pin_xy(fp, p["pin"], X, Y, rot))
    SLOTS.append({
        "id": sid, "kind": "port", "title": title, "footprint": fp, "at": part["at"], "rotation_deg": rot,
        "kicad": part["kicad"], "courtyard": part["courtyard"], "facing": facing,
        "connector": cable["family"], "pins_count": len(pins), "supply": voltage,
        "cable": cable, "accepts_roles": accepts, "pins": pins,
        "height_envelope_mm": {"above_board_top": envelope_top, "why": "header + mated plug + wire bend zone (estimate)"},
        "plug_access": "vertical from above; keep the plan courtyard free of modules above it",
        **({"note": note} if note else {}),
    })


def build():
    check_gpio()
    # ---- U1 core sockets
    yJ1 = Y0 + DK_ROW_U[0]
    yJ3 = Y0 + DK_ROW_U[1]
    xpin1 = X0 - DK_PIN1_V
    s1 = add_part("J_U1A", "Connector_PinSocket_2.54mm:PinSocket_1x22_P2.54mm_Vertical", xpin1, yJ1, 90, role="core_socket", slot="U1",
                  note="DevKitC header J1 (pin 1 = 3V3 at the antenna end)")
    s3 = add_part("J_U1B", "Connector_PinSocket_2.54mm:PinSocket_1x22_P2.54mm_Vertical", xpin1, yJ3, 90, role="core_socket", slot="U1",
                  note="DevKitC header J3 (pin 1 = GND at the antenna end)")
    upins = []
    for k in range(22):
        net = J1[k] if J1[k] else None
        upins.append({"pin": f"J1-{k + 1}", "devkit_label": J1_NAMES[k], "gpio": J1_GPIO[k], "net": net,
                      "at": list(pin_xy(s1["footprint"], k + 1, xpin1, yJ1, 90)), "socket": "J_U1A", "socket_pin": k + 1,
                      **({} if net else {"kind": "nc", "why": RESERVED.get(str(J1_GPIO[k]), "not used by backplane-v1")})})
    for k in range(22):
        net = J3[k] if J3[k] else None
        upins.append({"pin": f"J3-{k + 1}", "devkit_label": J3_NAMES[k], "gpio": J3_GPIO[k], "net": net,
                      "at": list(pin_xy(s3["footprint"], k + 1, xpin1, yJ3, 90)), "socket": "J_U1B", "socket_pin": k + 1,
                      **({} if net else {"kind": "nc", "why": RESERVED.get(str(J3_GPIO[k]), "free (GPIO1) or not used by backplane-v1")})})
    pcb = [round(X0 - DK_PCB_L, 3), round(X0, 3), round(Y0, 3), round(Y0 + DK_PCB_W, 3)]
    ant = [round(X0 - DK_ANT_V, 3), round(X0 - DK_PCB_L, 3), round(Y0 + (DK_PCB_W - 18.0) / 2, 3), round(Y0 + (DK_PCB_W + 18.0) / 2, 3)]
    SLOTS.append({
        "id": "U1", "kind": "core", "title": "ESP32-S3-DevKitC-1 on 2 x 1x22 female sockets",
        "module": "CORE_ESP32S3_DEVKITC1_N8R8", "accepts_roles": ["core"],
        "parts": ["J_U1A", "J_U1B"], "devkit_origin": [round(X0, 3), round(Y0, 3)], "rotation_deg": 90,
        "devkit_frame": "devkit (u across 0..25.4 with J1 at u=1.27, v along 0..62.865 with v=0 at the USB end) -> board (x0 - v, y0 + u)",
        "devkit_pcb_plan": pcb, "antenna_plan": ant, "usb_end_x": round(X0, 3), "usb_overhang_mm": USB_OVERHANG,
        "facing": "devkit USB ports face +x (robot's left), 1.0 mm past the board edge; antenna overhangs the -x edge",
        "row_spacing_mm": 22.86, "pins": upins,
        "height_envelope_mm": {"devkit_bottom_above_board_top": 11.0, "above_board_top": 16.0,
                               "why": "8.5 socket + ~2.5 male-header spacer + 1.6 PCB + 3.1 WROOM-1 + 0.3 (estimate; measure)"},
        "under_devkit_zone": [round(xpin1 + 3, 2), round(X0 - DK_PIN22_V - 3, 2), round(yJ1 + 2.2, 2), round(yJ3 - 2.2, 2)],
        "under_devkit_max_height_mm": 9.0,
    })
    # ---- A1 amp: Adafruit 3006 board frame (origin bottom-left, header along y=0); placed unrotated
    AX, AY = 44.5, 8.5
    a_pins_xy = [(AX + 1.27 + 2.54 * k, AY + 2.54) for k in range(7)]
    add_part("J_A1", "Connector_PinSocket_2.54mm:PinSocket_1x07_P2.54mm_Vertical", a_pins_xy[0][0], a_pins_xy[0][1], 90, role="module_socket", slot="A1")
    add_part("J_A1P", "Connector_PinSocket_2.54mm:PinSocket_1x01_P2.54mm_Vertical", AX + 10.59, AY + 15.49, 0, role="module_socket", slot="A1",
             note="amp VO+ (terminal pad); factory solders a 2.54 male pin instead of the screw terminal")
    add_part("J_A1N", "Connector_PinSocket_2.54mm:PinSocket_1x01_P2.54mm_Vertical", AX + 7.09, AY + 15.49, 0, role="module_socket", slot="A1",
             note="amp VO- (terminal pad)")
    a_names = ["LRC", "BCLK", "DIN", "GAIN", "SD", "GND", "VIN"]
    a_nets = ["I2S_SPK_LRC", "I2S_SPK_BCLK", "I2S_SPK_DIN", None, "AMP_SD", "GND", "+5V"]
    a_pins = [{"pin": k + 1, "name": a_names[k], "net": a_nets[k], "gpio": GPIO.get(a_nets[k]) if a_nets[k] else None,
               "at": [round(a_pins_xy[k][0], 3), round(a_pins_xy[k][1], 3)], "socket": "J_A1"} for k in range(7)]
    a_pins[3]["note"] = "GAIN left open = 9 dB (Adafruit default); not connected on the backplane"
    a_pins[3]["kind"] = "nc"
    a_pins += [{"pin": "VO+", "name": "VO+", "net": "SPK_P", "gpio": None, "at": [AX + 10.59, AY + 15.49], "socket": "J_A1P"},
               {"pin": "VO-", "name": "VO-", "net": "SPK_N", "gpio": None, "at": [AX + 7.09, AY + 15.49], "socket": "J_A1N"}]
    SLOTS.append({
        "id": "A1", "kind": "module_socket", "title": "MAX98357A I2S amplifier breakout (1x7 + 2 x 1x1 sockets)",
        "accepts_roles": ["amp"], "module": "AMP_MAX98357A_I2S", "parts": ["J_A1", "J_A1P", "J_A1N"],
        "module_plan": [AX, round(AX + 17.78, 3), AY, round(AY + 19.05, 3)], "module_frame_origin": [AX, AY], "module_rotation_deg": 0,
        "pins": a_pins, "supply": "+5V",
        "height_envelope_mm": {"module_bottom_above_board_top": 11.0, "above_board_top": 15.0, "why": "socket 8.5 + header spacer 2.5 + PCB 1.6 + parts (estimate)"},
        "assembly": "Solder the 7-pin header pins-down. Instead of the supplied screw terminal, solder two 2.54 mm male pins into the "
                    "VO+/VO- terminal pads (Adafruit board file: 3.5 mm pitch at y = 15.49) so the amp plugs fully into the backplane. "
                    "Speaker audio then reaches J_SPK through the board. Physical check still needed on a real board.",
        "uncertain": ["VO pin trick not yet tried on a real Adafruit 3006", "clone boards may differ from the Adafruit outline"],
    })
    # J_SPK next to the amp output
    add_port("J_SPK", PH.format(n=2), 52.0, 31.3, 0,
             [P(1, "SPK+", "SPK_P", ["+", "SPK+", "VO+", "RED"], "audio"), P(2, "SPK-", "SPK_N", ["-", "SPK-", "VO-", "BLACK"], "audio")],
             ["speaker"], "Speaker (amp output)", "up", 18.0, "none (bridge-tied amp output; never ground either pin)",
             {"family": "JST-PH 2.0", "pins": 2, "type": "PHR-2 housing, speaker lead"},
             note="Both pins are amplifier outputs (BTL): never connect SPK- to GND.")
    # ---- S1 microSD module socket (Catalex-type, 42 x 24, header on the +x short edge; positions ESTIMATED)
    SX0, SY0 = 0.5, 8.5
    hdr_x = SX0 + 42.0 - 2.0
    s_pin1_y = SY0 + 12.0 + 2.5 * 2.54
    add_part("J_S1", "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical", hdr_x, s_pin1_y, 0, role="module_socket", slot="S1")
    s_names = ["GND", "VCC", "MISO", "MOSI", "SCK", "CS"]
    s_nets = ["GND", "+5V", "SPI_MISO", "SPI_MOSI", "SPI_SCK", "SD_CS"]
    s_pins = [{"pin": k + 1, "name": s_names[k], "net": s_nets[k], "gpio": GPIO.get(s_nets[k]),
               "at": list(pin_xy("Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical", k + 1, hdr_x, s_pin1_y, 0)), "socket": "J_S1"}
              for k in range(6)]
    SLOTS.append({
        "id": "S1", "kind": "module_socket", "title": "microSD SPI module (Catalex-type) on a 1x6 socket",
        "accepts_roles": ["storage"], "module": "MICROSD_SPI_MODULE_5V", "parts": ["J_S1"],
        "module_plan": [SX0, SX0 + 42.0, SY0, SY0 + 24.0], "module_rotation_deg": 180,
        "card_slot_side": "-x (board edge at the robot's right); card ejects toward -x",
        "pins": s_pins, "supply": "+5V (module has its own 3.3 V LDO and level shifter)",
        "height_envelope_mm": {"module_bottom_above_board_top": 11.0, "above_board_top": 16.0, "why": "socket 8.5 + spacer 2.5 + PCB 1.6 + card socket/LDO ~3 (estimate)"},
        "support": "Cantilevered 42 mm module: the cartridge adds two printed posts S1_POST1/2 through the board (see cartridge.support_posts).",
        "uncertain": ["header position on the module (2.0 mm from the short edge) and pin-1 end are estimates; confirm on a sample",
                      "Probots lists VCC first; physical pin order must be checked before the socket pin map is frozen",
                      "the module's MISO buffer may not tri-state; the display never drives MISO so the shared SPI bus still works"],
    })
    # ---- Row B: mic, display, I2C1, I2C2 (pins at y = 37.5)
    yb = 37.5
    add_port("J_MIC", PH.format(n=6), 2.95, yb, 0,
             [P(1, "GND", "GND", ALIAS_GND, "power"), P(2, "3V3", "+3V3", ALIAS_3V3, "power"), P(3, "SCK", "I2S_MIC_SCK", ["SCK", "BCLK", "CLK"]),
              P(4, "WS", "I2S_MIC_WS", ["WS", "LRCL", "LRC"]), P(5, "SD", "I2S_MIC_SD", ["SD", "DOUT", "DATA"]), P(6, "LR", "GND", ["L/R", "LR", "SEL"], "strap")],
             ["mic"], "I2S microphone", "up", 20.0, "+3V3 only (INMP441 max 3.63 V)",
             {"family": "JST-PH 2.0", "pins": 6, "type": "PHR-6 to Dupont 1x6 female, by label"},
             note="LR (pin 6) is tied to GND on the board: the mic sends on the left channel.")
    add_port("J_DISP", PH.format(n=8), 18.85, yb, 0,
             [P(1, "VCC", "+3V3", ALIAS_3V3, "power"), P(2, "GND", "GND", ALIAS_GND, "power"), P(3, "DIN", "SPI_MOSI", ["DIN", "MOSI", "SDA", "SDI"]),
              P(4, "CLK", "SPI_SCK", ["CLK", "SCK", "SCL"]), P(5, "CS", "DISP_CS", ["CS"]), P(6, "DC", "DISP_DC", ["DC", "RS", "A0"]),
              P(7, "RST", "DISP_RST", ["RST", "RES", "RESET"]), P(8, "BL", "DISP_BL", ["BL", "BLK", "LED"])],
             ["display"], "SPI colour display", "up", 20.0, "+3V3",
             {"family": "JST-PH 2.0", "pins": 8, "type": "PH-PH 8p same-order cable (matches the Waveshare 2in LCD socket order)"},
             note="Pin order VCC, GND, DIN, CLK, CS, DC, RST, BL equals the Waveshare 2inch LCD PH2.0 socket, so a straight 1:1 cable works. "
                  "This replaces the ARCHITECTURE §6 draft order (GND first) on purpose.")
    i2c_pins = lambda: [P(1, "GND", "GND", ALIAS_GND, "power"), P(2, "3V3", "+3V3", ALIAS_3V3, "power"),
                        P(3, "SDA", "I2C_SDA", ["SDA", "SDI", "DATA"]), P(4, "SCL", "I2C_SCL", ["SCL", "SCK", "CLK"])]
    i2c_roles = ["display", "light_sensor", "temp_humidity", "pressure", "distance", "imu", "rtc"]
    i2c_cable = {"family": "JST-PH 2.0", "pins": 4, "type": "PHR-4 to Dupont 1x4 female (by label) or PH-SH for STEMMA QT"}
    add_port("J_I2C1", PH.format(n=4), 38.75, yb, 0, i2c_pins(), i2c_roles, "I2C port 1", "up", 20.0, "+3V3", i2c_cable,
             note="Shared I2C bus (SDA GPIO8, SCL GPIO9) with 4.7 k pull-ups R4/R5 on the board.")
    add_port("J_I2C2", PH.format(n=4), 50.65, yb, 0, i2c_pins(), i2c_roles, "I2C port 2", "up", 20.0, "+3V3", i2c_cable)
    # ---- Row A (front edge): IO1..3 and LED, all JST-PH 3p (coordinator 2026-11-01: PH only, no XH)
    io_pins = lambda n: [P(1, "GND", "GND", ALIAS_GND, "power"), P(2, "3V3", "+3V3", ALIAS_3V3, "power"),
                         P(3, "SIG", f"IO{n}_SIG", ["SIG", "S", "OUT", "I/O", "IO", "DO", "D0", "A0", "AO", "NO", "SW"])]
    io_roles = ["button", "touch", "buzzer", "motion", "light_sensor", "endstop", "hall"]
    io_cable = {"family": "JST-PH 2.0", "pins": 3, "type": "PHR-3 to Dupont/spade by label"}
    for n, x in ((1, 9.5), (2, 20.0), (3, 30.5)):
        add_port(f"J_IO{n}", PH.format(n=3), x, 3.8, 0, io_pins(n), io_roles, f"Digital/analog IO {n}", "up", 20.0, "+3V3", io_cable,
                 note=f"GPIO{GPIO[f'IO{n}_SIG']} (ADC1 + touch capable). 3.3 V logic and 3.3 V supply only.")
    add_port("J_LED", PH.format(n=3), 41.0, 3.8, 0,
             [P(1, "GND", "GND", ALIAS_GND, "power"), P(2, "5V", "+5V", ALIAS_5V, "power"), {**P(3, "DIN", "LED_DIN_5V", ["DIN", "DI", "IN", "D", "SIG", "PWM", "S"]),
              "source_gpio": GPIO["LED_DIN"], "via": "U3 74AHCT1G125 (3.3 V -> 5 V) + R3 330 ohm"}],
             ["led", "servo"], "Addressable LED or servo (5 V supply, 5 V data via level shifter)", "up", 20.0, "+5V",
             {"family": "JST-PH 2.0", "pins": 3, "type": "PHR-3; port and cable labelled LED 5V"},
             note="DIN is driven by U3 (74AHCT1G125 at 5 V) through R3 330 ohm from GPIO2. Same 3-pin PH family as J_IO: "
                  "a mis-plugged 3-pin IO module from this library sees 5 V (all of them accept 3.3-5 V) and its output meets "
                  "the buffer, never a GPIO; an LED plugged into J_IO just gets 3.3 V. Silkscreen 'LED 5V' and a red housing mark.")
    # ---- rear: USB-C power + I2C3
    usb_x, usb_y = round(W / 2, 3), 74.75
    add_part("J_PWR", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", usb_x, usb_y, 180, role="power_in", slot="J_PWR",
             note="opening faces +y (rear); front face 0.3 mm past the board edge (estimate, check the HRO drawing)")
    pwr_pins = [{"pin": p, "name": n, "net": net, "gpio": None, **({} if net else {"kind": "nc"})} for p, n, net in [
        ("A1", "GND", "GND"), ("B1", "GND", "GND"), ("A12", "GND", "GND"), ("B12", "GND", "GND"),
        ("A4", "VBUS", "VBUS"), ("B4", "VBUS", "VBUS"), ("A9", "VBUS", "VBUS"), ("B9", "VBUS", "VBUS"),
        ("A5", "CC1", "CC1"), ("B5", "CC2", "CC2"), ("A6", "D+", "USB_DP"), ("B6", "D+", "USB_DP"),
        ("A7", "D-", "USB_DN"), ("B7", "D-", "USB_DN"), ("A8", "SBU1", None), ("B8", "SBU2", None), ("S1", "SHIELD", "GND")]]
    SLOTS.append({
        "id": "J_PWR", "kind": "power_in", "title": "USB-C receptacle (power + native USB for flashing)", "accepts_roles": ["power_in"],
        "module": "USBC_RECEPTACLE_GCT_USB4105", "footprint": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
        "alt_footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
        "at": [usb_x, usb_y], "rotation_deg": 180, "kicad": {"x": usb_x, "y": round(H - usb_y, 3), "rot": 180},
        "courtyard": court("Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", usb_x, usb_y, 180),
        "facing": "rear (+y), board edge", "edge": "rear", "pins": pwr_pins,
        "opening_center_board": [usb_x, H], "receptacle_center_above_board_top": 1.6,
        "shell_opening_mm": {"w": 13.0, "h": 7.0, "corner_r": 2.5, "why": "plug overmold estimate from library row 03; confirm with the shipped cable"},
        "height_envelope_mm": {"above_board_top": 3.3, "why": "receptacle body (estimate)"},
    })
    add_port("J_I2C3", PH.format(n=4), 40.15, 73.0, 0, i2c_pins(), i2c_roles, "I2C port 3", "up", 20.0, "+3V3", i2c_cable)
    # ---- support parts (suggested places; PCB stage may move them inside their zone)
    # (ref, footprint, x, y, rot, value, group, {pad: net}); pinouts: 74AHCT1G125 SOT-23-5 1=OE# 2=A 3=GND 4=Y 5=VCC,
    # USBLC6-2SC6 1=I/O1 2=GND 3=I/O2 4=I/O2 5=VBUS 6=I/O1 (standard pinouts; verify against the chosen MPN datasheet)
    sup = [
        ("F1", "Fuse:Fuse_1812_4532Metric", 31.4, 62.0, 0, "PTC fuse 1.5 A hold, >= 6 V (1812)", "power", {1: "VBUS", 2: "+5V"}),
        ("D1", "Diode_SMD:D_SMA", 22.5, 62.0, 0, "TVS 5.0 V standoff, unidirectional (SMAJ5.0A class); pad 1 = cathode", "power", {1: "+5V", 2: "GND"}),
        ("C1", "Capacitor_SMD:C_0805_2012Metric", 38.0, 62.0, 0, "10 uF 25 V X5R", "power", {1: "+5V", 2: "GND"}),
        ("C2", "Capacitor_SMD:C_0603_1608Metric", 41.5, 62.0, 0, "100 nF 50 V X7R", "power", {1: "+5V", 2: "GND"}),
        ("R1", "Resistor_SMD:R_0603_1608Metric", 22.0, 72.0, 90, "5.1 k 1% (CC1 to GND)", "power", {1: "CC1", 2: "GND"}),
        ("R2", "Resistor_SMD:R_0603_1608Metric", 24.5, 72.0, 90, "5.1 k 1% (CC2 to GND)", "power", {1: "CC2", 2: "GND"}),
        ("U2", "Package_TO_SOT_SMD:SOT-23-6", 53.5, 72.0, 0, "USBLC6-2SC6 USB ESD array", "usb",
         {1: "USB_DP", 2: "GND", 3: "USB_DN", 4: "USB_DN", 5: "+5V", 6: "USB_DP"}),
        ("U3", "Package_TO_SOT_SMD:SOT-23-5", 14.0, 55.0, 0, "74AHCT1G125 buffer at +5V (LED data level shift; OE# tied low)", "led",
         {1: "GND", 2: "LED_DIN_3V3", 3: "GND", 4: "LED_DIN_BUF", 5: "+5V"}),
        ("R3", "Resistor_SMD:R_0603_1608Metric", 18.5, 55.0, 0, "330 ohm (LED DIN series)", "led", {1: "LED_DIN_BUF", 2: "LED_DIN_5V"}),
        ("C5", "Capacitor_SMD:C_0603_1608Metric", 14.0, 51.5, 0, "100 nF (U3 decoupling)", "led", {1: "+5V", 2: "GND"}),
        ("C4", "Capacitor_SMD:C_1206_3216Metric", 48.0, 55.0, 0, "22 uF 10 V X5R (J_LED 5 V bulk)", "led", {1: "+5V", 2: "GND"}),
        ("R4", "Resistor_SMD:R_0603_1608Metric", 26.0, 55.0, 0, "4.7 k (I2C SDA pull-up to +3V3)", "i2c", {1: "I2C_SDA", 2: "+3V3"}),
        ("R5", "Resistor_SMD:R_0603_1608Metric", 26.0, 52.5, 0, "4.7 k (I2C SCL pull-up to +3V3)", "i2c", {1: "I2C_SCL", 2: "+3V3"}),
        ("R7", "Resistor_SMD:R_0603_1608Metric", 36.0, 55.0, 0, "100 k (AMP_SD pull-down: amp stays off until firmware enables it)", "audio",
         {1: "AMP_SD", 2: "GND"}),
        ("C3", "Capacitor_SMD:C_1206_3216Metric", 42.0, 55.0, 0, "22 uF 10 V X5R (A1 VIN bulk)", "audio", {1: "+5V", 2: "GND"}),
    ]
    for ref, fp, x, y, r, val, grp, pins in sup:
        p = add_part(ref, fp, x, y, r, role="support", value=val, note="suggested position; keep inside under_devkit_zone or near its slot")
        p["group"] = grp
        p["pins"] = {str(k): v for k, v in pins.items()}
    # ---- mounting holes and test pads (bottom side)
    for hid, x, y in HOLES:
        add_part(hid, "MountingHole:MountingHole_2.7mm_M2.5", x, y, 0, role="mounting_hole")
    tps = [("TP1", "+5V"), ("TP2", "+3V3"), ("TP3", "GND"), ("TP4", "EN"), ("TP5", "UART0_TX"), ("TP6", "UART0_RX"), ("TP7", "BOOT_IO0")]
    for k, (ref, net) in enumerate(tps):
        tp = add_part(ref, "TestPoint:TestPoint_Pad_D1.5mm", 12.0 + 5.08 * k, 50.0, 0, side="bottom", role="test_pad", value=net,
                      note="bottom side, 5.08 mm grid for a pogo jig; reach from below before the cartridge goes into the shell")
        tp["group"] = "test"
        tp["pins"] = {"1": net}


WAIVERS = [{"J_A1P", "J_A1N"}]
DRC_WAIVERS = [{"refs": ["J_A1P", "J_A1N"], "rule": "courtyards_overlap",
                "why": "single 1x1 sockets at the amp's 3.5 mm terminal pitch: courtyards overlap 0.05 mm, housings clear by 0.96 mm"}]


def overlaps(a, b, gap=0.0):
    return a[0] < b[1] + gap and b[0] < a[1] + gap and a[2] < b[3] + gap and b[2] < a[3] + gap


def checks() -> list[str]:
    issues = []
    top = [p for p in PARTS if p["side"] == "top"]
    # board edge
    for p in PARTS:
        c = p["courtyard"]
        if p["ref"] == "J_PWR":
            if c[0] < 0 or c[1] > W or c[2] < 0:
                issues.append(f"{p['ref']} courtyard outside board {c}")
            continue
        if c[0] < 0 or c[1] > W or c[2] < 0 or c[3] > H:
            issues.append(f"{p['ref']} courtyard outside board {c}")
    # courtyard overlaps (top side)
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            if {top[i]["ref"], top[j]["ref"]} in WAIVERS:
                continue
            if overlaps(top[i]["courtyard"], top[j]["courtyard"]):
                issues.append(f"courtyard overlap {top[i]['ref']} / {top[j]['ref']}")
    # screw heads vs parts
    r = SCREW_HEAD_D / 2 + 0.3
    for hid, x, y in HOLES:
        box = [x - r, x + r, y - r, y + r]
        for p in top:
            if p["role"] != "mounting_hole" and overlaps(box, p["courtyard"]):
                issues.append(f"screw head {hid} hits {p['ref']}")
    # vertical plug access: ports must not sit under elevated modules / devkit
    elevated = {"A1": next(s["module_plan"] for s in SLOTS if s["id"] == "A1"),
                "S1": next(s["module_plan"] for s in SLOTS if s["id"] == "S1"),
                "U1": next(s["devkit_pcb_plan"] for s in SLOTS if s["id"] == "U1"),
                "U1_ANT": next(s["antenna_plan"] for s in SLOTS if s["id"] == "U1")}
    for s in SLOTS:
        if s["kind"] != "port":
            continue
        for k, e in elevated.items():
            if overlaps(s["courtyard"], e):
                issues.append(f"port {s['id']} is under {k}")
    # elevated modules must not collide with each other
    names = list(elevated)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if {names[i], names[j]} == {"U1", "U1_ANT"}:
                continue
            if overlaps(elevated[names[i]], elevated[names[j]]):
                issues.append(f"elevated {names[i]} overlaps {names[j]}")
    # support parts under elevated items must be low (all SMD here)
    return issues


def cartridge() -> dict:
    u1 = next(s for s in SLOTS if s["id"] == "U1")
    xmin = min(u1["antenna_plan"][0], 0.0)
    xmax = max(u1["usb_end_x"] + 1.0, W)  # devkit micro-USB shells stick out ~1 mm (estimate)
    ymax = H + 0.3
    return {
        "board_top_above_floor": 8.0,
        "board_bottom_above_floor": round(8.0 - T, 2),
        "boss": {"od": 7.2, "height_above_floor": round(8.0 - T, 2), "insert_hole_d": 4.0, "insert_hole_depth": 5.0,
                 "insert": "INSERT_HEATSET_M2_5X4_STD", "screw": "M2.5 x 6 socket head (SCREW_SET_M2_M2_5_SHCS_A2)",
                 "screw_head_d": SCREW_HEAD_D, "why": "CNC Kitchen table via library row 10: hole 4.0, wall >= 1.6 -> OD >= 7.2; "
                 "1.6 board + 4.0 insert -> 6 mm screw (head size estimate)"},
        "holes": [{"id": h, "at": [x, y], "d": 2.7} for h, x, y in HOLES],
        "support_posts": [
            {"id": "S1_POST1", "board_hole": {"at": [3.0, 11.0], "d": 4.5}, "post_d": 4.0, "top_above_board_top": 11.0,
             "pilot_hole_d": 1.8, "screw": "M2 x 6 into the post through the S1 module corner hole (hole position estimated)"},
            {"id": "S1_POST2", "board_hole": {"at": [3.0, 30.0], "d": 4.5}, "post_d": 4.0, "top_above_board_top": 11.0,
             "pilot_hole_d": 1.8, "screw": "M2 x 6 (as above)"},
        ],
        "keepout_plan_board": [round(xmin, 2), round(xmax, 2), 0.0, round(ymax, 2)],
        "keepout_height_above_floor": 32.0,
        "keepout_bottom_above_floor": round(8.0 - T - 1.5, 2),
        "keepout_why": "top: board 8.0 + tallest envelope 20.0 (PH plug + wire bend, estimate) rounded up with margin; bottom: "
                       "board underside minus 1.5 mm for through-hole pin ends. Below that only the 4 bosses reach the floor.",
        "clearance_mm": 3.0,
        "envelopes": [{"id": s["id"], "plan": s.get("module_plan") or s.get("devkit_pcb_plan") or s["courtyard"],
                       "z_above_board_top": [0.0, s["height_envelope_mm"]["above_board_top"]]} for s in SLOTS],
        "antenna_keepout": {"plan_board": [round(u1["antenna_plan"][0] - 5, 2), round(u1["antenna_plan"][1] + 3, 2),
                                           round(u1["antenna_plan"][2] - 5, 2), round(u1["antenna_plan"][3] + 5, 2)],
                            "rule": "no metal (screws, inserts, magnets, speaker, metal paint) within 15 mm of the antenna; plastic wall only. "
                                    "15 mm is from secondary sources (library rows 01/12); verify against the WROOM-1 datasheet.",
                            "copper_keepout_board": [0.0, 8.0, round(Y0, 2), round(Y0 + DK_PCB_W, 2)]},
        "placement_rule": "Board lies flat (rotation 0: board +x = world +X, board +y = world +Y). Board centre x = world x 0. "
                          "Board slides as far toward the rear (+Y) as the inner shell allows: the J_PWR front face sits 0.2 mm "
                          "inside the inner rear wall surface, and the whole keep-out box keeps clearance_mm to every inner wall. "
                          "World point = (bx - W/2, by + y_offset, floor_z + 6.4 + bz). gstudio.normalize writes the chosen "
                          "board_origin into spec.derived.cartridge.",
        "rear_gap_mm": 0.2,
        "usb_c_opening": {"center_board_x": round(W / 2, 3), "center_above_board_top": 1.6, "w": 13.0, "h": 7.0, "corner_r": 2.5},
        "factory_access": "DevKitC micro-USB ports face +X (robot's left) for bench flashing before the cartridge goes in; "
                          "production flashing uses the rear USB-C (native USB on GPIO19/20) or the bottom test pads TP1..TP7.",
    }


def power() -> dict:
    return {
        "rails": [
            {"name": "VBUS", "source": "USB-C J_PWR VBUS pins (sink only)", "note": "before the fuse"},
            {"name": "+5V", "source": "VBUS -> F1 (PTC 1.5 A hold) -> +5V; D1 TVS to GND after F1; C1 10 uF + C2 100 nF",
             "feeds": ["U1 5V pin (J1-21)", "A1 VIN", "S1 VCC", "J_LED 5V", "U3 VCC"]},
            {"name": "+3V3", "source": "DevKitC on-board LDO SGM2212-3.3 (fed from its VCC_5V net, which is the header 5V pin)",
             "feeds": ["J_I2C1..3", "J_DISP", "J_MIC", "J_IO1..3", "R4/R5 pull-ups"]},
        ],
        "cc": "CC1 (A5) and CC2 (B5) each through their own 5.1 k to GND (R1, R2): USB-C sink advertisement.",
        "usb_data": "D+ (A6/B6) and D- (A7/B7) through U2 USBLC6-2SC6 to GPIO20/GPIO19 (DevKitC J3-19/J3-20): the rear port "
                    "flashes and talks to the ESP32-S3 native USB. The DevKitC 'USB' micro port shares the same pins, so only one "
                    "of the two may be connected at a time (physical check still needed for the stub).",
        "devkit_isolation": {
            "finding": "On the DevKitC-1 v1.1 schematic (SCH_ESP32-S3-DevKitC-1_V1.1_20221130.pdf) each micro-USB VBUS reaches the "
                       "VCC_5V net through its own Schottky diode (D1 and D7, 1N5819HW-7-F). The header 5V pin (J1-21) is VCC_5V, "
                       "which feeds the SGM2212-3.3 LDO.",
            "consequence": "Backplane +5V into J1-21 cannot back-feed a PC on the DevKitC's own USB ports: those diodes block it. "
                           "If someone powers only from a DevKitC USB port, about VBUS - 0.4 V reaches the backplane +5V and the "
                           "rear USB-C VBUS pins.",
            "decision": "No series diode between F1 and +5V. A Schottky would drop about 0.4-0.5 V at 1-1.5 A and push the "
                        "Catalex microSD module (4.5-5.5 V) out of range. Factory rule: never connect the DevKitC USB and the "
                        "rear USB-C at the same time; flash through the rear USB-C. Fit a low-drop ideal-diode stage in a later "
                        "revision if the factory needs both ports at once.",
        },
        "limits": {
            "usb_source_ma": 3000, "usb_source_note": "approved 5.1 V 3 A adapter (library row 04); a computer port may give only 500-900 mA",
            "fuse_hold_ma": 1500,
            "limit_5v_ma": 1350, "limit_5v_why": "90% of the 1.5 A PTC hold current (also under 80% of a 3 A source)",
            "ldo_3v3_rating_ma": 800, "ldo_3v3_source": "SG Micro SGM2212 product page: 800 mA output current capability",
            "limit_3v3_ma": 600, "limit_3v3_why": "75% of the LDO rating; average dissipation (V5 - 3.3) x I must also stay low (SOT-223, estimate)",
        },
        "firmware_rules": ["cap LED brightness so the LED port stays under its budget", "limit amp volume when the budget is tight",
                           "keep AMP_SD low (amp off) when idle"],
    }


def main() -> int:
    build()
    issues = checks()
    doc = {
        "schema": "gadget-backplane/1",
        "id": "backplane-v1",
        "version": "1.0.0",
        "status": "design data, not fabricated: every physical item still needs a first-article check",
        "units": "mm",
        "frame": {
            "origin": "board lower-left corner, top view", "x": "world +X (robot's left)", "y": "world +Y (rear)",
            "rotation": "degrees counter-clockwise seen from the top (same value as KiCad)",
            "kicad": "kicad_x = x, kicad_y = board.size[1] - y with the board's top-left at the KiCad origin",
            "pin_at": "pin coordinates are board-frame positions of the pad centres",
        },
        "board": {
            "size": [W, H], "thickness": T, "layers": 2, "material": "FR-4", "copper_oz": 1, "corner_radius": 1.0,
            "outline": [[0, 0], [W, 0], [W, H], [0, H]],
            "holes": [{"id": h, "at": [x, y], "d": 2.7, "footprint": "MountingHole:MountingHole_2.7mm_M2.5"} for h, x, y in HOLES]
                     + [{"id": "S1_POST1", "at": [3.0, 11.0], "d": 4.5, "footprint": None, "note": "NPTH for the printed S1 support post"},
                        {"id": "S1_POST2", "at": [3.0, 30.0], "d": 4.5, "footprint": None, "note": "NPTH for the printed S1 support post"}],
            "why_size": "The DevKitC antenna must overhang a board edge (library rows 01/02), so the board is no longer than the "
                        "DevKitC (62.865) minus the socket courtyard margin: 62.8 wide. Depth 78.6 holds the amp, microSD module, "
                        "11 ports and the rear USB-C without stacking parts under the modules' plug paths.",
        },
        "core": {"slot": "U1", "module": "CORE_ESP32S3_DEVKITC1_N8R8", "variants_ok": ["N8R8 (Espressif)", "N16R8 clones (measure first)"],
                 "rgb_led_gpio": {"v1.1": 38, "v1.0_and_clones": 48}},
        "gpio": GPIO,
        "gpio_reserved": RESERVED,
        "gpio_free": [{"gpio": 1, "note": "ADC1_CH0/TOUCH1; routed to nothing in v1 (kept for a later 5 V servo port once a keyed family is approved)"}],
        "buses": [
            {"type": "i2c", "sda": 8, "scl": 9, "pullups": "4.7 k to +3V3 (R4, R5)", "slots": ["J_I2C1", "J_I2C2", "J_I2C3"]},
            {"type": "spi", "host": "SPI2/FSPI", "sck": 12, "mosi": 11, "miso": 13, "cs": {"S1": 10, "J_DISP": 14},
             "note": "FSPI IO_MUX pins (CLK 12, D 11, Q 13); CS pins via GPIO matrix", "slots": ["S1", "J_DISP"]},
            {"type": "i2s_tx", "port": "I2S0", "bclk": 15, "ws": 16, "dout": 7, "enable": 39, "slots": ["A1"]},
            {"type": "i2s_rx", "port": "I2S1", "bclk": 41, "ws": 42, "din": 40, "slots": ["J_MIC"]},
            {"type": "rmt_led", "gpio": 2, "level_shift": "U3 74AHCT1G125 at 5 V", "slots": ["J_LED"]},
            {"type": "gpio", "slots": {"J_IO1": 4, "J_IO2": 5, "J_IO3": 6}},
            {"type": "usb", "dp": 20, "dn": 19, "slots": ["J_PWR"]},
        ],
        "slots": SLOTS,
        "parts": PARTS,
        "power": power(),
        "test_pads": [p for p in PARTS if p["role"] == "test_pad"],
        "cartridge": cartridge(),
        "support_groups": {"power": "always", "usb": "always", "test": "always", "led": "when J_LED is used",
                           "i2c": "when any J_I2C port is used", "audio": "when A1 is used"},
        "always_nets": ["GND", "+5V", "+3V3", "VBUS", "CC1", "CC2", "EN", "UART0_TX", "UART0_RX", "BOOT_IO0", "USB_DP", "USB_DN"],
        "net_classes": {"power": ["VBUS", "+5V", "GND"], "power_track_mm": 0.8, "signal_track_mm": 0.25, "clearance_mm": 0.2},
        "checks": {"layout_issues": issues, "checked_by": "library/tools/gen_backplane_v1.py",
                   "rules": ["courtyards inside the board", "no top courtyard overlaps", "screw heads clear parts",
                             "ports not under elevated modules or the devkit", "elevated modules do not collide"]},
        "drc_waivers": DRC_WAIVERS,
        "evidence": [
            "Espressif ESP32-S3-DevKitC-1 v1.1 user guide (esp-dev-kits latest, read 2026-11-01): J1/J3 header tables; RGB LED GPIO38 (v1.1) vs GPIO48 (v1.0); GPIO35/36/37 used by Octal flash/PSRAM; power options USB / 5V pin / 3V3 pin",
            "Espressif DXF_ESP32-S3-DevKitC-1_V1.1_20220429.dxf parsed: BOARD_OUTLINE 25.40 x 62.865; header pads TOOL_BOT at x 1.27 / 24.13, y 7.96..61.30 step 2.54; module outline to y 69.15",
            "Espressif SCH_ESP32-S3-DevKitC-1_V1.1_20221130.pdf: VBUS -> D1 1N5819HW-7-F -> VCC_5V (UART port), VBUSB -> D7 1N5819HW-7-F -> VCC_5V (ESP USB port); J1 pin 21 = VCC_5V; U2 SGM2212-3.3XKC3G/TR VIN = VCC_5V",
            "SG Micro SGM2212 product page: '800mA output current capability', input 2.7 V to 20 V",
            "KiCad 9.0.9 footprints present on the build VM (/usr/share/kicad/footprints), courtyards read 2026-11-01",
            "library rows 21 (Adafruit 3006 board file: header y 2.54, VO pads y 15.49 at x 7.09/10.59) and 37 (Catalex-type 42 x 24, estimates)",
        ],
        "uncertain": [
            "DevKitC seated height (socket 8.5 + spacer 2.5) is an estimate; measure on the first article",
            "S1 module header position and pin order are estimates (library row 37)",
            "amp VO pin trick (2.54 pins in 3.5 mm terminal pads) is untested",
            "USB-C receptacle front-face position relative to the board edge (HRO drawing not read)",
            "USB D+/D- stub shared with the DevKitC's own USB connector: works at full speed in principle; physical check still needed",
            "antenna keep-out distance 15 mm comes from secondary sources",
            "PTC/TVS/ESD/level-shifter parts are classes, not chosen MPNs; values follow ARCHITECTURE §6",
        ],
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes); layout issues: {len(issues)}")
    for i in issues:
        print("  -", i)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
