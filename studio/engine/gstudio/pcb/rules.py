"""Pure design rules the PCB stage checks before and after it builds a board (no KiCad needed).

* port_keying_conflicts (REVIEW-1 MAJ-3): two backplane ports that take the same connector family and pin count must
  carry the same supply voltage, or a lead made for one fits the other (a 3V3 button lead in a 5 V LED port shorts the
  buffered LED data to GND; a 3V3 module plugged into a 5 V port gets 5 V).
* amp_sd_boot (REVIEW-1 MIN-1): the MAX98357A SD_MODE pin must sit under its shutdown threshold while the GPIO that
  drives it floats at boot. The breakout's own pull-up to VIN fights the board pull-down and the chip's internal one.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# --------------------------------------------------------------------------- connector keying (MAJ-3)

_JST = re.compile(r"JST_(?P<fam>[A-Z]+)_[A-Z]*?(?P<n>\d+)B-")


def connector_family(footprint: str) -> Optional[tuple[str, int]]:
    """('JST-PH', 3) for 'Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical'; None for anything not a keyed JST."""
    m = _JST.search(footprint.split(":", 1)[-1])
    if not m:
        return None
    return f"JST-{m.group('fam')}", int(m.group("n"))


def port_supply(slot: dict) -> str:
    """The supply rail a port's leads see: its power-pin nets except GND, else the slot's 'supply' text, else 'none'."""
    nets = sorted({str(p.get("net")) for p in slot.get("pins", [])
                   if p.get("net") and (p.get("kind") == "power" or p.get("net") in ("+5V", "+3V3", "VBUS"))}
                  - {"GND"})
    if nets:
        return "+".join(nets)
    txt = str(slot.get("supply") or "")
    for rail in ("+5V", "+3V3", "VBUS"):
        if rail in txt:
            return rail
    return "none"


def port_keying_conflicts(backplane: dict) -> list[dict]:
    """Groups of ports that share (family, pin count) but not a supply. Empty list = the rule passes."""
    groups: dict[tuple[str, int], dict[str, str]] = {}
    for s in backplane.get("slots", []):
        if s.get("kind") != "port" or not s.get("footprint"):
            continue
        fam = connector_family(s["footprint"])
        if fam is None:
            continue
        groups.setdefault(fam, {})[s["id"]] = port_supply(s)
    out = []
    for (fam, n), ports in sorted(groups.items()):
        if len(set(ports.values())) > 1:
            out.append({"family": fam, "pins": n, "ports": dict(sorted(ports.items())),
                        "why": f"a {fam} {n}-pin lead fits {', '.join(sorted(ports))}, which carry "
                               f"{', '.join(sorted(set(ports.values())))}"})
    return out


def port_keying_check(backplane: dict) -> dict:
    bad = port_keying_conflicts(backplane)
    return {"id": "pcb.port_keying", "title": "Ports with the same connector family and pin count share a supply voltage",
            "kind": "digital", "status": "failed" if bad else "passed",
            "detail": "; ".join(b["why"] for b in bad) or "every connector family and pin count maps to one supply",
            "artifact": "library/backplane-v1.json", "conflicts": bad}


# --------------------------------------------------------------------------- amplifier shutdown at boot (MIN-1)

# Adafruit "MAX98357 I2S Class-D Mono Amp" guide, Pinouts page (read 2026-11-01):
#   "If SD is connected to ground directly (voltage is under 0.16V) then the amp is shut down"
#   "an internal 100K pulldown resistor on SD"; "For the breakout board, there's a 1Mohm resistor from SD to Vin"
ADAFRUIT_SD_SOURCE = "https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts"
SD_SHUTDOWN_MAX_V = 0.16
SD_INTERNAL_PULLDOWN_OHM = 100e3
ADAFRUIT_3006_SD_PULLUP_OHM = 1e6
VIN_MAX_V = 5.25  # USB 2.0 VBUS upper limit; the worst case for SD (higher VIN, higher SD voltage)


def parse_ohms(text: Any) -> Optional[float]:
    """'10k' -> 10000, '100 k (...)' -> 100000, '330R' -> 330, '4k7' -> 4700, '5.1k 1%' -> 5100, '1M' -> 1e6."""
    s = str(text or "").strip()
    m = re.match(r"^\s*(\d+)([RkKM])(\d+)\b", s)  # 4k7 style
    if m:
        mult = {"R": 1, "k": 1e3, "K": 1e3, "M": 1e6}[m.group(2)]
        return float(f"{m.group(1)}.{m.group(3)}") * mult
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(k|K|M|R|ohm|Ω)?", s)
    if not m:
        return None
    mult = {"k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0, "ohm": 1.0, "Ω": 1.0, None: 1.0}[m.group(2)]
    return float(m.group(1)) * mult


def amp_sd_voltage(pullup_ohm: float, board_pulldown_ohm: Optional[float], vin: float = VIN_MAX_V,
                   internal_pulldown_ohm: float = SD_INTERNAL_PULLDOWN_OHM) -> float:
    """SD_MODE voltage with the driving GPIO floating: VIN through the breakout pull-up into (internal || board)."""
    rdown = internal_pulldown_ohm if not board_pulldown_ohm else \
        1.0 / (1.0 / internal_pulldown_ohm + 1.0 / board_pulldown_ohm)
    return vin * rdown / (pullup_ohm + rdown)


def amp_sd_boot_check(board_pulldown_ohm: Optional[float], module: Optional[dict] = None, vin: float = VIN_MAX_V,
                      gpio_high_v: float = 3.3) -> dict:
    """Digital check record: the amp stays shut down while GPIO39 floats at boot.

    The breakout's pull-up comes from the library module (`electrical.sd_pullup_ohm`, with `sd_pullup_source`) when
    foundation records it; otherwise from the Adafruit 3006 reference board, labelled as such."""
    el = (module or {}).get("electrical") or {}
    pullup = el.get("sd_pullup_ohm")
    if pullup:
        basis = f"module {module.get('id', '')} sd_pullup_ohm ({el.get('sd_pullup_source') or 'library'})"
    else:
        pullup = ADAFRUIT_3006_SD_PULLUP_OHM
        basis = (f"Adafruit 3006 reference: 1 MOhm SD-to-VIN ({ADAFRUIT_SD_SOURCE}); the fitted clone's SD network is "
                 "not confirmed from its own schematic")
    v = amp_sd_voltage(float(pullup), board_pulldown_ohm, vin)
    ok = v < SD_SHUTDOWN_MAX_V
    i_gpio_ma = gpio_high_v / board_pulldown_ohm * 1e3 if board_pulldown_ohm else 0.0
    rd = f"{board_pulldown_ohm / 1e3:g} kOhm" if board_pulldown_ohm else "none"
    return {"id": "pcb.amp_sd_boot", "title": "Amplifier stays shut down while its enable GPIO floats at boot",
            "kind": "digital", "status": "passed" if ok else "failed",
            "detail": (f"SD_MODE = {v:.3f} V at VIN {vin:g} V with board pull-down {rd}, breakout pull-up "
                       f"{pullup / 1e6:g} MOhm and the chip's internal 100 kOhm (shutdown below {SD_SHUTDOWN_MAX_V} V); "
                       f"driving it high costs {i_gpio_ma:.2f} mA in the pull-down. Basis: {basis}."),
            "sd_v": round(v, 4), "pullup_ohm": pullup, "pulldown_ohm": board_pulldown_ohm,
            "artifact": "pcb/bom.csv", "confirmed": bool(el.get("sd_pullup_ohm"))}


__all__ = ["connector_family", "port_supply", "port_keying_conflicts", "port_keying_check", "parse_ohms",
           "amp_sd_voltage", "amp_sd_boot_check", "SD_SHUTDOWN_MAX_V"]
