"""Hardware binding: electrical.json + the module library -> include/pins.h and include/hardware.h.

pins.h holds GPIO numbers only, and every number comes from electrical.json (QB-19):
  * GS_GPIO_<SIGNAL>   for each entry of electrical["gpio"] (the backplane-v1 signal map)
  * GS_<REF>_<PIN>     for each instance pin that reaches a GPIO, through its connections (net -> gpio)
                       or, for slot-wide buses (I2C, SPI, I2S, LED, IO ports), through electrical["buses"]

hardware.h binds roles to those symbols (GS_PIN_BUTTON -> GS_BTN1_SIG) and carries the module facts the
drivers need (display kind, I2C addresses, pixel count, button polarity). It never contains a GPIO number.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..errors import StageError

# Module id -> firmware driver facts. Ids are the library's (library/modules-*.json). Facts quote the
# library's own firmware notes (addresses, polarity, pixel count); nothing here is a new measurement.
DRIVERS: dict[str, dict[str, Any]] = {
    "CORE_ESP32S3_DEVKITC1_N8R8": {"kind": "core", "flash_mb": 8, "psram_mb": 8},
    "OLED_096_SSD1306_I2C": {"kind": "display", "display": "SSD1306_128X64", "addr": 0x3C, "w": 128, "h": 64},
    "OLED_130_SH1106_I2C": {"kind": "display", "display": "SH1106_128X64", "addr": 0x3C, "w": 128, "h": 64},
    "LCD_200_ST7789_240X320_WAVESHARE": {"kind": "display", "display": "ST7789", "w": 240, "h": 320, "rotation": 1},
    "LED_WS2812B_1PX_MODULE": {"kind": "neopixel", "pixels": 1},
    "LED_RING_12_WS2812_50MM": {"kind": "neopixel", "pixels": 12},
    "MIC_INMP441_I2S": {"kind": "mic"},
    "AMP_MAX98357A_I2S": {"kind": "amp"},
    "SPK_40MM_4R_3W": {"kind": "speaker"},
    "SPK_28MM_8R_2W": {"kind": "speaker"},
    "BUZZER_PASSIVE_TRANSISTOR_MODULE": {"kind": "buzzer", "active_low": True},
    "BUTTON_TACTILE_KY004": {"kind": "button", "active_low": True},
    "BUTTON_ARCADE_30MM_SNAPIN": {"kind": "button", "active_low": True},
    "TOUCH_TTP223_SMALL_15X11": {"kind": "button", "active_low": False, "touch": True},
    "ENDSTOP_OMRON_SS01GL": {"kind": "button", "active_low": True},
    "HALL_KY003_A3144": {"kind": "button", "active_low": True},
    "MICROSD_SPI_MODULE_5V": {"kind": "sd"},
    "LIGHT_BH1750_GY302_I2C": {"kind": "sensor", "sensor": "BH1750", "addr": 0x23},
    "LIGHT_LDR_LM393_4PIN": {"kind": "ldr"},
    "TEMPHUM_AHT20_ADAFRUIT_4566": {"kind": "sensor", "sensor": "AHT20", "addr": 0x38},
    "BARO_BMP280_GY_3V3_I2C": {"kind": "sensor", "sensor": "BMP280", "addr": 0x76},
    "TOF_VL53L0X_POLOLU_2490": {"kind": "sensor", "sensor": "VL53L0X", "addr": 0x29},
    "RTC_DS3231_ZS042": {"kind": "sensor", "sensor": "DS3231", "addr": 0x68},
    "PIR_AM312_MINI": {"kind": "motion"},
    "PIR_HCSR501_STD": {"kind": "motion"},
    "SERVO_SG90_TOWERPRO": {"kind": "servo"},
    "IMU_MPU6050_GY521": {"kind": "unsupported", "why": "no behaviour template uses the IMU yet"},
    "ENCODER_KY040_PUSH": {"kind": "unsupported", "why": "the rotary encoder needs two signal pins; no template uses it yet"},
    "GEARMOTOR_N20_6V_LOWRPM": {"kind": "unsupported", "why": "no behaviour template drives motors yet"},
    "MOTOR_DRV_DRV8833_POLOLU_2130": {"kind": "unsupported", "why": "no behaviour template drives motors yet"},
    "FAN_3010_5V_GUARDED": {"kind": "unsupported", "why": "no behaviour template drives a fan yet"},
}

DISPLAY_KINDS = {"NONE": 0, "SSD1306_128X64": 1, "SSD1306_128X32": 2, "SH1106_128X64": 3, "ST7789": 4}

# Arduino libraries per driver, pinned exactly (registry versions checked 2026-11-01).
LIBS = {
    "json": ["bblanchon/ArduinoJson@7.4.3"],
    "neopixel": ["adafruit/Adafruit NeoPixel@1.15.5"],
    "u8g2": ["olikraus/U8g2@2.36.18"],
    "lovyangfx": ["lovyan03/LovyanGFX@1.2.33"],
    "AHT20": ["adafruit/Adafruit BusIO@1.17.4", "adafruit/Adafruit Unified Sensor@1.1.15", "adafruit/Adafruit AHTX0@2.0.6"],
    "BMP280": ["adafruit/Adafruit BusIO@1.17.4", "adafruit/Adafruit Unified Sensor@1.1.15", "adafruit/Adafruit BMP280 Library@3.0.0"],
    "BH1750": ["claws/BH1750@1.3.0"],
    "DS3231": ["adafruit/Adafruit BusIO@1.17.4", "adafruit/RTClib@2.1.4"],
    "VL53L0X": ["pololu/VL53L0X@1.3.1"],
}

PASSIVE_ROLES = {"power", "power_in", "cable", "usb_cable", "consumable", "fastener", "mechanical", "label", "backplane", "core"}

# Fallback by library role when a module id is not in DRIVERS (a new library entry).
ROLE_FALLBACK = {
    "display": {"kind": "display", "display": "SSD1306_128X64", "addr": 0x3C, "w": 128, "h": 64},
    "light": {"kind": "neopixel", "pixels": 1},
    "audio_in": {"kind": "mic"},
    "amp": {"kind": "amp"},
    "storage": {"kind": "sd"},
    "input": {"kind": "button", "active_low": True},
    "controller": {"kind": "core", "flash_mb": 8, "psram_mb": 8},
}

# Net name -> canonical pin name on an instance.
NET_PIN = [
    (re.compile(r"^I2C_SDA$"), "SDA"), (re.compile(r"^I2C_SCL$"), "SCL"),
    (re.compile(r"^SPI_SCK$"), "SCK"), (re.compile(r"^SPI_MOSI$"), "MOSI"), (re.compile(r"^SPI_MISO$"), "MISO"),
    (re.compile(r"^SD_CS$"), "CS"), (re.compile(r"^DISP_CS$"), "CS"), (re.compile(r"^DISP_DC$"), "DC"),
    (re.compile(r"^DISP_RST$"), "RST"), (re.compile(r"^DISP_BL$"), "BL"),
    (re.compile(r"^I2S_SPK_BCLK$"), "BCLK"), (re.compile(r"^I2S_SPK_LRC$"), "LRC"), (re.compile(r"^I2S_SPK_DIN$"), "DIN"),
    (re.compile(r"^AMP_SD$"), "SD"),
    (re.compile(r"^I2S_MIC_SCK$"), "SCK"), (re.compile(r"^I2S_MIC_WS$"), "WS"), (re.compile(r"^I2S_MIC_SD$"), "SD"),
    (re.compile(r"^IO\d+_SIG$"), "SIG"), (re.compile(r"^LED_DIN(_5V)?$"), "DIN"),
]

BUS_PINS = {  # bus type -> [(bus key, canonical pin)]
    "i2c": [("sda", "SDA"), ("scl", "SCL")],
    "spi": [("sck", "SCK"), ("mosi", "MOSI"), ("miso", "MISO")],
    "i2s_tx": [("bclk", "BCLK"), ("ws", "LRC"), ("lrc", "LRC"), ("dout", "DIN"), ("din", "DIN"), ("enable", "SD")],
    "i2s_rx": [("bclk", "SCK"), ("sck", "SCK"), ("ws", "WS"), ("din", "SD"), ("sd", "SD")],
    "rmt_led": [("gpio", "DIN")],
    "ws2812": [("gpio", "DIN")],
}


def ident(s: str) -> str:
    """C identifier piece: upper case, non-alphanumerics to '_'."""
    out = re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_").upper()
    return out or "X"


def _hex_addr(text: str) -> Optional[int]:
    m = re.search(r"0x([0-9A-Fa-f]{2})", text or "")
    return int(m.group(1), 16) if m else None


@dataclass
class Inst:
    ref: str
    module: str
    kind: str
    feature: Optional[str] = None
    slot: Optional[str] = None
    label: str = ""
    facts: dict = field(default_factory=dict)
    pins: dict = field(default_factory=dict)  # canonical pin -> (gpio, net or bus, via)


@dataclass
class Binding:
    instances: list[Inst]
    gpio: dict[str, int]
    core_module: str
    core_facts: dict
    warnings: list[str]
    lib_deps: list[str]
    electrical_sha256: str

    def by_kind(self, kind: str) -> list[Inst]:
        return [i for i in self.instances if i.kind == kind]

    def first(self, kind: str) -> Optional[Inst]:
        lst = self.by_kind(kind)
        return lst[0] if lst else None

    def sensors(self) -> list[Inst]:
        return self.by_kind("sensor")

    def has_sensor(self, name: str) -> bool:
        return any(i.facts.get("sensor") == name for i in self.sensors())


ModuleLookup = Callable[[str], Optional[dict]]


def driver_facts(module_id: str, module: Optional[dict]) -> tuple[dict, Optional[str]]:
    """(facts, warning) for one module."""
    if module_id in DRIVERS:
        facts = dict(DRIVERS[module_id])
    else:
        role = (module or {}).get("role", "")
        if role in PASSIVE_ROLES or role in ("audio_out",):
            return {"kind": "passive" if role != "audio_out" else "speaker"}, None
        if role in ROLE_FALLBACK:
            facts = dict(ROLE_FALLBACK[role])
            warn = f"{module_id}: not in the firmware driver table; driven as a generic {facts['kind']} from its library role"
        else:
            return {"kind": "unsupported", "why": f"role {role or 'unknown'} has no firmware driver"}, None
        return _apply_library_facts(facts, module), warn
    return _apply_library_facts(facts, module), None


def _apply_library_facts(facts: dict, module: Optional[dict]) -> dict:
    """Prefer the library's own stated I2C address and pixel count when it gives one."""
    if not module:
        return facts
    bus = str((module.get("electrical") or {}).get("bus") or "")
    addr = _hex_addr(bus) if "i2c" in bus.lower() else None
    if addr and facts.get("kind") in ("display", "sensor"):
        facts["addr"] = addr
    notes = str((module.get("firmware") or {}).get("driver_notes") or "")
    m = re.search(r"\b(\d{1,3}) pixels\b", notes)
    if m and facts.get("kind") == "neopixel":
        facts["pixels"] = int(m.group(1))
    return facts


def _canonical_pin(net: str) -> Optional[str]:
    for rx, name in NET_PIN:
        if rx.match(net):
            return name
    return None


def _endpoint(s: str) -> tuple[str, str]:
    """'DISP1.SDA' -> ('DISP1', 'SDA'); 'BP1.J_I2C1.3' -> ('BP1', 'J_I2C1.3')."""
    parts = str(s).split(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def instance_pins(electrical: dict, inst: Inst) -> dict:
    gpio = {k: v for k, v in (electrical.get("gpio") or {}).items() if isinstance(v, int)}
    pins: dict[str, tuple[int, str, str]] = {}
    conflicts: list[str] = []

    def put(pin: str, num: int, src: str, via: str) -> None:
        if pin in pins and pins[pin][0] != num:
            conflicts.append(f"{inst.ref}.{pin}: {pins[pin][0]} ({pins[pin][1]}) vs {num} ({src})")
            return
        pins.setdefault(pin, (num, src, via))

    for c in electrical.get("connections") or []:
        a, b = _endpoint(c.get("from", "")), _endpoint(c.get("to", ""))
        mine = a if a[0] == inst.ref else b if b[0] == inst.ref else None
        if not mine:
            continue
        net = str(c.get("net") or "")
        if net in gpio:
            pin = _canonical_pin(net) or ident(mine[1])
            put(pin, gpio[net], net, str(c.get("via") or inst.slot or ""))
    for bus in electrical.get("buses") or []:
        # Two shapes are accepted: buses that list their member "devices" by ref (gstudio.electrical), and
        # buses that list backplane "slots" (backplane-v1.json style).
        slots = bus.get("slots") or []
        btype = bus.get("type")
        devices = [d for d in bus.get("devices") or [] if isinstance(d, dict)]
        dev = next((d for d in devices if d.get("ref") == inst.ref), None)
        if btype == "gpio" and isinstance(slots, dict):
            if inst.slot in slots and isinstance(slots[inst.slot], int):
                put("SIG", slots[inst.slot], f"gpio bus {inst.slot}", inst.slot or "")
            continue
        on_bus = dev is not None or inst.slot in (slots if isinstance(slots, list) else list(slots))
        if not on_bus:
            continue
        for key, pin in BUS_PINS.get(btype, []):
            if isinstance(bus.get(key), int):
                put(pin, bus[key], f"{btype} bus", inst.slot or "")
        if btype == "spi":
            cs = bus.get("cs")
            if dev is not None and isinstance(dev.get("cs"), int):
                put("CS", dev["cs"], "spi bus device cs", inst.slot or "")
            elif isinstance(cs, dict) and isinstance(cs.get(inst.slot), int):
                put("CS", cs[inst.slot], "spi bus cs", inst.slot or "")
        if btype == "i2c" and dev is not None:
            addr = dev.get("addr")
            if isinstance(addr, str) and re.fullmatch(r"0x[0-9A-Fa-f]{2}", addr):
                inst.facts["addr"] = int(addr, 16)  # the electrical stage's address wins (e.g. a strapped 0x3D)
            elif isinstance(addr, int):
                inst.facts["addr"] = addr
    if conflicts:
        raise StageError("electrical.json gives one pin two different GPIOs", {"conflicts": conflicts})
    return pins


def bind(electrical: dict, spec: dict, lookup: ModuleLookup) -> Binding:
    """Resolve every electrical instance to a driver and its GPIOs."""
    if not isinstance(electrical, dict) or not electrical.get("gpio"):
        raise StageError("electrical.json has no gpio map; run the electrical stage first", {})
    gpio = {k: v for k, v in electrical["gpio"].items() if isinstance(v, int)}
    feats = {f.get("id"): f for f in spec.get("features") or [] if isinstance(f, dict)}
    order = {fid: i for i, fid in enumerate(feats)}
    warnings: list[str] = []
    insts: list[Inst] = []
    core = electrical.get("core") or {}
    core_module = core.get("module") or (spec.get("electronics") or {}).get("core") or "CORE_ESP32S3_DEVKITC1_N8R8"
    core_facts, w = driver_facts(core_module, lookup(core_module))
    if w:
        warnings.append(w)
    if core_facts.get("kind") != "core":
        core_facts = {"kind": "core", "flash_mb": 8, "psram_mb": 8}
        warnings.append(f"{core_module}: not a known core board; assuming 8 MB flash and 8 MB PSRAM")
    for raw in electrical.get("instances") or []:
        mid = raw.get("module", "")
        mod = lookup(mid)
        if mid not in DRIVERS and str(raw.get("role") or "") in PASSIVE_ROLES:
            continue  # power input, cables, the backplane itself: nothing for the firmware to drive
        facts, w = driver_facts(mid, mod)
        if w:
            warnings.append(w)
        kind = facts.get("kind", "passive")
        if kind in ("passive", "core"):
            continue
        feat = raw.get("feature")
        label = (feats.get(feat) or {}).get("label") or (mod or {}).get("name") or mid
        inst = Inst(ref=str(raw.get("ref")), module=mid, kind=kind, feature=feat, slot=raw.get("slot"),
                    label=str(label), facts=facts)
        if kind == "unsupported":
            warnings.append(f"{inst.ref} ({mid}) is wired but not driven by this firmware: {facts.get('why')}")
            continue
        inst.pins = instance_pins(electrical, inst)
        insts.append(inst)
    insts.sort(key=lambda i: (order.get(i.feature, 999), i.ref))
    deps = list(LIBS["json"])
    if any(i.kind == "neopixel" for i in insts):
        deps += LIBS["neopixel"]
    disp = next((i for i in insts if i.kind == "display"), None)
    if disp is not None:
        deps += LIBS["lovyangfx"] if disp.facts.get("display") == "ST7789" else LIBS["u8g2"]
    for s in insts:
        if s.kind == "sensor":
            deps += LIBS.get(s.facts.get("sensor", ""), [])
    seen, lib_deps = set(), []
    for d in deps:
        if d not in seen:
            seen.add(d)
            lib_deps.append(d)
    sha = hashlib.sha256(json.dumps(electrical, sort_keys=True).encode()).hexdigest()
    return Binding(insts, gpio, core_module, core_facts, warnings, lib_deps, sha)


# ------------------------------------------------------------------ pins.h


def pins_h(b: Binding) -> str:
    L = [
        "// pins.h: GPIO numbers for this design. GENERATED from electrical.json; do not edit by hand.",
        f"// electrical.json sha256 {b.electrical_sha256}",
        "// Every number below is copied from electrical.json (\"gpio\", \"connections\", \"buses\"),",
        "// so the firmware pins always equal the wiring (QB-19).",
        "#pragma once",
        "",
        "// Backplane signal map (electrical.json \"gpio\")",
    ]
    for sig, num in b.gpio.items():
        L.append(f"#define GS_GPIO_{ident(sig)} {num}")
    L += ["", "// Per module instance: <REF>_<PIN> (ref: module, slot; source)"]
    for i in b.instances:
        L.append(f"// {i.ref}: {i.module} on {i.slot or '?'}" + (f" (feature {i.feature})" if i.feature else ""))
        for pin, (num, src, _via) in i.pins.items():
            L.append(f"#define GS_{ident(i.ref)}_{pin} {num}  // {src}")
    L.append("")
    return "\n".join(L)


def parse_pins_h(text: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^#define\s+(GS_\w+)\s+(-?\d+)\b", text, re.M)}


# ------------------------------------------------------------------ hardware.h


def _pin_sym(i: Optional[Inst], pin: str) -> str:
    if i is None or pin not in i.pins:
        return "-1"
    return f"GS_{ident(i.ref)}_{pin}"


def _need(i: Inst, *pins: str) -> None:
    missing = [p for p in pins if p not in i.pins]
    if missing:
        raise StageError(f"{i.ref} ({i.module}) on {i.slot}: electrical.json gives no GPIO for {', '.join(missing)}",
                         {"ref": i.ref, "missing": missing, "fix": "re-run the electrical stage; check the slot assignment"})


def hardware_h(b: Binding) -> str:
    L = ["// hardware.h: which modules this design has and which pins.h symbols drive them.",
         "// GENERATED from electrical.json and library/modules.json; no GPIO numbers here (see pins.h).",
         "#pragma once", '#include "pins.h"', "",
         f'#define GS_CORE_MODULE "{b.core_module}"',
         f"#define GS_CORE_FLASH_MB {int(b.core_facts.get('flash_mb', 8))}",
         f"#define GS_CORE_PSRAM_MB {int(b.core_facts.get('psram_mb', 8))}", ""]

    def flag(name: str, on: bool) -> None:
        L.append(f"#define GS_HAS_{name} {1 if on else 0}")

    buttons = b.by_kind("button")
    for btn in buttons:
        _need(btn, "SIG")
    flag("BUTTON", bool(buttons))
    if buttons:
        L.append("#define GS_BUTTON_PINS { " + ", ".join(_pin_sym(x, "SIG") for x in buttons) + " }")
        L.append("#define GS_BUTTON_ACTIVE_LOW { " + ", ".join("1" if x.facts.get("active_low", True) else "0" for x in buttons) + " }")
        L.append("#define GS_BUTTON_IDS { " + ", ".join(json.dumps(x.feature or x.ref.lower()) for x in buttons) + " }")
    L.append("")

    led = b.first("neopixel")
    if led:
        _need(led, "DIN")
    flag("NEOPIXEL", led is not None)
    L.append(f"#define GS_PIN_LED_DIN {_pin_sym(led, 'DIN')}")
    L.append(f"#define GS_LED_COUNT {int(led.facts.get('pixels', 1)) if led else 0}")
    L.append("")

    amp = b.first("amp")
    if amp:
        _need(amp, "BCLK", "LRC", "DIN")
    flag("AMP", amp is not None)
    for pin in ("BCLK", "LRC", "DIN"):
        L.append(f"#define GS_PIN_I2S_SPK_{pin} {_pin_sym(amp, pin)}")
    L.append(f"#define GS_PIN_AMP_SD {_pin_sym(amp, 'SD')}")
    flag("SPEAKER", b.first("speaker") is not None)
    L.append("")

    mic = b.first("mic")
    if mic:
        _need(mic, "SCK", "WS", "SD")
    flag("MIC", mic is not None)
    for pin in ("SCK", "WS", "SD"):
        L.append(f"#define GS_PIN_I2S_MIC_{pin} {_pin_sym(mic, pin)}")
    L.append("")

    disp = b.first("display")
    i2c_users = [x for x in b.instances if "SDA" in x.pins and "SCL" in x.pins]
    i2c_ref = i2c_users[0] if i2c_users else None
    flag("I2C", i2c_ref is not None)
    L.append(f"#define GS_PIN_I2C_SDA {_pin_sym(i2c_ref, 'SDA')}")
    L.append(f"#define GS_PIN_I2C_SCL {_pin_sym(i2c_ref, 'SCL')}")
    sd = b.first("sd")
    spi_users = [x for x in b.instances if "SCK" in x.pins and "MOSI" in x.pins]
    spi_ref = sd or (spi_users[0] if spi_users else None)
    if sd:
        _need(sd, "SCK", "MOSI", "MISO", "CS")
    flag("SPI", spi_ref is not None)
    L.append(f"#define GS_PIN_SPI_SCK {_pin_sym(spi_ref, 'SCK')}")
    L.append(f"#define GS_PIN_SPI_MOSI {_pin_sym(spi_ref, 'MOSI')}")
    L.append(f"#define GS_PIN_SPI_MISO {_pin_sym(spi_ref, 'MISO')}")
    flag("SD", sd is not None)
    L.append(f"#define GS_PIN_SD_CS {_pin_sym(sd, 'CS')}")
    L.append("")

    dkind = disp.facts.get("display", "NONE") if disp else "NONE"
    L.append(f"#define GS_DISPLAY_KIND {DISPLAY_KINDS[dkind]}  // {dkind}")
    if disp and dkind == "ST7789":
        _need(disp, "SCK", "MOSI", "CS", "DC")
        L.append(f"#define GS_PIN_TFT_CS {_pin_sym(disp, 'CS')}")
        L.append(f"#define GS_PIN_TFT_DC {_pin_sym(disp, 'DC')}")
        L.append(f"#define GS_PIN_TFT_RST {_pin_sym(disp, 'RST')}")
        L.append(f"#define GS_PIN_TFT_BL {_pin_sym(disp, 'BL')}")
        L.append(f"#define GS_DISP_WIDTH {disp.facts.get('w', 240)}")
        L.append(f"#define GS_DISP_HEIGHT {disp.facts.get('h', 320)}")
        L.append("#define GS_TFT_OFFSET_X 0")
        L.append("#define GS_TFT_OFFSET_Y 0")
        L.append(f"#define GS_TFT_ROTATION {disp.facts.get('rotation', 1)}")
    elif disp:
        _need(disp, "SDA", "SCL")
        L.append(f"#define GS_DISP_I2C_ADDR 0x{int(disp.facts.get('addr', 0x3C)):02X}")
        L.append(f"#define GS_DISP_WIDTH {disp.facts.get('w', 128)}")
        L.append(f"#define GS_DISP_HEIGHT {disp.facts.get('h', 64)}")
    L.append("")

    names = ["AHT20", "BMP280", "BH1750", "DS3231", "VL53L0X"]
    addrs = []
    for n in names:
        s = next((x for x in b.sensors() if x.facts.get("sensor") == n), None)
        if s:
            _need(s, "SDA", "SCL")
            addrs.append(int(s.facts.get("addr", 0)))
        flag(n, s is not None)
        if s:
            L.append(f"#define GS_{n}_ADDR 0x{int(s.facts.get('addr', 0)):02X}")
    if disp and dkind != "ST7789":
        addrs.insert(0, int(disp.facts.get("addr", 0x3C)))
    L.append("#define GS_I2C_EXPECTED { " + (", ".join(f"0x{a:02X}" for a in addrs) if addrs else "0") + " }")
    L.append("")

    bz = b.first("buzzer")
    if bz:
        _need(bz, "SIG")
    flag("BUZZER", bz is not None)
    L.append(f"#define GS_PIN_BUZZER {_pin_sym(bz, 'SIG')}")
    L.append(f"#define GS_BUZZER_ACTIVE_LOW {1 if bz and bz.facts.get('active_low') else 0}")
    sv = b.first("servo")
    if sv:
        _need(sv, "SIG")
    flag("SERVO", sv is not None)
    L.append(f"#define GS_PIN_SERVO {_pin_sym(sv, 'SIG')}")
    ldr = b.first("ldr")
    if ldr:
        _need(ldr, "SIG")
    flag("LDR", ldr is not None)
    L.append(f"#define GS_PIN_LDR {_pin_sym(ldr, 'SIG')}")
    mo = b.first("motion")
    if mo:
        _need(mo, "SIG")
    flag("MOTION", mo is not None)
    L.append(f"#define GS_PIN_MOTION {_pin_sym(mo, 'SIG')}")
    L.append("")
    return "\n".join(L)


def check_template(template: str, b: Binding, settings: dict) -> list[str]:
    """Hard requirements of a template on the hardware. Raises StageError; returns soft warnings."""
    errs: list[dict] = []
    warn: list[str] = []
    has = {k: bool(b.by_kind(k)) for k in ("button", "neopixel", "amp", "speaker", "mic", "display", "sd", "sensor", "buzzer", "ldr", "motion")}

    def need(ok: bool, msg: str, fix: str) -> None:
        if not ok:
            errs.append({"msg": msg, "fix": fix})

    if template == "story_player":
        need(has["amp"], "story_player needs the MAX98357A amplifier", "add a speaker feature; normalize adds the amp")
        need(has["sd"], "story_player needs the microSD module for the story pack", "normalize adds it for story_player")
        need(has["button"], "story_player needs a button or touch pad", "add a button feature")
        if settings.get("capture_mode") == "child_voice_clip":
            need(has["mic"], "capture_mode child_voice_clip needs a microphone (INMP441)",
                 "add a mic feature, or set behaviour.settings.capture_mode to parent_web")
        if not has["neopixel"]:
            warn.append("no WS2812B light: there is no visible listening indicator or night light")
    elif template == "desk_companion":
        need(has["display"], "desk_companion needs a display for its face", "add an OLED or ST7789 feature")
        if settings.get("clap_detect") and not has["mic"]:
            warn.append("clap_detect is on but there is no microphone; clap reactions are off")
        if not (has["amp"] or has["buzzer"]):
            warn.append("no speaker or buzzer: the companion is silent")
    elif template == "notifier_clock":
        need(has["display"], "notifier_clock needs a display for the time", "add an OLED or ST7789 feature")
    elif template == "ambient_light":
        need(has["neopixel"], "ambient_light needs a WS2812B LED or ring", "add an LED feature")
        if settings.get("auto_dim") and not (b.has_sensor("BH1750") or has["ldr"]):
            warn.append("auto_dim is on but there is no light sensor; brightness stays fixed")
    elif template == "sensor_display":
        need(has["display"], "sensor_display needs a display", "add an OLED or ST7789 feature")
        need(bool(b.sensors()) or has["ldr"], "sensor_display needs at least one sensor",
             "add an AHT20, BMP280, BH1750 or VL53L0X feature")
    if errs:
        raise StageError(f"{template}: the design is missing hardware the behaviour needs",
                         {"errors": errs})
    return warn


__all__ = ["DRIVERS", "LIBS", "Binding", "Inst", "bind", "pins_h", "hardware_h", "parse_pins_h", "check_template", "ident"]
