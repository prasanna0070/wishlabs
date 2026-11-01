"""Provisional module geometry, used ONLY until library/modules.json and library/backplane-v1.json provide values.

Every number here is a planning estimate, not a measured or datasheet-verified value. Each entry carries
`provisional: True`, and `gstudio.cad.params` records which modules fell back to this table, so the
outputs say "provisional dimensions" for them. The adapter in `params.py` prefers library values field by
field and only fills what the library does not give.

Local module frame (all module descriptors):
  u = board width axis, v = board height axis, board front face at z = 0 facing +z (out of the gadget).
  `front` items sit on the front face (z >= 0), `back` items behind the board (z <= -t).
"""

from __future__ import annotations

PCB_GREEN = (0.06, 0.36, 0.20)
PCB_BLUE = (0.08, 0.24, 0.55)
PCB_BLACK = (0.09, 0.09, 0.10)
PCB_PURPLE = (0.33, 0.16, 0.45)
HEADER_BLACK = (0.08, 0.08, 0.09)
JST_WHITE = (0.93, 0.91, 0.84)
METAL = (0.74, 0.75, 0.77)
GLASS = (0.03, 0.04, 0.06)
CONE = (0.11, 0.11, 0.12)
MAGNET = (0.58, 0.59, 0.61)
LED_WHITE = (0.97, 0.97, 0.94)
CHIP = (0.12, 0.12, 0.13)

# ----------------------------------------------------------------------------------------------- fasteners
FASTENERS = {
    "provisional": True,
    "screw": {"d": 2.5, "clear_d": 2.8, "head_d": 4.6, "head_h": 1.8, "lengths": [4, 6, 8, 10, 12, 16]},
    "insert": {"thread": "M2.5", "od": 3.9, "length": 4.0, "hole_d": 3.5, "hole_depth": 5.0, "boss_d": 7.2},
    "foot": {"d": 10.0, "h": 3.0, "seat_d": 11.0, "seat_depth": 0.8},
    "tie": {"w": 2.5, "t": 1.0, "slot_w": 3.6, "slot_h": 1.8},
    "masses_g_estimate": {"screw": 0.25, "insert": 0.35, "foot": 0.4},
}

# ----------------------------------------------------------------------------------------------- modules
# kind -> descriptor. `match` lists lowercase substrings used to map a library id/name to this kind.
MODULES: dict[str, dict] = {
    "esp32s3_devkitc1": {
        "role": "core", "name": "ESP32-S3-DevKitC-1 N16R8", "match": ["devkitc", "esp32-s3", "esp32s3", "esp32_s3"],
        "pcb": {"w": 62.7, "h": 25.4, "t": 1.6, "color": PCB_BLACK},
        "front": [
            {"name": "Module", "kind": "box", "c": [-18.5, 0.0], "size": [18.0, 25.5, 0.8], "color": PCB_BLUE},
            {"name": "ModuleCan", "kind": "box", "c": [-17.0, 0.0], "size": [15.8, 17.6, 2.4], "z0": 0.8, "color": METAL},
            {"name": "UsbA", "kind": "box", "c": [28.6, -6.3], "size": [7.4, 8.9, 3.2], "color": METAL},
            {"name": "UsbB", "kind": "box", "c": [28.6, 6.3], "size": [7.4, 8.9, 3.2], "color": METAL},
            {"name": "Buttons", "kind": "box", "c": [20.0, 0.0], "size": [3.5, 14.0, 1.5], "color": CHIP},
        ],
        "back": [
            {"name": "PinsA", "kind": "box", "c": [0.0, -11.43], "size": [55.9, 0.64, 6.0], "color": METAL},
            {"name": "PinsB", "kind": "box", "c": [0.0, 11.43], "size": [55.9, 0.64, 6.0], "color": METAL},
        ],
        "header_rows": {"pins": 22, "pitch": 2.54, "row_v": [-11.43, 11.43]},
        "mass_g": 7.0, "provisional": True,
    },
    "max98357a": {
        "role": "amp", "name": "MAX98357A I2S amplifier breakout", "match": ["max98357"],
        "pcb": {"w": 19.3, "h": 17.8, "t": 1.6, "color": PCB_PURPLE},
        "front": [
            {"name": "Chip", "kind": "box", "c": [0, 1.0], "size": [3.0, 3.0, 0.9], "color": CHIP},
            {"name": "Terminal", "kind": "box", "c": [0, 6.0], "size": [7.6, 6.5, 8.0], "color": (0.15, 0.45, 0.75)},
        ],
        "back": [{"name": "Pins", "kind": "box", "c": [0, -7.6], "size": [17.8, 0.64, 6.0], "color": METAL}],
        "mass_g": 2.0, "provisional": True,
    },
    "microsd_spi": {
        "role": "storage", "name": "microSD SPI module", "match": ["microsd", "micro_sd", "sd_card", "sdcard"],
        "pcb": {"w": 24.0, "h": 42.0, "t": 1.6, "color": PCB_BLUE},
        "front": [
            {"name": "Socket", "kind": "box", "c": [0, 8.0], "size": [14.5, 15.0, 1.9], "color": METAL},
            {"name": "Regulator", "kind": "box", "c": [-5, -8.0], "size": [6.5, 3.5, 1.6], "color": CHIP},
            {"name": "Shifter", "kind": "box", "c": [4, -8.0], "size": [5.0, 4.0, 1.4], "color": CHIP},
        ],
        "back": [{"name": "Pins", "kind": "box", "c": [0, -19.5], "size": [15.2, 0.64, 6.0], "color": METAL}],
        "mass_g": 4.0, "provisional": True,
    },
    # ---------------------------------------------------------------- displays
    "oled096": {
        "role": "display", "name": "SSD1306 0.96in OLED 128x64 I2C", "match": ["ssd1306", "oled_096", "oled096", "0.96", "096"],
        "pcb": {"w": 27.3, "h": 27.8, "t": 1.2, "color": PCB_BLUE},
        "glass": {"w": 26.7, "h": 19.3, "t": 1.5, "c": [0.0, -1.2]},
        "active": {"w": 21.74, "h": 10.86, "c": [0.0, 0.6]},
        "back": [{"name": "Header", "kind": "box", "c": [0, 11.6], "size": [10.2, 2.5, 8.5], "color": HEADER_BLACK},
                 {"name": "Parts", "kind": "box", "c": [0, -2.0], "size": [14.0, 8.0, 1.2], "color": CHIP}],
        "connector": {"c": [0, 11.6], "side": "back", "label": "4-pin header"},
        "mass_g": 4.0, "provisional": True,
    },
    "oled130": {
        "role": "display", "name": "SH1106 1.3in OLED 128x64 I2C", "match": ["sh1106", "oled_130", "oled130", "1.3"],
        "pcb": {"w": 35.4, "h": 33.5, "t": 1.2, "color": PCB_BLUE},
        "glass": {"w": 34.5, "h": 23.0, "t": 1.6, "c": [0.0, -1.5]},
        "active": {"w": 29.42, "h": 14.70, "c": [0.0, 0.5]},
        "back": [{"name": "Header", "kind": "box", "c": [0, 14.3], "size": [10.2, 2.5, 8.5], "color": HEADER_BLACK},
                 {"name": "Parts", "kind": "box", "c": [0, -3.0], "size": [18.0, 9.0, 1.2], "color": CHIP}],
        "connector": {"c": [0, 14.3], "side": "back", "label": "4-pin header"},
        "mass_g": 6.0, "provisional": True,
    },
    "st7789": {
        "role": "display", "name": "ST7789 1.54in IPS 240x240 SPI", "match": ["st7789", "ips", "tft", "color_display", "colour"],
        "pcb": {"w": 31.0, "h": 43.0, "t": 1.6, "color": PCB_BLUE},
        "glass": {"w": 31.0, "h": 33.5, "t": 2.0, "c": [0.0, -1.5]},
        "active": {"w": 27.72, "h": 27.72, "c": [0.0, -1.5]},
        "back": [{"name": "Header", "kind": "box", "c": [0, 18.8], "size": [20.3, 2.5, 8.5], "color": HEADER_BLACK},
                 {"name": "Parts", "kind": "box", "c": [0, -4.0], "size": [16.0, 10.0, 1.4], "color": CHIP}],
        "connector": {"c": [0, 18.8], "side": "back", "label": "8-pin header"},
        "mass_g": 9.0, "provisional": True,
    },
    # ---------------------------------------------------------------- lights
    "ws2812_mini": {
        "role": "led", "name": "WS2812B single addressable LED board", "match": ["ws2812b_mini", "ws2812_mini", "led_mini", "single"],
        "pcb": {"w": 12.0, "h": 12.0, "t": 1.0, "color": PCB_BLACK},
        "front": [{"name": "Led", "kind": "box", "c": [0, 0], "size": [5.0, 5.0, 1.6], "color": LED_WHITE}],
        "back": [{"name": "Pads", "kind": "box", "c": [0, -3.5], "size": [8.0, 2.5, 3.0], "color": JST_WHITE}],
        "connector": {"c": [0, -3.5], "side": "back", "label": "3-pin"},
        "leds": 1, "mass_g": 1.0, "provisional": True,
    },
    "ws2812_ring12": {
        "role": "led", "name": "WS2812B 12-LED ring", "match": ["ring12", "ring_12", "12led", "12_led"],
        "ring": {"od": 37.0, "id": 23.0}, "pcb": {"w": 37.0, "h": 37.0, "t": 1.6, "color": PCB_BLACK},
        "connector": {"c": [0, -15.0], "side": "back", "label": "3-pin"},
        "leds": 12, "mass_g": 4.0, "provisional": True,
    },
    "ws2812_ring16": {
        "role": "led", "name": "WS2812B 16-LED ring", "match": ["ring16", "ring_16", "16led", "16_led"],
        "ring": {"od": 44.5, "id": 31.8}, "pcb": {"w": 44.5, "h": 44.5, "t": 1.6, "color": PCB_BLACK},
        "connector": {"c": [0, -19.0], "side": "back", "label": "3-pin"},
        "leds": 16, "mass_g": 5.0, "provisional": True,
    },
    "ws2812_ring24": {
        "role": "led", "name": "WS2812B 24-LED ring", "match": ["ring24", "ring_24", "24led", "24_led"],
        "ring": {"od": 66.0, "id": 52.6}, "pcb": {"w": 66.0, "h": 66.0, "t": 1.6, "color": PCB_BLACK},
        "connector": {"c": [0, -29.5], "side": "back", "label": "3-pin"},
        "leds": 24, "mass_g": 8.0, "provisional": True,
    },
    # ---------------------------------------------------------------- audio
    "speaker40": {
        "role": "speaker", "name": "40 mm 4 ohm speaker", "match": ["40mm", "40_mm", "spk40", "speaker_40"],
        "speaker": {"d": 40.0, "depth": 20.0, "rim_t": 1.2, "rim_w": 3.0, "magnet_d": 22.0, "magnet_h": 8.0, "cone_d": 33.0},
        "connector": {"c": [16.0, 0.0], "side": "back", "label": "solder tabs + JST-PH 2p lead"},
        "mass_g": 22.0, "provisional": True,
    },
    "speaker28": {
        "role": "speaker", "name": "28 mm 8 ohm speaker", "match": ["28mm", "28_mm", "spk28", "speaker_28"],
        "speaker": {"d": 28.0, "depth": 14.0, "rim_t": 1.0, "rim_w": 2.5, "magnet_d": 15.0, "magnet_h": 6.0, "cone_d": 22.0},
        "connector": {"c": [11.0, 0.0], "side": "back", "label": "solder tabs + JST-PH 2p lead"},
        "mass_g": 8.0, "provisional": True,
    },
    "inmp441": {
        "role": "mic", "name": "INMP441 I2S MEMS microphone", "match": ["inmp441", "mems_mic", "i2s_mic"],
        "pcb": {"w": 14.0, "h": 14.0, "t": 1.0, "color": PCB_BLUE},
        "port": {"c": [0.0, 0.0], "d": 1.0},
        "back": [{"name": "Mic", "kind": "box", "c": [0, 0], "size": [4.7, 3.8, 1.1], "color": METAL},
                 {"name": "Header", "kind": "box", "c": [0, -5.6], "size": [15.2, 2.5, 8.5], "color": HEADER_BLACK}],
        "connector": {"c": [0, -5.6], "side": "back", "label": "6-pin header"},
        "mass_g": 1.0, "provisional": True,
    },
    # ---------------------------------------------------------------- inputs
    "arcade24": {
        "role": "button", "name": "24 mm snap-in arcade push button", "match": ["24mm", "24_mm", "arcade_24", "arcade24"],
        "snap_button": {"hole_d": 24.0, "panel_t": [1.5, 4.0], "flange_d": 30.0, "flange_h": 2.6, "cap_d": 20.0,
                        "cap_h": 3.8, "body_d": 23.5, "body_len": 28.0, "clip_w": 27.5},
        "connector": {"c": [0, 0], "side": "back", "label": "microswitch tabs + 3p lead"},
        "mass_g": 9.0, "provisional": True,
    },
    "arcade30": {
        "role": "button", "name": "30 mm snap-in arcade push button", "match": ["30mm", "30_mm", "arcade_30", "arcade30", "arcade"],
        "snap_button": {"hole_d": 30.0, "panel_t": [1.5, 4.0], "flange_d": 33.0, "flange_h": 2.8, "cap_d": 24.5,
                        "cap_h": 4.2, "body_d": 29.5, "body_len": 31.0, "clip_w": 33.5},
        "connector": {"c": [0, 0], "side": "back", "label": "microswitch tabs + 3p lead"},
        "mass_g": 12.0, "provisional": True,
    },
    "tactile12": {
        "role": "button", "name": "12 mm tactile button module", "match": ["tactile", "tact", "push_button_module"],
        "pcb": {"w": 20.0, "h": 16.0, "t": 1.6, "color": PCB_BLUE},
        "tactile": {"body": [12.0, 12.0, 3.6], "stem_d": 6.8, "stem_h": 3.7, "travel": 0.3},
        "back": [{"name": "Header", "kind": "box", "c": [0, -6.7], "size": [7.6, 2.5, 8.5], "color": HEADER_BLACK}],
        "connector": {"c": [0, -6.7], "side": "back", "label": "3-pin header"},
        "mass_g": 2.0, "provisional": True,
    },
    # ---------------------------------------------------------------- misc
    "sg90": {
        "role": "servo", "name": "SG90 micro servo", "match": ["sg90", "servo"],
        "servo": {"body": [22.8, 12.2, 22.7], "tab_w": 32.2, "tab_t": 2.5, "tab_z": 15.9, "shaft_off": 5.9, "shaft_d": 4.8,
                  "shaft_h": 4.0, "hole_pitch": 27.8},
        "connector": {"c": [0, 0], "side": "back", "label": "3-wire lead"},
        "mass_g": 9.0, "provisional": True,
    },
    "sensor_small": {
        "role": "sensor", "name": "small I2C sensor board", "match": ["bme", "bmp", "sht", "aht", "vl53", "bh1750", "ldr", "sensor", "rtc", "ds3231", "imu", "mpu"],
        "pcb": {"w": 15.0, "h": 12.0, "t": 1.6, "color": PCB_PURPLE},
        "front": [{"name": "Chip", "kind": "box", "c": [0, 1.0], "size": [3.0, 3.0, 1.0], "color": CHIP}],
        "back": [{"name": "Header", "kind": "box", "c": [0, -4.7], "size": [10.2, 2.5, 8.5], "color": HEADER_BLACK}],
        "connector": {"c": [0, -4.7], "side": "back", "label": "4-pin header"},
        "mass_g": 1.0, "provisional": True,
    },
}

# ----------------------------------------------------------------------------------------------- backplane v1
# Board coords: origin at the board's lower-left corner in TOP view, +x right, +y toward the REAR (+Y world).
BACKPLANE = {
    "id": "backplane-v1", "provisional": True,
    "board": {"w": 92.0, "h": 62.0, "t": 1.6, "color": (0.05, 0.30, 0.26)},
    "holes": [[3.5, 3.5], [88.5, 3.5], [3.5, 58.5], [88.5, 58.5]], "hole_d": 2.7,
    "board_top_above_floor": 8.0,
    "slots": {
        # id: position (board coords, centre), footprint size [w, d, h] above board, kind, facing
        "U1": {"kind": "devkit_socket", "c": [62.0, 40.0], "rot": 0, "socket_h": 8.5},
        "A1": {"kind": "amp_socket", "c": [14.0, 47.0], "rot": 0, "socket_h": 8.5, "pins": 7},
        "S1": {"kind": "sd_socket", "c": [16.0, 20.0], "rot": 0, "socket_h": 8.5, "pins": 6},
        "J_PWR": {"kind": "usb_c", "c": [40.0, 62.0 - 3.0], "facing": "+y", "overhang": 1.0},
        "J_SPK": {"kind": "jst_ph", "pins": 2, "c": [6.0, 33.0], "facing": "+z"},
        "J_I2C1": {"kind": "jst_ph", "pins": 4, "c": [38.0, 4.5], "facing": "+z"},
        "J_I2C2": {"kind": "jst_ph", "pins": 4, "c": [51.0, 4.5], "facing": "+z"},
        "J_DISP": {"kind": "jst_ph", "pins": 8, "c": [68.0, 4.5], "facing": "+z"},
        "J_MIC": {"kind": "jst_ph", "pins": 6, "c": [84.0, 13.0], "rot": 90, "facing": "+z"},
        "J_IO1": {"kind": "jst_ph", "pins": 3, "c": [86.0, 27.0], "rot": 90, "facing": "+z"},
        "J_IO2": {"kind": "jst_ph", "pins": 3, "c": [33.0, 15.0], "facing": "+z"},
        "J_IO3": {"kind": "jst_ph", "pins": 3, "c": [33.0, 25.0], "facing": "+z"},
        "J_LED": {"kind": "jst_ph", "pins": 3, "c": [33.0, 34.0], "facing": "+z"},
    },
    "devkit_overhang_x": 3.0,
}

# Default slot per role when electrical.json is not available yet (preview / early builds).
DEFAULT_SLOTS = {
    "display_i2c": ["J_I2C1", "J_I2C2"],
    "display_spi": ["J_DISP"],
    "mic": ["J_MIC"],
    "led": ["J_LED"],
    "button": ["J_IO1", "J_IO2", "J_IO3"],
    "servo": ["J_IO2", "J_IO3", "J_IO1"],
    "sensor": ["J_I2C2", "J_I2C1"],
    "speaker": ["J_SPK"],
}

JST_PH = {"pitch": 2.0, "body_extra": 3.9, "depth": 4.5, "height": 6.0, "plug_h": 9.6}
USB_C = {"w": 8.94, "d": 7.35, "h": 3.26, "plug_w": 8.4, "plug_h": 2.6, "overmold_w": 12.5, "overmold_h": 7.0,
         "plug_shell_len": 6.6}

# Density for mass estimates of printed parts (PLA), g/mm^3.
PLA_DENSITY = 1.24e-3
