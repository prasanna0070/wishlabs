"""End-to-end PCB stage: backplane-v1 + electrical.json -> KiCad project, routed board, checks, fab files, visuals."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from ..errors import StageError
from . import build as B
from . import fab, kicad_cli, reuse, route, rules, silk_audit, sourcing, visuals
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
    silk.refdes(design.parts)
    # U1 (the devkit) has no single connector to name: print its designator between its two sockets (REVIEW-1 MAJ-16)
    if "J_U1A" in bb.boxes and "J_U1B" in bb.boxes:
        a_, b_ = bb.boxes["J_U1A"], bb.boxes["J_U1B"]
        silk.free_label(["U1", "DevKitC-1"], ((a_[0] + a_[2]) / 2, (min(a_[3], b_[3]) + max(a_[1], b_[1])) / 2),
                        size=1.4, tag="U1 label")
    # no version number or date on copper or silk: the same electrical design prints the same board (MIN-36)
    silk.top_title("WISH LABS", f"BP1 {design.backplane_id} rev {design.revision}")
    silk.bottom_identity(f"WISH LABS {design.backplane_id.upper()}",
                         f"BP1 rev {design.revision}  {design.slug}", f"ID {design.design_id[:8]}")
    silk.test_pads([p for p in design.parts if p.ref.startswith("TP")])
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


def _raw_backplane(backplane_src) -> dict:
    return backplane_src if isinstance(backplane_src, dict) else json.loads(Path(backplane_src).read_text())


def try_reuse(backplane_src, electrical: dict, pcb_dir: Path, meta: dict, options: dict,
              lib_footprints: Optional[dict], prog) -> tuple[str, Optional[dict]]:
    """(pcb_key, summary of a reused board or None). REVIEW-1 MIN-36."""
    t0 = time.time()
    key = reuse.pcb_key(_raw_backplane(backplane_src), electrical, meta.get("slug", "gadget"), options, lib_footprints)
    if options.get("reuse") is False:
        return key, None
    hit = reuse.find(pcb_dir.parent, key, options, log=lambda m: prog(m, None))
    if hit is None:
        return key, None
    label, src, prev = hit
    reuse.adopt(src, pcb_dir)
    if label.startswith("gs://"):  # downloaded into a temp folder by reuse.find
        shutil.rmtree(src.parent, ignore_errors=True)
    s = dict(prev)
    secs = round(time.time() - t0, 2)
    s["reused"] = {"from": label if label != "cache" else f"cache {src}", "pcb_key": key, "seconds": secs,
                   "original_timings_s": prev.get("timings_s"), "why": "electrical inputs unchanged (same pcb_key)"}
    s["timings_s"] = {"total": secs, "reused": True}
    if reuse.scrub_paths(pcb_dir):  # a board cached before MIN-19 may still name its build folder
        s = reuse.scrub_obj(s)
        s["file_sha256"] = reuse.file_hashes(pcb_dir)
    s["say"] = (f"Reused the routed board from {s['reused']['from']} in {secs:g} s: the electrical design did not change "
                f"(rev {s.get('revision', '')}). " + str(prev.get("say", "")))
    (pcb_dir / reuse.SUMMARY).write_text(json.dumps(s, indent=2) + "\n")
    prog(f"reused: board unchanged (rev {s.get('revision', '')}), copied from {s['reused']['from']}", 100)
    return key, s


def count_joints(pcb_path: Path, design: Design) -> dict:
    """Solder joints per board for the assembly estimate: SMD pads and plated through-hole pads of BOM parts."""
    import pcbnew

    board = pcbnew.LoadBoard(str(pcb_path))
    inbom = {p.ref for p in design.parts if p.in_bom and not p.board_only}
    smt = man = 0
    for fp in board.GetFootprints():
        if fp.GetReference() not in inbom:
            continue
        for pad in fp.Pads():
            a = pad.GetAttribute()
            if a == pcbnew.PAD_ATTRIB_SMD:
                smt += 1
            elif a == pcbnew.PAD_ATTRIB_PTH:
                man += 1
    return {"smt": smt, "manual": man}


def silk_report(pcb_path: Path, design: Design) -> dict:
    """Designators the build guide uses for this board, and whether each one is printed on the top silk (MAJ-16)."""
    texts = silk_audit.silk_texts(pcb_path)
    words = silk_audit.silk_words(texts)
    want = ["BP1", "U1"]
    for sid in design.used_slots:
        if sid != "U1":
            want.append(sid)
    phrases = ["LED 5V"] if "J_LED" in design.used_slots else []
    missing = [w for w in want if w not in words] + [ph for ph in phrases if not any(ph in t for t in texts)]
    return {"expected": want + phrases, "missing": missing, "texts": len(texts)}


def build_pcb(backplane_src, electrical: dict, pcb_dir: Path, meta: dict, options: Optional[dict] = None,
              progress: Optional[Callable[[str, Optional[float]], None]] = None,
              lib_footprints: Optional[dict] = None, modules: Optional[dict] = None) -> dict:
    options = dict(options or {})
    prog = progress or (lambda m, p=None: None)
    pcb_dir = Path(pcb_dir).resolve()
    key, reused = try_reuse(backplane_src, electrical, pcb_dir, meta, options, lib_footprints, prog)
    if reused is not None:
        return reused
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
    joints = count_joints(board["pcb"], design)
    pcba = sourcing.pcba_cost(bom["pcba_lines"], joints, design.design_id)
    (pcb_dir / "pcba.json").write_text(json.dumps(pcba, indent=2) + "\n")
    timings["fab"] = round(time.time() - t, 2)
    prog("Gerbers, drill, BOM, CPL and PCBA estimate written", 78)
    extra = {"bom": bom, "cpl": cpl, "pcba": pcba, "silk": silk_report(board["pcb"], design),
             "bp_raw": _raw_backplane(backplane_src), "modules": modules or {}}

    t = time.time()
    vis = visuals.make_all(board["pcb"], pcb_dir / f"{B.PROJECT}.kicad_sch", pcb_dir, design, options)
    timings["visuals"] = round(time.time() - t, 2)
    prog("renders, SVG plots and GLB written", 97)
    timings["total"] = round(time.time() - t0, 2)
    summary = summarize(design, pcb_dir, sch, board, drc, g, layers, bom, cpl, vis, timings, extra, key)
    if summary["routed"]:
        reuse.store(pcb_dir, key, options)
    if not summary["routed"]:
        raise StageError(f"Autorouting left {summary['unrouted']} connections unrouted (Freerouting "
                         f"{board['freerouting'].get('passes')} passes, retried); outputs kept in pcb/ for review",
                         {"unrouted": summary["unrouted"], "summary": str(pcb_dir / "pcb-summary.json"),
                          "drc": str(pcb_dir / "drc.json")})
    return summary


def extra_checks(design: Design, extra: dict) -> list[dict]:
    """REVIEW-1 checks: port keying (MAJ-3), amp shutdown at boot (MIN-1), orderable BOM/CPL (MAJ-22), silk
    designators (MAJ-16)."""
    out = [rules.port_keying_check(extra["bp_raw"])]
    if "A1" in design.used_slots:
        r7 = next((p for p in design.parts if p.ref == "R7"), None)
        amp_id = (next((s for s in extra["bp_raw"].get("slots", []) if s.get("id") == "A1"), {}) or {}).get("module")
        out.append(rules.amp_sd_boot_check(rules.parse_ohms(r7.value) if r7 else None,
                                           (extra.get("modules") or {}).get(amp_id)))
    bom, cpl = extra["bom"], extra["cpl"]
    out.append({"id": "pcb.bom_orderable", "title": "Every PCBA BOM line has a pinned maker part number and LCSC id",
                "kind": "digital", "status": "failed" if bom["unpinned"] else "passed",
                "detail": (f"{bom['lines'] - 1} part lines pinned (parts.json, read 2026-11-01); stock and price not read"
                           if not bom["unpinned"] else "no pinned part for " + ", ".join(bom["unpinned"])),
                "artifact": "pcb/bom.csv"})
    out.append({"id": "pcb.cpl_clean", "title": "Pick-and-place file lists only parts to place (no fiducials)",
                "kind": "digital", "status": "failed" if cpl.get("fiducial_rows") else "passed",
                "detail": f"{cpl['placements']} placements, {cpl.get('fiducial_rows', 0)} fiducial rows",
                "artifact": "pcb/cpl.csv"})
    sk = extra["silk"]
    out.append({"id": "pcb.silk_designators", "title": "Silkscreen prints every designator the build guide uses",
                "kind": "digital", "status": "failed" if sk["missing"] else "passed",
                "detail": ("missing: " + ", ".join(sk["missing"])) if sk["missing"]
                else "on the top silk: " + ", ".join(sk["expected"]),
                "artifact": "pcb/backplane.kicad_pcb"})
    return out


def summarize(design, pcb_dir, sch, board, drc, g, layers, bom, cpl, vis, timings, extra=None, key="") -> dict:
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
    art = {"pcb.erc": "pcb/erc.json", "pcb.netlist": "pcb/netlist.json", "pcb.drc": "pcb/drc.json",
           "pcb.routed": "pcb/backplane.kicad_pcb", "pcb.parity": "pcb/drc.json", "pcb.courtyards": "pcb/backplane.kicad_pcb",
           "pcb.gerbers": "pcb/backplane-gerbers.zip", "pcb.fabricated": "pcb/backplane-gerbers.zip"}
    for c in checks:
        c.setdefault("artifact", art.get(c["id"], "pcb/pcb-summary.json"))
    if extra:
        checks[-1:-1] = extra_checks(design, extra)
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
        "revision": design.revision,
        "pcb_key": key,
        "reused": None,
        "backplane": f"{design.backplane_id} {design.backplane_version}",
        "rules": board["rules"],
        "net_classes": {k: {"track_mm": v["track"], "clearance_mm": v["clearance"],
                            "route_clearance_mm": v.get("route_clearance", v["clearance"]),
                            "via_mm": [v["via_d"], v["via_drill"]], "nets": v["nets"]}
                        for k, v in board["classes"].items()},
        "freerouting": board["freerouting"],
        "deviations": design.deviations,
        "silk_skipped": board.get("silk_skipped", []),
        "envelope_models": board.get("envelopes", []),
        "thermal_fixes": board.get("thermal_fixes", 0),
        "splits": design.splits,
        "files": files,
        "renders": vis,
        "bom_lines": bom["lines"],
        "bom_unpinned": bom.get("unpinned", []),
        "cpl_placements": cpl["placements"],
        "pcba": _pcba_brief(extra["pcba"]) if extra else None,
        "silk": extra["silk"] if extra else None,
        "checks": checks,
        "timings_s": timings,
        "say": (f"Routed the {design.width:g} x {design.height:g} mm backplane: {drc['errors']} DRC errors, "
                f"{sch['erc']['errors']} ERC errors, {max(st['unrouted'], drc['unconnected'])} unrouted, "
                f"{st['vias']} vias, {st['track_length_mm']:.0f} mm of track."),
    }
    reuse.scrub_paths(pcb_dir)  # MIN-19: no build-machine paths in the delivered PCB files
    summary = reuse.scrub_obj(summary)
    summary["file_sha256"] = reuse.file_hashes(pcb_dir)
    (pcb_dir / "pcb-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _pcba_brief(pcba: dict) -> dict:
    """What the order stage needs to price BP1 as a populated board (details in pcb/pcba.json)."""
    b = pcba["batches"]
    return {"file": "pcb/pcba.json", "status": pcba["status"], "complete": pcba["complete"],
            "includes_usb_c_receptacle": pcba["includes_usb_c_receptacle"],
            "inr_per_board": {k: v["inr_per_board"] for k, v in b.items()},
            "usd_per_board": {k: v["usd_per_board"] for k, v in b.items()},
            "excludes": pcba["excludes"], "joints_per_board": pcba["joints_per_board"]}


__all__ = ["build_pcb"]
