"""Compile the generated PlatformIO project and run its native unit tests.

PlatformIO's own state lives outside the export so nothing heavy lands in <out>/firmware:
  PLATFORMIO_CORE_DIR        the warmed core (default /opt/gs/pio on the VM / builder image)
  GS_FW_SHARED_DIR           shared, reused across builds (default <core>/../pio-shared, else ~/.cache/gstudio-fw):
      cache/                 PLATFORMIO_BUILD_CACHE_DIR (object cache: repeat builds are fast)
      libdeps/<hash>/        PLATFORMIO_LIBDEPS_DIR per pinned library set (installed once, under a lock)
      stage/<k>/proj         the project is compiled HERE, at a fixed path per build slot k, then results are
      stage/<k>/ws           copied back. Compiler command lines (include paths) then do not depend on the
                             output folder, so the object cache works across versions and designs, and the same
                             sources give a byte-identical firmware.bin wherever the version is stored.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ..errors import StageError

FLASH_MODES = {0: "qio", 1: "qout", 2: "dio", 3: "dout"}
FLASH_SIZES = {0: "1MB", 1: "2MB", 2: "4MB", 3: "8MB", 4: "16MB", 5: "32MB", 6: "64MB", 7: "128MB"}
FLASH_FREQS_S3 = {0x0: "40m", 0x2: "20m", 0x7: "120m", 0xF: "80m"}  # ESP32-S3 image header encoding (esptool)


def core_dir() -> Path:
    return Path(os.environ.get("PLATFORMIO_CORE_DIR") or "/opt/gs/pio")


def shared_dir() -> Path:
    env = os.environ.get("GS_FW_SHARED_DIR")
    cands = [Path(env)] if env else []
    cands += [core_dir().parent / "pio-shared", Path.home() / ".cache" / "gstudio-fw"]
    for c in cands:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".w"
            probe.write_text("ok")
            probe.unlink()
            return c
        except OSError:
            continue
    raise StageError("no writable directory for the PlatformIO cache", {"tried": [str(c) for c in cands]})


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


SLOTS = 4
OUTPUTS = {"firmware.bin", "firmware-merged.bin", "firmware.sig", "web-flasher-manifest.json", "SHA256SUMS", "size.json",
           "build-log.txt", "test-results.txt", "flash", "sdcard"}


@contextlib.contextmanager
def build_slot():
    """A fixed staging folder, locked for one build at a time (up to SLOTS builds run in parallel)."""
    base = shared_dir() / "stage"
    base.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for k in range(SLOTS):
            fh = open(base / f"{k}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                handles.append(fh)
                yield base / str(k)
                return
            except BlockingIOError:
                fh.close()
        fh = open(base / "0.lock", "a")
        fcntl.flock(fh, fcntl.LOCK_EX)
        handles.append(fh)
        yield base / "0"
    finally:
        for fh in handles:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()


def stage_project(fw: Path, slot: Path) -> Path:
    proj = slot / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    for item in fw.iterdir():
        if item.name in OUTPUTS or item.name.startswith("."):
            continue
        if item.is_dir():
            shutil.copytree(item, proj / item.name)
        else:
            shutil.copyfile(item, proj / item.name)
    return proj


def pio_exe() -> str:
    for c in (shutil.which("pio"), shutil.which("platformio"), "/opt/gs/venv/bin/pio"):
        if c and Path(c).exists():
            return c
    raise StageError("PlatformIO (pio) is not installed on this machine", {"fix": "pip install platformio==6.2.0"})


def _env(workspace: Path, lib_hash: str) -> dict:
    sh = shared_dir()
    env = dict(os.environ)
    env.setdefault("PLATFORMIO_CORE_DIR", str(core_dir()))
    env["PLATFORMIO_BUILD_CACHE_DIR"] = str(sh / "cache")
    env["PLATFORMIO_LIBDEPS_DIR"] = str(sh / "libdeps" / lib_hash)
    env["PLATFORMIO_WORKSPACE_DIR"] = str(workspace)
    env.setdefault("SOURCE_DATE_EPOCH", "0")
    env["PLATFORMIO_SETTING_ENABLE_TELEMETRY"] = "No"
    env["PLATFORMIO_SETTING_CHECK_PLATFORMIO_INTERVAL"] = "3650"
    env["PLATFORMIO_DISABLE_PROGRESSBAR"] = "true"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run(cmd: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int, str, float]:
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=timeout, errors="replace")
        return p.returncode, p.stdout, time.monotonic() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        return 124, out + f"\n[timed out after {timeout:.0f} s]\n", time.monotonic() - t0


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_size(log: str) -> dict:
    out: dict[str, Any] = {}
    for key, label in (("ram", "RAM"), ("flash", "Flash")):
        m = re.search(label + r":\s*\[[^\]]*\]\s*([\d.]+)%\s*\(used (\d+) bytes from (\d+) bytes\)", log)
        if m:
            out[key] = {"percent": float(m.group(1)), "used_bytes": int(m.group(2)), "total_bytes": int(m.group(3))}
    return out


def parse_warnings(log: str) -> list[str]:
    seen, out = set(), []
    for line in log.splitlines():
        if ": warning:" in line:
            # Only our own sources; framework and library warnings are noise for the maker.
            if not re.search(r"(^|[\s/])(src|lib/gs_core|include|test)/", line):
                continue
            w = re.sub(r"\s+", " ", line.strip())
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out


def parse_tests(log: str) -> tuple[Optional[int], Optional[int]]:
    m = re.search(r"(\d+)\s+test cases?:\s*(?:(\d+)\s+failed,\s*)?(?:(\d+)\s+skipped,\s*)?(\d+)\s+succeeded", log)
    if m:
        return int(m.group(4)), int(m.group(1))
    m = re.search(r"(\d+)\s+test cases?:\s*(\d+)\s+failed", log)
    if m:
        return 0, int(m.group(1))
    return None, None


def flash_params(bootloader: Path) -> dict:
    """Flash mode/size/frequency exactly as the build wrote them into the bootloader image header."""
    b = bootloader.read_bytes()[:4]
    if len(b) < 4 or b[0] != 0xE9:
        raise StageError("bootloader.bin has no ESP image header", {"path": str(bootloader)})
    return {"flash_mode": FLASH_MODES.get(b[2], "dio"), "flash_size": FLASH_SIZES.get(b[3] >> 4, "8MB"),
            "flash_freq": FLASH_FREQS_S3.get(b[3] & 0x0F, "80m")}


def esptool_exe() -> list[str]:
    base = core_dir() / "packages" / "tool-esptool"
    for name in ("esptool", "esptool.py"):
        p = base / name
        if p.exists():
            return [str(p)] if os.access(p, os.X_OK) and name == "esptool" else ["python3", str(p)]
    w = shutil.which("esptool") or shutil.which("esptool.py")
    if w:
        return [w]
    raise StageError("esptool not found for merge_bin", {})


def merge_bin(parts: list[tuple[int, Path]], out: Path, params: dict, env: dict) -> tuple[bool, str]:
    args = []
    for off, p in parts:
        args += [hex(off), str(p)]
    tool = esptool_exe()
    tries = [
        tool + ["--chip", "esp32s3", "merge-bin", "-o", str(out), "--flash-mode", params["flash_mode"],
                "--flash-freq", params["flash_freq"], "--flash-size", params["flash_size"]] + args,
        tool + ["--chip", "esp32s3", "merge_bin", "-o", str(out), "--flash_mode", params["flash_mode"],
                "--flash_freq", params["flash_freq"], "--flash_size", params["flash_size"]] + args,
    ]
    log = ""
    for cmd in tries:
        rc, o, _ = _run(cmd, out.parent, env, 120)
        log += "$ " + " ".join(cmd) + "\n" + o
        if rc == 0 and out.exists():
            return True, log
    return False, log


def build(fw: Path, lib_deps: list[str], build_key: str, *, run_tests: bool = True, timeout: float = 900,
          progress=None) -> dict:
    """pio run -e s3 (+ pio test -e native). Writes the result files into fw and returns facts."""
    fw = Path(fw).resolve()
    with build_slot() as slot:
        return _build_in(fw, stage_project(fw, slot), slot / "ws", lib_deps, run_tests, timeout, progress)


def _build_in(fw: Path, proj: Path, workspace: Path, lib_deps: list[str], run_tests: bool, timeout: float,
              progress) -> dict:
    pio = pio_exe()
    lib_hash = hashlib.sha256("\n".join(sorted(lib_deps)).encode()).hexdigest()[:16]
    env = _env(workspace, lib_hash)
    sh = shared_dir()
    res: dict[str, Any] = {"compiled": False, "tests_passed": None, "tests_total": None, "timings": {}}
    log_parts = [f"# Gadget Studio firmware build\n# project {fw} (compiled in the staging copy {proj})\n# PLATFORMIO_CORE_DIR={env['PLATFORMIO_CORE_DIR']}\n"
                 f"# libdeps {env['PLATFORMIO_LIBDEPS_DIR']}\n# workspace {env['PLATFORMIO_WORKSPACE_DIR']}\n"]
    rc, ver, _ = _run([pio, "--version"], proj, env, 60)
    res["platformio"] = ver.strip()
    if progress:
        progress("Installing pinned libraries (first build of this set only)", 32)
    with locked(sh / "libdeps" / f"{lib_hash}.lock"):
        rc, o, dt = _run([pio, "pkg", "install", "-e", "s3"], proj, env, timeout)
        log_parts.append("$ pio pkg install -e s3\n" + o)
        res["timings"]["deps_s"] = round(dt, 1)
        if rc != 0:
            (fw / "build-log.txt").write_text("\n".join(log_parts))
            raise StageError("could not install the firmware libraries", {"log": o[-3000:]})
    if progress:
        progress("Compiling for ESP32-S3", 40)
    rc, o, dt = _run([pio, "run", "-e", "s3"], proj, env, timeout)
    log_parts.append("$ pio run -e s3\n" + o)
    res["timings"]["compile_s"] = round(dt, 1)
    res["warnings"] = parse_warnings(o)
    res["size"] = parse_size(o)
    bdir = Path(env["PLATFORMIO_WORKSPACE_DIR"]) / "build" / "s3"
    app = bdir / "firmware.bin"
    if rc == 0 and app.exists():
        res["compiled"] = True
        shutil.copyfile(app, fw / "firmware.bin")
        flash = fw / "flash"
        flash.mkdir(exist_ok=True)
        boot_app0 = core_dir() / "packages" / "framework-arduinoespressif32" / "tools" / "partitions" / "boot_app0.bin"
        parts = [(0x0, bdir / "bootloader.bin"), (0x8000, bdir / "partitions.bin"), (0xE000, boot_app0), (0x10000, app)]
        for off, p in parts:
            shutil.copyfile(p, flash / p.name)
        params = flash_params(flash / "bootloader.bin")
        ok, mlog = merge_bin([(o_, flash / p.name) for o_, p in parts], fw / "firmware-merged.bin", params, env)
        log_parts.append(mlog)
        res["flash"] = dict(params, parts=[{"offset": hex(o_), "file": f"flash/{p.name}"} for o_, p in parts])
        res["merged"] = ok
        res["sha256"] = sha256(fw / "firmware.bin")
        res["bytes"] = (fw / "firmware.bin").stat().st_size
        if ok:
            res["merged_sha256"] = sha256(fw / "firmware-merged.bin")
            res["merged_bytes"] = (fw / "firmware-merged.bin").stat().st_size
        elf = bdir / "firmware.elf"
        size_tool = core_dir() / "packages" / "toolchain-xtensa-esp-elf" / "bin" / "xtensa-esp32s3-elf-size"
        if elf.exists() and size_tool.exists():
            rc2, so, _ = _run([str(size_tool), "-A", str(elf)], fw, env, 60)
            if rc2 == 0:
                secs = {}
                for line in so.splitlines():
                    m = re.match(r"^(\.\S+)\s+(\d+)\s+(\d+)", line.strip())
                    name = m.group(1) if m else ""
                    # Only what is loaded into the chip: no debug info, placeholders or non-loaded sections.
                    if m and int(m.group(2)) > 0 and not re.search(r"debug|dummy|noload|comment|xtensa|^\.xt\.|eh_frame|iram0.text_end", name):
                        secs[name] = int(m.group(2))
                res["size"]["sections"] = secs
    if progress:
        progress("Running native unit tests", 80)
    if run_tests:
        with locked(sh / "libdeps" / f"{lib_hash}.native.lock"):
            rc, o, dt = _run([pio, "test", "-e", "native", "-v"], proj, env, timeout)
        res["timings"]["tests_s"] = round(dt, 1)
        (fw / "test-results.txt").write_text(f"$ pio test -e native -v\n# {res['platformio']}\n" + o)
        p, t = parse_tests(o)
        res["tests_passed"], res["tests_total"] = p, t
        res["tests_ok"] = rc == 0 and t is not None and p == t
    (fw / "build-log.txt").write_text("\n".join(log_parts))
    return res


__all__ = ["build", "parse_size", "parse_tests", "flash_params", "shared_dir", "sha256"]
