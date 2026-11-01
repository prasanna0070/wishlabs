"""The generated PlatformIO project: pinned, complete (QB-39), and laid out for OTA."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from gstudio.firmware import generate, run
from gstudio.firmware.project import PARTITIONS_8MB, page_header

from .conftest import load_fixture


def test_generate_writes_the_project(design, tmp_path):
    template, spec, elec = design
    facts = generate(spec, elec, tmp_path)
    fw = tmp_path / "firmware"
    for rel in ("platformio.ini", "partitions.csv", "include/pins.h", "include/hardware.h", "include/config.h",
                "include/parent_page.h", "src/main.cpp", f"src/app_{template}.cpp", "lib/gs_core/src/gs_store.cpp",
                "test/test_core/test_main.cpp", "data/parent.html", "scripts/prebuild.py"):
        assert (fw / rel).exists(), rel
    other = [p.name for p in (fw / "src").glob("app_*.cpp")]
    assert other == [f"app_{template}.cpp"]  # exactly one behaviour compiled in
    assert facts["version"].startswith("0.") and "+" in facts["version"]


def test_versions_are_pinned(tmp_path):
    spec, elec = load_fixture("story_player")
    generate(spec, elec, tmp_path)
    ini = (tmp_path / "firmware" / "platformio.ini").read_text()
    assert "platform = platformio/espressif32@8.0.0" in ini
    assert "platformio/native@1.2.1" in ini
    deps = re.findall(r"^\s+([\w-]+/[^@\n]+)@(\S+)$", ini, re.M)
    assert deps, "no lib_deps found"
    for name, ver in deps:
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), f"{name}@{ver} is not an exact version"
    assert "board_build.arduino.memory_type = qio_opi" in ini  # N8R8: octal PSRAM
    assert "board_upload.flash_size = 8MB" in ini


def test_partition_table_fits_8mb_with_two_ota_slots():
    rows = [r.split(",") for r in PARTITIONS_8MB.splitlines() if r and not r.startswith("#")]
    rows = [[c.strip() for c in r] for r in rows]
    names = [r[0] for r in rows]
    assert {"app0", "app1", "otadata", "nvs", "gsnvs"} <= set(names)
    end = 0x9000
    for name, typ, sub, off, size, *_ in rows:
        off, size = int(off, 16), int(size, 16)
        assert off >= end, f"{name} overlaps"
        if typ == "app":
            assert off % 0x10000 == 0, f"{name} must be 64 KB aligned"
        end = off + size
    assert end <= 8 * 1024 * 1024
    a0 = next(r for r in rows if r[0] == "app0")
    a1 = next(r for r in rows if r[0] == "app1")
    assert a0[4] == a1[4]  # equal slots so any update fits either


def test_config_h_carries_the_settings(tmp_path):
    spec, elec = load_fixture("story_player")
    spec["behaviour"]["settings"] = {"capture_mode": "parent_web", "volume_max": 50, "keep_window_seconds": 12}
    generate(spec, elec, tmp_path)
    cfg = (tmp_path / "firmware" / "include" / "config.h").read_text()
    assert "#define GS_CFG_CAPTURE_MODE 2" in cfg
    assert "#define GS_CFG_VOLUME_MAX 50" in cfg
    assert "#define GS_CFG_KEEP_WINDOW_MS 12000" in cfg
    assert '#define GS_DEVICE_SLUG "luna-owl"' in cfg
    assert '#define GS_OTA_PUBKEY_PEM ""' in cfg


def test_no_stubs_or_todos_in_runtime_code(design, tmp_path):
    template, spec, elec = design
    generate(spec, elec, tmp_path)
    fw = tmp_path / "firmware"
    bad = re.compile(r"\b(TODO|FIXME|XXX|TBD)\b|not implemented|stub", re.I)
    for p in list((fw / "src").glob("*")) + list((fw / "lib").rglob("*.*")) + list((fw / "include").glob("*.h")):
        if p.name == "parent_page.h":
            continue
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            assert not bad.search(line), f"{p.name}:{n}: {line.strip()}"


def test_parent_page_header_matches_the_html(tmp_path):
    spec, elec = load_fixture("story_player")
    generate(spec, elec, tmp_path)
    fw = tmp_path / "firmware"
    html = (fw / "data" / "parent.html").read_text()
    assert (fw / "include" / "parent_page.h").read_text() == page_header(html)
    # The page enforces confirm-before-save and typed ERASE / RESET.
    assert "confirm(" in html and '"ERASE"' in html and '"RESET"' in html
    assert "X-GS-PIN" in html


def test_c_strings_are_escaped(tmp_path):
    spec, elec = load_fixture("story_player")
    spec["name"] = 'Lūna "Owl"'
    generate(spec, elec, tmp_path)
    cfg = (tmp_path / "firmware" / "include" / "config.h").read_text()
    line = next(l for l in cfg.splitlines() if l.startswith("#define GS_DEVICE_NAME"))
    assert '\\"Owl\\"' in line and "\\305\\253" in line  # UTF-8 of "ū" as octal escapes
    assert "#define GS_AP_PREFIX \"LnaOwl\"" in cfg


class _Ctx:
    def __init__(self, spec, elec, out, options):
        self.spec, self.electrical, self.out, self.options, self.lib = spec, elec, Path(out), options, None
        self.messages = []

    def progress(self, stage, msg, pct=None):
        self.messages.append((stage, msg, pct))


def test_run_without_compile_returns_summary(tmp_path):
    spec, elec = load_fixture("story_player")
    s = run(_Ctx(spec, elec, tmp_path, {"firmware_compile": False}))
    assert s["compiled"] is False
    assert s["template"] == "story_player"
    assert "## 1. Put the firmware on the board" in s["setup_markdown"]
    assert s["software_setup_md"] == s["setup_markdown"]
    assert "no audio yet" in s["setup_markdown"]
    assert s["selftest_protocol"]["command"] == "selftest\\n"
    assert (tmp_path / "firmware" / "sdcard" / "stories.json").exists()
    assert any("compile skipped" in w for w in s["warnings"])


@pytest.mark.skipif(not os.environ.get("GS_FW_COMPILE_TESTS"), reason="set GS_FW_COMPILE_TESTS=1 to compile (VM only)")
def test_compile_story_player(tmp_path):
    spec, elec = load_fixture("story_player")
    s = run(_Ctx(spec, elec, tmp_path, {}))
    assert s["compiled"] and s["tests_passed"] == s["tests_total"] > 0
    fw = tmp_path / "firmware"
    assert (fw / "firmware.bin").stat().st_size > 100_000
    assert (fw / "firmware-merged.bin").read_bytes()[0] == 0xE9  # ESP image magic at offset 0x0
    man = json.loads((fw / "web-flasher-manifest.json").read_text())
    assert man["builds"][0]["chipFamily"] == "ESP32-S3"
    assert s["size"]["app_slot_used_percent"] < 80
