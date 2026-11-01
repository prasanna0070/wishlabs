"""Autorouting: Specctra DSN export -> Freerouting 2.5 headless -> SES import (pcbnew)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pcbnew

from ..errors import StageError


def java() -> str:
    return os.environ.get("GS_JAVA", "java")


def jar() -> str:
    j = os.environ.get("GS_FREEROUTING_JAR", "/opt/gs/freerouting.jar")
    if not Path(j).exists():
        raise StageError("Freerouting jar not found", {"GS_FREEROUTING_JAR": j})
    return j


MIN_TRACK_MM = 0.15


def export_dsn(board: pcbnew.BOARD, dsn: Path, signal_route_clearance_mm: float | None = None) -> Path:
    """Export Specctra DSN. With signal_route_clearance_mm, the board-default rule and the kicad_default (signal)
    class are routed at that clearance, a little more than KiCad checks, so neck-downs can be widened afterwards."""
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        raise StageError("Specctra DSN export failed", {"dsn": str(dsn)})
    if signal_route_clearance_mm:
        txt = dsn.read_text()
        if "(unit um)" not in txt and "(resolution um" not in txt:
            raise StageError("unexpected DSN units (expected um)", {"dsn": str(dsn)})
        val = f"(clearance {signal_route_clearance_mm * 1000:g})"  # DSN values are in um (width 250 = 0.25 mm)
        plain = re.compile(r"\(clearance \d+(?:\.\d+)?\)")
        # 1) structure-level default rule (first plain clearance in the file)
        txt = plain.sub(val, txt, count=1)
        # 2) the kicad_default class block only
        i = txt.find("(class kicad_default")
        if i >= 0:
            j = txt.find("(class ", i + 5)
            j = len(txt) if j < 0 else j
            txt = txt[:i] + plain.sub(val, txt[i:j]) + txt[j:]
        dsn.write_text(txt)
    return dsn


def widen_necks(board: pcbnew.BOARD, min_mm: float = MIN_TRACK_MM) -> int:
    """Freerouting necks traces down at small pins; any segment under the fab minimum is widened to it. Signals are
    routed at 0.20 mm and checked at 0.18 mm, so widening a 0.125 mm neck to 0.15 mm keeps >= 0.1875 mm."""
    n = 0
    w = pcbnew.FromMM(min_mm)
    for t in board.GetTracks():
        if t.GetClass() in ("PCB_TRACK", "PCB_ARC") and t.GetWidth() < w:
            t.SetWidth(w)
            n += 1
    return n


def freeroute(dsn: Path, ses: Path, passes: int = 25, threads: int = 1, timeout: float = 150.0) -> dict:
    """Run Freerouting headless. One thread + prioritized selection keeps the result deterministic."""
    dsn, ses = Path(dsn).resolve(), Path(ses).resolve()
    if ses.exists():
        ses.unlink()
    cmd = [java(), "-Djava.awt.headless=true", "-jar", jar(), "-de", str(dsn), "-do", str(ses), "-mp", str(passes),
           "-mt", str(threads), "-l", "en", "--gui.enabled=false", "--api_server.enabled=false",
           "--feature_flags.save_jobs=0"]
    t = time.time()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(dsn.parent))
    except subprocess.TimeoutExpired as exc:
        raise StageError("Freerouting did not finish in time", {"timeout_s": timeout, "dsn": str(dsn)}) from exc
    log = (cp.stdout or "") + (cp.stderr or "")
    (dsn.parent / "freerouting.log").write_text(log)
    stray = dsn.parent / "en"  # Freerouting 2.5 also writes its own log under ./<-l value>/; keep only ours
    if stray.is_dir():
        shutil.rmtree(stray, ignore_errors=True)
    if not ses.exists():
        raise StageError("Freerouting produced no session file",
                         {"rc": cp.returncode, "log_tail": log[-3000:], "cmd": cmd})
    unrouted = None
    m = re.findall(r"final score: [\d.]+ \((\d+) unrouted", log) or re.findall(r"(\d+)\s+unrouted", log)
    if m:
        unrouted = int(m[-1])
    return {"seconds": round(time.time() - t, 1), "rc": cp.returncode, "unrouted_reported": unrouted,
            "passes": passes}


def import_ses(board: pcbnew.BOARD, ses: Path) -> None:
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        raise StageError("Specctra SES import failed", {"ses": str(ses)})


__all__ = ["export_dsn", "freeroute", "import_ses"]
