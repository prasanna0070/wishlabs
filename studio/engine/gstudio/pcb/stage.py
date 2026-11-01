"""End-to-end PCB stage: backplane-v1 + electrical.json -> KiCad project, routed board, checks, fab files, visuals."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from ..errors import StageError
from . import build as B
from . import fab, kicad_cli, route, visuals
from .design import Design

PCB_FILE = "backplane.kicad_pcb"


def _pcb_steps(design: Design, pcb_dir: Path, timings: dict, options: dict, progress, sch_nets: dict,
               bp: dict) -> dict:
    import pcbnew  # noqa: F401  (imported here so `gstudio.pcb` imports without KiCad)

    from .board import CLASSES, RULES, BoardBuilder, add_stackup, sym_uuid_fn
    from .silk import Silk

    t = time.time()
    pcb_path = pcb_dir / PCB_FILE
    bb = BoardBuilder(design, pcb_path, sym_uuid_fn(design.design_id))
    bb.setup()
    bb.add_nets()
    bb.outline()
    bb.place()
    bb.keepouts()
    envelopes = bb.envelope_models(pcb_dir, bp["slots"])
    bb.devkit_outline()
    silk = Silk(bb)
    for p in design.parts:
        if p.block in ("test", "mech") or p.board_only or not p.ref.startswith("J_"):
            continue
        silk.port(p, name_label=not p.ref.startswith("J_U1"))
    silk.top_title("GADGET STUDIO", f"{design.backplane_id} rev {design.revision}")
    silk.bottom_identity(f"GADGET STUDIO {design.backplane_id.upper()}",
                         f"rev {design.revision}  {design.slug}  {design.date}", f"ID {design.design_id[:8]}")
    silk.test_pads([p for p in design.parts if p.ref.startswith("TP")])
    silk.refdes(design.parts)
    bb.silk_boxes = list(silk.top_boxes) + list(silk.bot_boxes)  # stitching vias stay out of silk text
    bb.assign_unconnected(sch_nets)
    waived = {tuple(sorted(w.get("refs", []))) for w in design.waivers if w.get("rule") == "courtyards_overlap"}
    overlaps = [o for o in bb.courtyard_overlaps() if tuple(sorted(o)) not in waived]
    write_rules(pcb_dir, design)
    bb.reload()
    timings["board"] = round(time.time() - t, 2)
    progress(f"board placed: {len(bb.fps)} footprints", 25)

    # ---------------- routing
    t = time.time()
    dsn = pcb_dir / "board.dsn"
    ses = pcb_dir / "board.ses"
    bb.remove_gnd_zones()
    fan = bb.usb_fanout()
    route_clr = CLASSES["Default"].get("route_clearance")
    route.export_dsn(bb.board, dsn, route_clr)
    passes = int(options.get("route_passes", 12 if options.get("fast") else 30))
    fr = route.freeroute(dsn, ses, passes=passes, threads=1, timeout=float(options.get("route_timeout", 170)))
    route.import_ses(bb.board, ses)
    left = fr.get("unrouted_reported") or 0
    if left:  # one retry from scratch with twice the passes before giving up
        progress(f"{left} connections unrouted after {passes} passes; retrying with {passes * 2}", 45)
        bb.load_from_disk()  # the saved file is still the unrouted board
        bb.remove_gnd_zones()
        bb.usb_fanout()
        route.export_dsn(bb.board, dsn, route_clr)
        fr = route.freeroute(dsn, ses, passes=passes * 2, threads=1, timeout=float(options.get("route_timeout", 170)))
        route.import_ses(bb.board, ses)
        fr["retried"] = True
    fr["usb_fanout_vias"] = fan
    timings["route"] = round(time.time() - t, 2)
    progress(f"routed with Freerouting in {fr['seconds']} s", 55)

    t = time.time()
    bb.gnd_zones()
    bb.fill()
    fr["necks_widened"] = route.widen_necks(bb.board)
    if fr["necks_widened"]:
        bb.fill()
    stitched = bb.stitch()
    bb.fill()
    islands = bb.stitch_islands()
    if islands:
        bb.fill()
        islands += bb.stitch_islands()
        bb.fill()
    stitched += islands
    st = bb.stats()
    bb.save()
    add_stackup(pcb_path)
    timings["zones_save"] = round(time.time() - t, 2)
    if st["unrouted"]:
        progress(f"{st['unrouted']} connections left unrouted", 60)
    return {"pcb": pcb_path, "overlaps": overlaps, "envelopes": envelopes, "freerouting": fr, "stitch_vias": stitched, "stats": st,
            "placement": bb.placement_log, "silk_skipped": silk.skipped, "rules": RULES,
            "classes": {k: {kk: vv for kk, vv in v.items()} for k, v in CLASSES.items()}}


def fix_starved(design: Design, pcb_path: Path, positions: list[tuple[float, float]]) -> int:
    """Second pass for DRC 'starved thermal': solid-connect those pads, refill, save (stackup is kept in the file)."""
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb_path))
    n = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            px, py = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
            if any(abs(px - x) < 0.02 and abs(py - y) < 0.02 for x, y in positions):
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
                n += 1
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(pcb_path), board)
    return n


def write_rules(pcb_dir: Path, design: Design) -> Path:
    """Custom DRC rules (backplane.kicad_dru): backplane-v1 drc_waivers become explicit, reviewable KiCad rules."""
    lines = ["(version 1)"]
    for w in design.waivers:
        refs = w.get("refs", [])
        if w.get("rule") == "courtyards_overlap" and len(refs) == 2:
            a, b = refs
            why = str(w.get("why", "")).replace('"', "'")
            lines += [f'(rule "backplane-v1 waiver {a}/{b}: {why[:120]}"',
                      f"\t(condition \"(A.Reference == '{a}' && B.Reference == '{b}') || "
                      f"(A.Reference == '{b}' && B.Reference == '{a}')\")",
                      "\t(constraint courtyard_clearance (min -1mm)))"]
    path = pcb_dir / "backplane.kicad_dru"
    path.write_text("\n".join(lines) + "\n")
    return path


def patch_project(pcb_dir: Path, design: Design) -> None:
    pro = pcb_dir / f"{B.PROJECT}.kicad_pro"
    data = json.loads(pro.read_text()) if pro.exists() else {}
    data.setdefault("meta", {"filename": pro.name, "version": 3})
    data["text_variables"] = {"DESIGN_ID": design.design_id, "ENGINE": design.engine_version,
                              "BACKPLANE": f"{design.backplane_id} {design.backplane_version}"}
    pro.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def build_pcb(backplane_src, electrical: dict, pcb_dir: Path, meta: dict, options: Optional[dict] = None,
              progress: Optional[Callable[[str, Optional[float]], None]] = None,
              lib_footprints: Optional[dict] = None) -> dict:
    options = dict(options or {})
    prog = progress or (lambda m, p=None: None)
    pcb_dir = Path(pcb_dir).resolve()
    if pcb_dir.exists() and options.get("clean", True):
        for child in pcb_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    pcb_dir.mkdir(parents=True, exist_ok=True)
    timings: dict = {}
    t0 = time.time()
    bp, design = B.load_inputs(backplane_src, electrical, meta, lib_footprints)
    prog(f"design: {len(design.parts)} parts on {len(design.used_slots)} slots", 3)

    sch = B.make_schematic(design, pcb_dir, bp, timings, prog)
    if sch["erc"]["errors"]:
        prog(f"ERC reports {sch['erc']['errors']} errors (continuing; see erc.json)", 12)

    board = _pcb_steps(design, pcb_dir, timings, options, prog, sch["sch_nets"], bp)
    patch_project(pcb_dir, design)

    t = time.time()
    drc_raw = kicad_cli.drc(board["pcb"], pcb_dir / "drc.json", parity=True)
    starved = [i["pos"] for v in drc_raw.get("violations", []) if v.get("type") == "starved_thermal"
               for i in v.get("items", []) if " pad " in f" {i.get('description', '')}"]
    if starved:
        board["thermal_fixes"] = fix_starved(design, board["pcb"], [(p["x"], p["y"]) for p in starved])
        drc_raw = kicad_cli.drc(board["pcb"], pcb_dir / "drc.json", parity=True)
    drc = B.drc_summary(drc_raw, design.waivers)
    timings["drc"] = round(time.time() - t, 2)
    prog(f"DRC {drc['errors']} errors, {drc['unconnected']} unconnected, parity issues {drc['parity_issues']}", 70)

    t = time.time()
    g = fab.gerbers(board["pcb"], pcb_dir)
    layers = fab.zip_layers(Path(g["zip"]))
    bom = fab.bom(design, pcb_dir / "bom.csv")
    cpl = fab.cpl(board["pcb"], pcb_dir, design)
    timings["fab"] = round(time.time() - t, 2)
    prog("Gerbers, drill, BOM and CPL written", 78)

    t = time.time()
    vis = visuals.make_all(board["pcb"], pcb_dir / f"{B.PROJECT}.kicad_sch", pcb_dir, design, options)
    timings["visuals"] = round(time.time() - t, 2)
    prog("renders, SVG plots and GLB written", 97)
    timings["total"] = round(time.time() - t0, 2)
    summary = summarize(design, pcb_dir, sch, board, drc, g, layers, bom, cpl, vis, timings)
    if not summary["routed"]:
        raise StageError(f"Autorouting left {summary['unrouted']} connections unrouted (Freerouting "
                         f"{board['freerouting'].get('passes')} passes, retried); outputs kept in pcb/ for review",
                         {"unrouted": summary["unrouted"], "summary": str(pcb_dir / "pcb-summary.json"),
                          "drc": str(pcb_dir / "drc.json")})
    return summary


def summarize(design, pcb_dir, sch, board, drc, g, layers, bom, cpl, vis, timings) -> dict:
    st = board["stats"]
    files = sorted(str(p.relative_to(pcb_dir.parent)) for p in pcb_dir.rglob("*") if p.is_file())
    routed = st["unrouted"] == 0 and drc["unconnected"] == 0
    checks = [
        {"id": "pcb.erc", "title": "Schematic ERC has no errors", "kind": "digital",
         "status": "passed" if sch["erc"]["errors"] == 0 else "failed",
         "detail": f"{sch['erc']['errors']} errors, {sch['erc']['warnings']} warnings"},
        {"id": "pcb.netlist", "title": "Schematic netlist equals electrical.json (on-board nodes)", "kind": "digital",
         "status": "passed" if sch["netlist"]["ok"] else "failed",
         "detail": f"{sch['netlist']['electrical_nodes_checked']} nodes checked; "
                   + ("; ".join(sch["netlist"]["problems"][:5]) or "no differences")},
        {"id": "pcb.drc", "title": "Board DRC has no errors", "kind": "digital",
         "status": "passed" if drc["errors"] == 0 else "failed",
         "detail": f"{drc['errors']} errors, {drc['warnings']} warnings, {drc['waived']} waived (backplane-v1 drc_waivers)"},
        {"id": "pcb.routed", "title": "Every connection is routed", "kind": "digital",
         "status": "passed" if routed else "failed",
         "detail": f"{st['unrouted']} unrouted (pcbnew), {drc['unconnected']} unconnected (DRC)"},
        {"id": "pcb.parity", "title": "Board matches the schematic (DRC schematic parity)", "kind": "digital",
         "status": "passed" if drc["parity_issues"] == 0 else "failed", "detail": f"{drc['parity_issues']} parity issues"},
        {"id": "pcb.courtyards", "title": "No overlapping courtyards", "kind": "digital",
         "status": "passed" if not board["overlaps"] else "failed",
         "detail": "none" if not board["overlaps"] else ", ".join("/".join(o) for o in board["overlaps"])},
        {"id": "pcb.gerbers", "title": "Gerber zip holds copper, mask, silk, paste, outline and drill", "kind": "digital",
         "status": "passed" if all(layers.values()) else "failed",
         "detail": ", ".join(k for k, v in layers.items() if not v) or "all layers present"},
        {"id": "pcb.fabricated", "title": "Board fabricated and electrically tested", "kind": "physical",
         "status": "not_run", "detail": "Physical checks still needed: no board has been made."},
    ]
    summary = {
        "layers": 2,
        "size_mm": [design.width, design.height],
        "thickness_mm": design.thickness,
        "drc_errors": drc["errors"],
        "drc_warnings": drc["warnings"],
        "erc_errors": sch["erc"]["errors"],
        "erc_warnings": sch["erc"]["warnings"],
        "routed": routed,
        "unrouted": max(st["unrouted"], drc["unconnected"]),
        "parity_issues": drc["parity_issues"],
        "track_length_mm": st["track_length_mm"],
        "vias": st["vias"],
        "stitch_vias": board["stitch_vias"],
        "footprints": len([p for p in design.parts]),
        "nets": sch["netlist"]["nets"],
        "slots_used": design.used_slots,
        "slots_omitted": design.omitted_slots,
        "design_id": design.design_id,
        "backplane": f"{design.backplane_id} {design.backplane_version}",
        "rules": board["rules"],
        "net_classes": {k: {"track_mm": v["track"], "clearance_mm": v["clearance"],
                            "route_clearance_mm": v.get("route_clearance", v["clearance"]),
                            "via_mm": [v["via_d"], v["via_drill"]], "nets": v["nets"]}
                        for k, v in board["classes"].items()},
        "freerouting": board["freerouting"],
        "deviations": design.deviations,
        "envelope_models": board.get("envelopes", []),
        "thermal_fixes": board.get("thermal_fixes", 0),
        "splits": design.splits,
        "files": files,
        "renders": vis,
        "bom_lines": bom["lines"],
        "cpl_placements": cpl["placements"],
        "checks": checks,
        "timings_s": timings,
        "say": (f"Routed the {design.width:g} x {design.height:g} mm backplane: {drc['errors']} DRC errors, "
                f"{sch['erc']['errors']} ERC errors, {max(st['unrouted'], drc['unconnected'])} unrouted, "
                f"{st['vias']} vias, {st['track_length_mm']:.0f} mm of track."),
    }
    (pcb_dir / "pcb-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


__all__ = ["build_pcb"]
