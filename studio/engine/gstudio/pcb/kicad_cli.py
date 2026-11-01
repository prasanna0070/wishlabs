"""Thin wrappers around kicad-cli 9 (ERC, DRC, exports, renders). Every call has a timeout and returns paths."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..errors import StageError

KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")


def env() -> dict:
    e = dict(os.environ)
    e.setdefault("KICAD9_3DMODEL_DIR", "/usr/share/kicad/3dmodels")
    e.setdefault("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
    e.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")
    e.setdefault("KICAD_3DMODEL_DIR", "/usr/share/kicad/3dmodels")
    return e


def run(args: list[str], timeout: float = 180.0, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    if shutil.which(KICAD_CLI) is None:
        raise StageError("kicad-cli not found on PATH", {"cli": KICAD_CLI})
    cmd = [KICAD_CLI, *args]
    try:
        cp = subprocess.run(cmd, cwd=cwd, env=env(), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise StageError(f"kicad-cli timed out: {' '.join(args[:3])}", {"cmd": cmd}) from exc
    if check and cp.returncode not in (0, 5):  # 5 = violations found (erc/drc with --exit-code-violations)
        raise StageError(f"kicad-cli failed: {' '.join(args[:3])}",
                         {"cmd": cmd, "rc": cp.returncode, "stderr": cp.stderr[-2000:], "stdout": cp.stdout[-2000:]})
    return cp


def erc(sch: Path, out_json: Path) -> dict:
    run(["sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out_json), str(sch)])
    return json.loads(out_json.read_text())


def drc(pcb: Path, out_json: Path, parity: bool = True) -> dict:
    args = ["pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "--all-track-errors"]
    if parity:
        args.append("--schematic-parity")
    run([*args, "-o", str(out_json), str(pcb)], timeout=300)
    return json.loads(out_json.read_text())


def netlist(sch: Path, out: Path) -> Path:
    run(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)])
    return out


def sch_svg(sch: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["sch", "export", "svg", "-o", str(out_dir), str(sch)])
    return sorted(out_dir.glob("*.svg"))


def sch_pdf(sch: Path, out: Path) -> Path:
    run(["sch", "export", "pdf", "-o", str(out), str(sch)])
    return out


def gerbers(pcb: Path, out_dir: Path, layers: list[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "gerbers", "--layers", ",".join(layers), "--subtract-soldermask",
         "--use-drill-file-origin", "-o", str(out_dir) + "/", str(pcb)])
    return sorted(out_dir.iterdir())


def drill(pcb: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "drill", "--format", "excellon", "--drill-origin", "plot", "--excellon-units", "mm",
         "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2", "-o", str(out_dir) + "/", str(pcb)])
    return sorted(out_dir.iterdir())


def pos(pcb: Path, out: Path) -> Path:
    run(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "--exclude-dnp",
         "--use-drill-file-origin", "-o", str(out), str(pcb)])
    return out


def pcb_svg(pcb: Path, out: Path, layers: list[str], mirror: bool = False) -> Path:
    args = ["pcb", "export", "svg", "--layers", ",".join(layers), "--mode-single", "--exclude-drawing-sheet",
            "--page-size-mode", "2", "--subtract-soldermask"]
    if mirror:
        args.append("--mirror")
    run([*args, "-o", str(out), str(pcb)])
    return out


def render(pcb: Path, out: Path, side: str, width: int = 1800, height: int = 1300, zoom: float = 1.0,
           quality: str = "high", timeout: float = 300.0) -> Path:
    run(["pcb", "render", "--side", side, "--width", str(width), "--height", str(height), "--quality", quality,
         "--background", "opaque", "--zoom", str(zoom), "--floor", "-o", str(out), str(pcb)], timeout=timeout)
    return out


def glb(pcb: Path, out: Path) -> Path:
    run(["pcb", "export", "glb", "--subst-models", "--include-tracks", "--include-pads",
         "--include-silkscreen", "--include-soldermask", "--force", "-o", str(out), str(pcb)], timeout=300)
    return out


def help_text(*sub: str) -> str:
    cp = run([*sub, "--help"], check=False)
    return cp.stdout + cp.stderr


__all__ = ["erc", "drc", "netlist", "sch_svg", "sch_pdf", "gerbers", "drill", "pos", "pcb_svg", "render", "glb",
           "help_text", "run", "env"]
