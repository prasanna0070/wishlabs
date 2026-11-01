"""SOFTWARE_SETUP.md: flashing and setup, from the firmware stage's summary and files."""
from __future__ import annotations

import re

from .util import Bundle, table

SETUP_KEYS = ("software_setup_md", "setup_md", "software_setup", "setup")


def _firmware_text(s: dict) -> str | None:
    for k in SETUP_KEYS:
        v = s.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v:
            return "\n".join(str(x) for x in v)
    return None


def _demote(md: str) -> str:
    """Firmware's Markdown nests under our '## Setup for this design': drop its H1 and push headings down a level."""
    lines = md.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    out, fence = [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
        out.append("#" + ln if (not fence and re.match(r"#{1,5} ", ln)) else ln)
    return "\n".join(out).strip()


def _pins(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r"#define\s+([A-Za-z_]\w*)\s+\(?(-?\d+)\)?|(?:constexpr|const|static)\s+[\w:<> ]*?\b([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;", text):
        out.append((m.group(1) or m.group(3), m.group(2) or m.group(4)))
    return out


def render(b: Bundle) -> str:
    out, sp = b.out, b.spec
    fw_dir = out / "firmware"
    s = b.summary("firmware")
    state = b.state("firmware")
    fwb = b.build.get("firmware") or {}
    beh = sp.get("behaviour") or {}
    L = [f"# {sp.get('name', 'Gadget')}: software setup", ""]
    if state != "done" or not fw_dir.exists():
        L.append(f"The firmware stage did not complete in this version ({state}: {b.message('firmware') or 'no message'}). "
                 "No firmware ships with it; do not flash anything from an older version without rebuilding.")
        L.append("")
        return "\n".join(L)

    binp = fw_dir / "firmware.bin"
    L.append(f"The gadget runs Arduino-framework firmware on the ESP32-S3 core board (`{fwb.get('board', 'esp32-s3-devkitc-1')}`), "
             f"generated from the `{beh.get('template', '?')}` behaviour template, this design's settings and its pin map.")
    L.append("")
    rows = [["PlatformIO project", "`firmware/platformio.ini`, `firmware/src/`, `firmware/include/`"],
            ["Compiled binary", f"`firmware/firmware.bin` ({binp.stat().st_size:,} bytes)" if binp.exists() else "not produced"],
            ["Native unit tests", f"{fwb.get('tests_passed', '?')}/{fwb.get('tests_total', '?')} passed (`firmware/test-results.txt`)"
             if (fw_dir / "test-results.txt").exists() else "no saved test results"],
            ["Build log", "`firmware/build-log.txt`" if (fw_dir / "build-log.txt").exists() else "not saved"]]
    if (fw_dir / "sdcard").exists():
        rows.append(["microSD content", "`firmware/sdcard/` (copy its contents to the root of a FAT32 microSD card)"])
    L.append(table(["What", "Where"], rows))

    txt = _firmware_text(s)
    if txt:
        L += ["", "## Setup for this design", "", _demote(txt)]
    else:
        L += ["", "## Flash the firmware", "",
              "1. Install PlatformIO Core 6.2 (`pip install platformio==6.2.0`).",
              "2. Connect the core board's USB port to a computer with the gadget's own USB-C power disconnected.",
              "3. From this folder:", "", "```sh", "cd firmware", "pio run -t upload", "pio test -e native   # optional: re-run the unit tests on the computer", "```",
              "", "The firmware stage gave no design-specific setup text for this version; ask before changing partition, flash or OTA settings."]

    from gstudio.checks import flasher_parts

    man, parts = flasher_parts(out)
    if man is not None and parts and not txt:  # the firmware stage's own text already explains its flasher files
        chips = sorted({str(b.get("chipFamily")) for b in man.get("builds") or [] if isinstance(b, dict) and b.get("chipFamily")})
        L += ["", "## Flash from a web browser", "",
              "`firmware/web-flasher-manifest.json` is an in-browser flasher manifest (Web Serial, Chrome or Edge on a computer)"
              + (f" for {', '.join(chips)}" if chips else "") + ". It writes these files at the offsets the manifest itself gives:", ""]
        L.append(table(["File", "Offset", "Present"], [[f"`{pth}`", hex(o) if isinstance(o, int) else (o if o is not None else "not stated"),
                                                        "yes" if ok else "MISSING"] for pth, o, ok in parts]))

    pins_h = fw_dir / "include" / "pins.h"
    if pins_h.exists():
        pins = _pins(pins_h.read_text(errors="ignore"))
        if pins:
            L += ["", "## Pin constants", "", "Generated from `electrical.json` into `firmware/include/pins.h`:", ""]
            L.append(table(["Constant", "GPIO"], pins[:60]))

    src = "\n".join(p.read_text(errors="ignore") for p in fw_dir.glob("src/**/*") if p.is_file() and p.suffix in (".cpp", ".h", ".ino"))
    if "/api/state" in src and "/api/action" in src:
        L += ["", "## Local control API", "",
              "The firmware serves a small JSON API on the local network for the companion app or an MCP client: "
              "`GET /api/state` and `POST /api/action`. It is local only; nothing is uploaded."]

    L += ["", "## What was checked", "",
          "- Compiled for ESP32-S3 and unit-tested (native tests of the hardware-free logic) on the builder.",
          "- Not flashed to a physical board in this build. Power-on, buttons, light, audio and microphone behaviour are physical checks still needed.",
          ""]
    return "\n".join(L)
