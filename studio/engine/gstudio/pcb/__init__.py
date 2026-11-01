"""Gadget Studio PCB stage: a real, routed, DRC-checked backplane PCB for every design.

    gstudio.pcb.run(ctx) -> summary dict (for build.json `pcb` and digital checks)

From library/backplane-v1.json (fixed outline, holes, slot positions, pin maps) and electrical.json (which slots this
design populates and their nets), it writes into <out>/pcb/:

  backplane.kicad_pro/.kicad_sch/.kicad_pcb (+ gstudio.kicad_sym, gstudio.pretty/, fp-lib-table, sym-lib-table)
  erc.json, drc.json, netlist.json, pcb-summary.json
  gerbers/ + backplane-gerbers.zip, bom.csv (pinned maker, MPN and LCSC id per line), cpl.csv, pcba.json (PCBA estimate)
  schematic.svg/.pdf/.png, board-top.png, board-bottom.png, board-iso.png, board-top.svg, board-bottom.svg, board.glb

The stage is keyed by a hash of its board inputs (see reuse.py): when they did not change since a previous build it
copies that build's files instead of routing again, and reports `reused`.

Importing this package is cheap: pcbnew is imported only inside the board/route steps.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from ..errors import StageError

STAGE = "pcb"


def studio_root() -> Path:
    return Path(__file__).resolve().parents[3]


def backplane_path(ctx: Any = None) -> Path:
    opt = (getattr(ctx, "options", None) or {}).get("backplane") if ctx is not None else None
    if opt:
        return Path(opt)
    lib = getattr(ctx, "lib", None) if ctx is not None else None
    for attr in ("backplane_path", "backplane_file"):
        p = getattr(lib, attr, None)
        if p:
            return Path(p)
    env = os.environ.get("GSTUDIO_LIBRARY_DIR")
    base = Path(env) if env else studio_root() / "library"
    return base / "backplane-v1.json"


def module_footprints(ctx: Any = None) -> dict:
    """{module_id: kicad.backplane_footprint} from the module library (used for the USB-C footprint choice)."""
    path = backplane_path(ctx).parent / "modules.json"
    out: dict = {}
    if path.exists():
        try:
            for m in json.loads(path.read_text()):
                fp = (m.get("kicad") or {}).get("backplane_footprint")
                if fp:
                    out[m["id"]] = fp
        except (ValueError, KeyError, TypeError):
            pass
    return out


def library_modules(ctx: Any = None, ids: tuple[str, ...] = ("AMP_MAX98357A_I2S",)) -> dict:
    """{module_id: module row} for the few library rows the PCB rules read (the amp's SD network for MIN-1)."""
    lib = getattr(ctx, "lib", None) if ctx is not None else None
    out: dict = {}
    for mid in ids:
        m = None
        if lib is not None and hasattr(lib, "get"):
            try:
                m = lib.get(mid)
            except Exception:  # noqa: BLE001 - the library object may raise for unknown ids
                m = None
        if m is not None and not isinstance(m, dict):
            m = m.model_dump() if hasattr(m, "model_dump") else getattr(m, "__dict__", None)
        if m:
            out[mid] = m
    path = backplane_path(ctx).parent / "modules.json"
    if len(out) < len(ids) and path.exists():
        try:
            for m in json.loads(path.read_text()):
                if m.get("id") in ids and m["id"] not in out:
                    out[m["id"]] = m
        except (ValueError, KeyError, TypeError):
            pass
    return out


def _meta(ctx: Any) -> dict:
    from ..version import ENGINE_VERSION

    spec = getattr(ctx, "spec", None)
    opts = getattr(ctx, "options", None) or {}
    name = getattr(spec, "name", None) or opts.get("name") or "Gadget"
    slug = getattr(spec, "slug", None) or opts.get("slug") or "gadget"
    ver = opts.get("version")
    date = opts.get("date")
    proj = Path(ctx.out) / "project.json"
    if proj.exists():
        try:
            pj = json.loads(proj.read_text())
            ver = ver or pj.get("version")
            date = date or (pj.get("created") or "")[:10] or None
        except ValueError:
            pass
    return {"name": name, "slug": slug, "revision": f"v{ver}" if ver else "A",
            "date": date or _dt.date.today().isoformat(), "engine_version": ENGINE_VERSION}


def run(ctx: Any) -> dict:
    from .stage import build_pcb

    electrical = getattr(ctx, "electrical", None)
    if electrical is None:
        ep = Path(ctx.out) / "electrical.json"
        if not ep.exists():
            raise StageError("pcb needs electrical.json (run the electrical stage first)", {"path": str(ep)})
        electrical = json.loads(ep.read_text())
    bpp = backplane_path(ctx)
    if not bpp.exists():
        raise StageError("backplane-v1.json not found", {"path": str(bpp)})

    def progress(msg: str, pct: float | None = None) -> None:
        ctx.progress(STAGE, msg, pct)

    summary = build_pcb(bpp, electrical, Path(ctx.out) / "pcb", _meta(ctx), getattr(ctx, "options", None) or {},
                        progress, module_footprints(ctx), library_modules(ctx))
    arts = getattr(ctx, "artifacts", None)
    if isinstance(arts, dict):
        for k in ("board_glb", "board_top_png", "board_bottom_png", "schematic_svg"):
            if k in summary.get("renders", {}):
                arts[f"pcb.{k}"] = str(Path(ctx.out) / "pcb" / summary["renders"][k])
    return summary


__all__ = ["run", "backplane_path", "module_footprints", "library_modules"]
