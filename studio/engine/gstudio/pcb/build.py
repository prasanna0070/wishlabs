"""PCB stage orchestration: design -> schematic (+ERC, netlist) -> board -> route -> DRC -> fab -> visuals."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from ..errors import StageError
from . import genlib, kicad_cli, netlist, schematic
from .design import Design, build_design, load_backplane

PROJECT = "backplane"
Progress = Callable[[str, Optional[float]], None]


def _noop(msg: str, pct: Optional[float] = None) -> None:
    pass


def erc_summary(rep: dict) -> dict:
    errors, warnings = [], []
    for sheet in rep.get("sheets", []):
        for v in sheet.get("violations", []):
            item = {"type": v.get("type"), "description": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])]}
            (errors if v.get("severity") == "error" else warnings).append(item)
    return {"errors": len(errors), "warnings": len(warnings), "error_items": errors, "warning_items": warnings}


def drc_summary(rep: dict, waivers: list[dict]) -> dict:
    errors, warnings, waived = [], [], []
    waiver_refs = [set(w.get("refs", [])) for w in waivers]

    def _waived(v) -> bool:
        txt = " ".join(i.get("description", "") for i in v.get("items", []))
        for refs, w in zip(waiver_refs, waivers):
            if v.get("type") == w.get("rule") and all(r in txt for r in refs):
                return True
        return False

    for v in rep.get("violations", []):
        item = {"type": v.get("type"), "severity": v.get("severity"), "description": v.get("description"),
                "items": [i.get("description") for i in v.get("items", [])]}
        if _waived(v):
            waived.append(item)
        elif v.get("severity") == "error":
            errors.append(item)
        else:
            warnings.append(item)
    unconnected = rep.get("unconnected_items", [])
    parity = rep.get("schematic_parity", [])
    return {"errors": len(errors), "warnings": len(warnings), "waived": len(waived), "unconnected": len(unconnected),
            "parity_issues": len(parity), "error_items": errors, "warning_items": warnings, "waived_items": waived,
            "unconnected_items": [[i.get("description") for i in u.get("items", [])] for u in unconnected],
            "parity_items": [{"type": p.get("type"), "description": p.get("description"),
                              "items": [i.get("description") for i in p.get("items", [])]} for p in parity]}


def lib_sets(design: Design) -> tuple[set[str], set[str]]:
    sym = {p.symbol.split(":")[0] for p in design.parts if p.symbol}
    sym |= {"power"}
    fp = {p.footprint.split(":")[0] for p in design.parts}
    return sym, fp


def generated_symbols(design: Design) -> list[list]:
    nodes = {}
    for p in design.parts:
        if p.gen_symbol and p.symbol not in nodes:
            g = p.gen_symbol
            nodes[p.symbol] = genlib.socket_symbol(g["name"], g["title"], g["pins"], g["side"], p.footprint)
    return [nodes[k] for k in sorted(nodes)]


def generated_footprints(design: Design) -> list[tuple[str, list]]:
    out = {}
    for p in design.parts:
        lib, name = p.footprint.split(":", 1)
        if lib == genlib.LIB and name.startswith("NPTH_"):
            d = float(name[len("NPTH_"):-2])
            out[name] = genlib.npth_footprint(d)
    return [out[k] for k in sorted(out)]


def write_project_skeleton(pcb_dir: Path) -> Path:
    pro = pcb_dir / f"{PROJECT}.kicad_pro"
    if not pro.exists():
        pro.write_text(json.dumps({"meta": {"filename": f"{PROJECT}.kicad_pro", "version": 3}}, indent=2) + "\n")
    return pro


def make_schematic(design: Design, pcb_dir: Path, bp: dict, timings: dict, progress: Progress = _noop) -> dict:
    t = time.time()
    gen_syms = generated_symbols(design)
    sym_libs, fp_libs = lib_sets(design)
    genlib.write_project_libs(pcb_dir, gen_syms, generated_footprints(design), sym_libs, fp_libs)
    write_project_skeleton(pcb_dir)
    sch = pcb_dir / f"{PROJECT}.kicad_sch"
    info = schematic.write_schematic(design, sch, gen_syms, PROJECT)
    timings["schematic"] = round(time.time() - t, 2)
    progress("schematic written", 8)
    t = time.time()
    rep = kicad_cli.erc(sch, pcb_dir / "erc.json")
    erc = erc_summary(rep)
    timings["erc"] = round(time.time() - t, 2)
    t = time.time()
    net_path = kicad_cli.netlist(sch, pcb_dir / f"{PROJECT}.net")
    sch_nets = netlist.parse_kicad_netlist(net_path)
    cmp = netlist.compare(design, sch_nets, bp)
    nl = netlist.to_json(sch_nets)
    nl["comparison"] = cmp
    (pcb_dir / "netlist.json").write_text(json.dumps(nl, indent=2) + "\n")
    timings["netlist"] = round(time.time() - t, 2)
    progress(f"ERC {erc['errors']} errors, {erc['warnings']} warnings; netlist match {cmp['ok']}", 12)
    return {"sch": sch, "erc": erc, "netlist": cmp, "info": info, "sch_nets": sch_nets}


def load_inputs(backplane_src, electrical: dict, meta: dict, lib_footprints: dict | None = None) -> tuple[dict, Design]:
    bp = load_backplane(backplane_src)
    design = build_design(bp, electrical, meta, lib_footprints)
    return bp, design


__all__ = ["make_schematic", "load_inputs", "erc_summary", "drc_summary", "PROJECT"]
