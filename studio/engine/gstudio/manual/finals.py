"""build.json `tests[]`: the final checks, physical (for the maker, status not_run) and digital (status
read from saved build files only). A physical check is never marked passed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .data import ManualData
from .model import ROLE_NOUN, Assembly


def _t(title: str, expected: str, kind: str = "physical", status: str = "not_run", evidence: Optional[str] = None) -> dict:
    row = {"title": title, "expected": expected, "kind": kind, "status": status}
    if evidence:
        row["evidence"] = evidence
    return row


def physical_tests(data: ManualData, A: Assembly) -> list[dict]:
    roles = {g.role for g in [*A.features, *A.internals]}
    beh = data.spec.get("behaviour") or {}
    tpl = beh.get("template") or ""
    settings = beh.get("settings") or {}
    child = bool((data.spec.get("audience") or {}).get("child_directed")) or "CHILD_DIRECTED" in ((data.spec.get("safety") or {}).get("flags") or [])
    t: list[dict] = []
    if A.backplane or A.core:
        t.append(_t("Bench self-test on the table",
                    "Before the shell is printed, with every module plugged into the cartridge on the table, the self-test "
                    "summary line shows \"ok\":true and \"failed\":0, and the whole line is written down (checklist step bench-test)."))
    if (data.firmware_dir / "sdcard").is_dir():
        t.append(_t("Story card prepared",
                    "The microSD card is FAT32, holds the contents of firmware/sdcard/ at its root, and its stories.json says \"audio\": true (checklist step prepare-card)."))
    t.append(_t("Power-on and self-test",
                "On USB-C power the gadget starts, and every module passes its self-test as described in SOFTWARE_SETUP.md: "
                + ", ".join(ROLE_NOUN.get(r, r) for r in sorted(roles)) + "."))
    if tpl == "story_player":
        t.append(_t("Story, stop and volume", "A tap plays a story through the speaker; a tap during a story stops it; the volume is comfortable at the listening position."))
        mode = settings.get("capture_mode") or settings.get("capture") or ""
        if "mic" in roles and mode != "none":
            t.append(_t("Listening light and confirmation",
                        "The microphone opens only while the button is held, the light shows it is listening, and a new favourite is kept only after an explicit confirming tap."))
        t.append(_t("Memory after restart", "A confirmed favourite is still there after unplugging and re-plugging power, and appears in a later story."))
    elif tpl == "desk_companion":
        if "display" in roles:
            t.append(_t("Face and expressions", "The display shows the face the right way up and changes expression on input."))
        if "speaker" in roles:
            t.append(_t("Chimes", "The speaker plays the chimes clearly and at a comfortable volume."))
        if "mic" in roles:
            t.append(_t("Clap response", "A clap near the gadget triggers a reaction; ordinary room noise does not."))
    elif tpl == "notifier_clock":
        t.append(_t("Time sync and display", "After joining Wi-Fi the display shows the correct local time."))
        t.append(_t("Notification light", "A notification sent to the local API lights the LEDs and can be cleared."))
    elif tpl == "ambient_light":
        t.append(_t("Light scenes and dimming", "Each scene shows as expected and brightness follows the control and the room light."))
    elif tpl == "sensor_display":
        t.append(_t("Sensor readings", "Readings shown on the display are plausible against a reference thermometer or hygrometer in the same room."))
    if "button" in roles and tpl != "story_player":
        t.append(_t("Button response", "Each press is registered once; holding does not cause repeated actions unless designed to."))
    t.append(_t("Local API", "From a phone or computer on the same network, GET /api/state returns the gadget's state and POST /api/action performs an action."))
    t.append(_t("Closed-case heat, power and retention",
                "During a 30-minute session with the gadget closed there are no resets, no part becomes uncomfortably warm to touch, nothing rattles and no cable is pinched."))
    t.append(_t("Stability", "The gadget stands level on its feet and does not tip or slide during normal use" + (" by a child under adult supervision." if child else ".")))
    return t


def digital_tests(data: ManualData) -> list[dict]:
    out = data.out
    t: list[dict] = []
    fw = out / "firmware"
    size = _read(fw / "size.json")
    if (fw / "firmware.bin").is_file():
        t.append(_t("Firmware compiles", "PlatformIO builds the delivered firmware for esp32-s3-devkitc-1.", "digital", "passed", "firmware/firmware.bin"))
    elif size is not None or (fw / "build-log.txt").is_file():
        t.append(_t("Firmware compiles", "PlatformIO builds the delivered firmware for esp32-s3-devkitc-1.", "digital", "failed", "firmware/build-log.txt"))
    else:
        t.append(_t("Firmware compiles", "PlatformIO builds the delivered firmware for esp32-s3-devkitc-1.", "digital", "not_run"))
    status, ev = _unit_tests(fw / "test-results.txt")
    t.append(_t("Firmware unit tests", "The hardware-free unit tests of the firmware logic pass.", "digital", status, ev))
    n, ev = drc_errors(data)
    st = "not_run" if n is None else ("passed" if n == 0 else "failed")
    t.append(_t("Backplane design-rule check", "The routed backplane has no design-rule errors (warnings and waived items excluded).",
                "digital", st, ev))
    return t


def drc_errors(data: ManualData) -> tuple[Optional[int], Optional[str]]:
    """DRC error count: the pcb stage's own figure first, else kicad-cli drc.json filtered to severity 'error'."""
    out = data.out
    pcb = (data.build_existing.get("pcb") or {}) if isinstance(data.build_existing, dict) else {}
    if isinstance(pcb.get("drc_errors"), int):
        return pcb["drc_errors"], "build.json pcb.drc_errors"
    st = _read(out / ".gstudio" / "stages" / "pcb.json")
    for d in (st, (st or {}).get("summary") if isinstance(st, dict) else None):
        if isinstance(d, dict) and isinstance(d.get("drc_errors"), int):
            return d["drc_errors"], ".gstudio/stages/pcb.json"
    drc = _read(out / "pcb" / "drc.json")
    if isinstance(drc, dict):
        items = []
        for key in ("violations", "unconnected_items", "schematic_parity"):
            v = drc.get(key)
            if isinstance(v, list):
                items += [x for x in v if isinstance(x, dict)]
        if items or any(isinstance(drc.get(k), list) for k in ("violations", "unconnected_items")):
            errs = [x for x in items if str(x.get("severity", "error")).lower() == "error" and not x.get("excluded")]
            return len(errs), "pcb/drc.json"
        if isinstance(drc.get("errors"), int):
            return drc["errors"], "pcb/drc.json"
    return None, None


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def _unit_tests(p: Path) -> tuple[str, Optional[str]]:
    if not p.is_file():
        return "not_run", None
    txt = p.read_text(errors="ignore")
    m = re.search(r"(\d+)\s+test cases?:\s*(\d+)\s+succeeded(?:.*?(\d+)\s+failed)?", txt, re.I | re.S)
    if m:
        failed = int(m.group(3) or 0)
        return ("failed" if failed else "passed"), "firmware/test-results.txt"
    if re.search(r"\bFAIL(ED)?\b", txt):
        return "failed", "firmware/test-results.txt"
    if re.search(r"\b(PASSED|SUCCESS)\b", txt):
        return "passed", "firmware/test-results.txt"
    return "not_run", "firmware/test-results.txt"


def build_tests(data: ManualData, A: Assembly) -> list[dict]:
    return physical_tests(data, A) + digital_tests(data)
