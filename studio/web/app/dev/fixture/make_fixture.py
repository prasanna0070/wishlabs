"""DEV FIXTURE generator: one complete Gadget Studio build-version tree for the web frontend (never shipped).

Run on the VM (heavy compute never runs on the laptop):
    cd studio && tools/vm.sh sync && \
    tools/vm.sh run 'PYTHONPATH=engine /opt/gs/venv/bin/python web/app/dev/fixture/make_fixture.py'

Writes web/app/_out/devroot/ as a fake STUDIO_ROOT for the backend's fake builder:
    engine -> studio/engine, library -> studio/library, docs -> studio/docs   (absolute symlinks)
    examples/bedtime-robot/spec.json          the input spec (scripted fallback_spec, library ids filled)
    examples/bedtime-robot/_out/              one version tree per ARCHITECTURE §5

What is real and what is fixture:
  real engine   gstudio.normalize (spec.derived, cartridge placement), gstudio.electrical (electrical.json),
                gstudio.order.bom (purchased[], printed[]), gstudio.order.quote.make_quote (order/quote.json),
                gstudio.order.slicer (PrusaSlicer stats, when the slicer finishes in time), gstudio.pcb.stage (the
                backplane KiCad project, when it finishes in time), gstudio.checks cross-stage checks,
                gstudio.package delivery checks, manifest and zip, gstudio.docs_gen documents
  this script   CadQuery CAD (cad/gadget_cad.py), GLB, renders, wiring diagram, build steps, firmware files
                (NOT compiled: placeholder logs, sizes and binary), BUILD_GUIDE.md
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(STUDIO / "engine"))

import cadquery as cq  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

import fx_build  # noqa: E402
import fx_docs  # noqa: E402
import fx_layout as L  # noqa: E402
import fx_mesh as M  # noqa: E402
import fx_render as R  # noqa: E402
import gadget_cad as G  # noqa: E402
from fx_lib import IDS, load_backplane, load_modules  # noqa: E402

DEVROOT = STUDIO / "web" / "app" / "_out" / "devroot"
EX = DEVROOT / "examples" / "bedtime-robot"
OUT = EX / "_out"
WORK = DEVROOT / ".work"
T0 = time.time()
TIMES: dict = {}
LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.time() - T0:6.1f}s] {msg}"
    LOG.append(line)
    print(line, flush=True)


def wjson(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ============================================================================ component metadata
def component_meta(spec: dict) -> dict:
    inst = {i["ref"]: i for i in spec["derived"]["instances"]}

    def mod(ref):
        return (inst.get(ref) or {}).get("module")
    return {
        "PRINT_Shell": ("Shell (front and sides)", None, None, None),
        "PRINT_RearCover": ("Rear cover", None, None, None),
        "PRINT_SpeakerRing": ("Speaker ring", None, None, "speaker"),
        "PURCHASE_Backplane": ("Backplane (core cartridge)", "BP1", mod("BP1"), None),
        "PURCHASE_UsbC": ("USB-C power socket", "PWR1", mod("PWR1"), None),
        "PURCHASE_CoreBoard": ("ESP32-S3 core board", "U1", mod("U1"), None),
        "PURCHASE_Amp": ("I2S amplifier", "AMP1", mod("AMP1"), None),
        "PURCHASE_SdCard": ("microSD module", "SD1", mod("SD1"), None),
        "PURCHASE_Speaker": ("40 mm speaker", "SPK1", mod("SPK1"), "speaker"),
        "PURCHASE_Button": ("Story button", "BTN1", mod("BTN1"), "story_button"),
        "PURCHASE_Led": ("Night light LED", "LED1", mod("LED1"), "night_light"),
        "PURCHASE_Mic": ("Microphone", "MIC1", mod("MIC1"), "mic"),
        "PURCHASE_Feet": ("Bumper feet", None, IDS["feet"], None),
        "PURCHASE_CoverScrews": ("Rear cover screws", None, IDS["screws"], None),
        "PURCHASE_BoardScrews": ("Backplane screws", None, IDS["screws"], None),
        "PURCHASE_Inserts": ("Heat-set inserts", None, IDS["inserts"], None),
        "REFERENCE_CableButton": ("Cable: story button", None, None, "story_button"),
        "REFERENCE_CableMic": ("Cable: microphone", None, None, "mic"),
        "REFERENCE_CableLed": ("Cable: night light", None, None, "night_light"),
        "REFERENCE_CableSpeaker": ("Lead: speaker", None, None, "speaker"),
    }


def test_coupon(params: dict) -> cq.Shape:
    """TEST_ButtonFit: a 2.4 mm plate with the real button aperture (and the mic port) to check the fit first."""
    d = next(a["d"] for a in params["apertures"] if a["feature"] == "story_button")
    m = next(a["d"] for a in params["apertures"] if a["feature"] == "mic")
    plate = cq.Workplane("XY").box(48, 48, params["body"]["wall"], centered=(True, True, False)).edges("|Z").fillet(4)
    plate = plate.faces(">Z").workplane().hole(d).faces(">Z").workplane().center(17, 17).hole(max(m, 1.5))
    return plate.val()


# ============================================================================ main
def main() -> dict:
    from gstudio.electrical import build_electrical
    from gstudio.library import Library
    from gstudio.normalize import normalize
    from gstudio.version import ENGINE_VERSION

    # ---------------------------------------------------------------- tree + symlinks
    if OUT.exists():
        shutil.rmtree(OUT)
    if WORK.exists():
        shutil.rmtree(WORK)
    OUT.mkdir(parents=True)
    WORK.mkdir(parents=True)
    for name in ("engine", "library", "docs"):
        link = DEVROOT / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(STUDIO / name)
    created = now_iso()

    # ---------------------------------------------------------------- spec, normalize, electrical (real engine)
    lib = Library()
    mods = load_modules()
    bp_raw, bp_src = load_backplane()
    spec_in = L.make_spec()
    nspec, nrep = normalize(spec_in, lib)
    if not nrep.get("ok", True) or nrep.get("errors"):
        raise SystemExit(f"normalize rejected the fixture spec: {nrep.get('errors')}")
    spec = nspec.to_json_dict(True)
    electrical = build_electrical(nspec, lib)
    wjson(EX / "spec.json", spec_in)
    wjson(OUT / "spec.json", spec)
    wjson(OUT / "electrical.json", electrical)
    log(f"normalize + electrical: slots {electrical['slots_used']}")

    # ---------------------------------------------------------------- PCB stage in a subprocess (real, if it finishes)
    pcb_dir = OUT / "pcb"
    pcb_sum = WORK / "pcb-summary.json"
    env = dict(os.environ, PYTHONPATH=str(STUDIO / "engine"))
    pcb_log = open(WORK / "pcb.log", "w")
    pcb_proc = subprocess.Popen([sys.executable, str(HERE / "fx_pcb_run.py"), str(OUT / "electrical.json"), str(pcb_dir),
                                 str(pcb_sum), spec["name"], spec["slug"]], stdout=pcb_log, stderr=subprocess.STDOUT, env=env,
                                cwd=str(STUDIO))
    t_pcb = time.time()

    # ---------------------------------------------------------------- CAD
    board = L.Board(bp_raw, spec["derived"]["cartridge"]["board_origin"])
    seq, left, right = G.outline_sequence(spec["body"]["outline"])
    opoly = Polygon(G.wire_xz(G.outline_wire(seq, left, right, 0.0)))
    cover_screws = L.place_cover_screws(opoly, min(float(spec["body"]["edge_round"]), L.REAR_ROUND_MAX))
    params = L.make_params(spec, mods, board, electrical["slots_used"], cover_screws)
    cad = OUT / "cad"
    wjson(cad / "params.json", params)
    shutil.copyfile(HERE / "gadget_cad.py", cad / "gadget_cad.py")
    t = time.time()
    res = G.build(params)
    comps = res["components"]
    info = {k: v for k, v in res["info"].items() if k != "outline_poly"}
    TIMES["cad_build"] = round(time.time() - t, 1)
    log(f"CAD built: {len(comps)} solid components, {info}")

    # ---------------------------------------------------------------- print STLs, then slicing in the background
    pdir = OUT / "print"
    pdir.mkdir(parents=True)
    stl_paths = []
    for name in ("PRINT_Shell", "PRINT_RearCover", "PRINT_SpeakerRing"):
        p = pdir / f"{name}.stl"
        M.write_stl(G.print_pose(name, comps[name], params), p, 0.1, 0.2)
        stl_paths.append(p)
    coupon = test_coupon(params)
    M.write_stl(coupon, pdir / "TEST_ButtonFit.stl", 0.1, 0.2)
    stl_paths.append(pdir / "TEST_ButtonFit.stl")
    slice_result: dict = {}

    def do_slice():
        try:
            from gstudio.order import slicer
            ts = time.time()
            slice_result["parts"] = slicer.slice_all(stl_paths, WORK / "slice", slicer.INI, timeout=150)
            slice_result["seconds"] = round(time.time() - ts, 1)
            exe = slicer.find_slicer()
            slice_result["slicer"] = slicer.slicer_version(exe) if exe else None
        except Exception as exc:  # noqa: BLE001
            slice_result["error"] = f"{type(exc).__name__}: {exc}"
    th_slice = threading.Thread(target=do_slice, daemon=True)
    th_slice.start()
    log("STLs written, slicing started")

    # ---------------------------------------------------------------- STEP, BREP
    t = time.time()
    assy = cq.Assembly(name=spec["slug"])
    for name, shp in comps.items():
        rgb = M.hex_rgb(params["colors"].get(name, "#CCCCCC"))
        assy.add(shp, name=name, color=cq.Color(*rgb))
    assy.save(str(cad / "assembly.step"))
    (cad / "components").mkdir()
    for name, shp in comps.items():
        shp.exportBrep(str(cad / "components" / f"{name}.brep"))
    TIMES["step_brep"] = round(time.time() - t, 1)
    log("assembly.step + BREPs written")

    # ---------------------------------------------------------------- meshes, cables, components.json, GLB
    t = time.time()
    anchors = L.make_anchors(params, board, spec)
    harness = {h["ref"]: h for h in spec["derived"]["harness"]}
    routes = L.make_routes(anchors, harness)
    meshes: dict = {}
    for name, shp in comps.items():
        meshes[name] = M.mesh_shape(shp, 0.1 if name.startswith("PRINT_") else 0.15, 0.2)
    for r in routes:
        meshes[r["id"]] = M.tube_mesh(r["points"], r["diameter"])
    meta = component_meta(spec)
    components = {}
    for name in list(comps) + [r["id"] for r in routes]:
        label, ref, mid, fid = meta[name]
        kind = name.split("_", 1)[0]
        V = meshes[name][0]
        if name in comps:
            shp = comps[name]
            bb = shp.BoundingBox()
            bbox = [bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax]
            vol, valid, solids, step = shp.Volume(), bool(shp.isValid()), len(shp.Solids()), "assembly"
        else:
            r = next(x for x in routes if x["id"] == name)
            bbox = L.tube_bbox(r["points"], r["diameter"])
            vol, valid, solids, step = math.pi * (r["diameter"] / 2) ** 2 * r["route_length_mm"], True, 0, None
        c = {"kind": kind, "label": label, "ref": ref, "module_id": mid, "feature_id": fid,
             "color": params["colors"].get(name, "#CCCCCC"), "bbox": [round(float(v), 3) for v in bbox],
             "volume_mm3": round(float(vol), 1), "valid": valid, "solids": solids, "step": step,
             "brep": f"cad/components/{name}.brep" if name in comps else None, "triangles": int(len(meshes[name][2]))}
        if name.startswith("REFERENCE_Cable"):
            r = next(x for x in routes if x["id"] == name)
            c.update({"harness": harness.get(r["ref"], {}).get("id"), "route": r["id"], "display_only": True})
        if name in params["print_pose"]:
            c["orientation_note"] = params["print_pose"][name]["note"]
        components[name] = c
    wjson(cad / "components.json", components)
    wjson(cad / "anchors.json", anchors)
    wjson(cad / "cable-routes.json", routes)
    glb_meshes = [{"name": n, "V": meshes[n][0], "N": meshes[n][1], "F": meshes[n][2], "color": components[n]["color"],
                   "roughness": 0.35 if n.startswith(("PURCHASE_CoverScrews", "PURCHASE_BoardScrews", "PURCHASE_Inserts", "PURCHASE_UsbC")) else 0.6,
                   "extras": {k: components[n][k] for k in ("kind", "label", "ref", "module_id", "feature_id")}}
                  for n in components]
    glb = M.write_glb(cad / "assembly.glb", glb_meshes, "Gadget Studio dev fixture (web/app/dev/fixture)",
                      {"fixture": True, "units": "metres, +Y up (gadget mm (x,y,z) -> (x/1000, z/1000, -y/1000))"})
    TIMES["mesh_glb"] = round(time.time() - t, 1)
    log(f"GLB written: {glb['bytes'] / 1e6:.1f} MB, bbox {glb['bbox_m']}")

    # ---------------------------------------------------------------- renders
    t = time.time()
    mdir = OUT / "manual"
    mdir.mkdir()
    steps = fx_build.steps()
    scene = R.Scene({n: meshes[n] for n in components}, {n: components[n]["color"] for n in components})
    scene.shot(mdir / "assembled.png", fx_build.CAM["front"], highlight=None, hide=fx_build.CABLES)
    for k, s in enumerate(steps, 1):
        hide = () if any(h.startswith("REFERENCE_") for h in s["highlight"]) else tuple(fx_build.CABLES)
        scene.shot(mdir / f"step-{k:02d}.png", s["camera"], highlight=set(s["highlight"]), hide=hide)
    offsets = {"PRINT_RearCover": (0, 200, 0), "PURCHASE_CoverScrews": (0, 245, 0), "PURCHASE_Button": (0, -55, 0),
               "PURCHASE_Speaker": (0, 30, 0), "PRINT_SpeakerRing": (0, 18, 0), "PURCHASE_Led": (0, 22, 0),
               "PURCHASE_Mic": (0, 22, 0), "PURCHASE_Feet": (0, 0, -30),
               "PURCHASE_Backplane": (0, 40, 200), "PURCHASE_UsbC": (0, 40, 200), "PURCHASE_BoardScrews": (0, 40, 232),
               "PURCHASE_CoreBoard": (0, 40, 222), "PURCHASE_Amp": (0, 40, 222), "PURCHASE_SdCard": (0, 40, 222)}
    scene.shot(mdir / "exploded.png", fx_build.CAM["exploded"], highlight=None, offsets=offsets, hide=fx_build.CABLES,
               view_angle=34.0)
    scene.close()
    R.wiring_diagram(mdir / "wiring.png", mdir / "wiring.svg", electrical, f"{spec['name']}: wiring")
    TIMES["renders"] = round(time.time() - t, 1)
    log("renders written")

    # ---------------------------------------------------------------- firmware (fixture)
    fw_block = fx_docs.write_firmware(OUT / "firmware", electrical["gpio"])
    log("firmware fixture files written")

    # ---------------------------------------------------------------- slicing result
    th_slice.join(timeout=max(5.0, 150 - (time.time() - T0)))
    from gstudio.order import bom, slicer
    from gstudio.order import assembly_bbox
    from gstudio.order.quote import make_quote
    prof = slicer.read_profile(slicer.INI)
    parts_stats = slice_result.get("parts") or {}
    real_slice = bool(parts_stats) and all(v.get("ok") for k, v in parts_stats.items() if k.startswith("PRINT_"))
    if not real_slice:
        parts_stats = {}
        for p in stl_paths:
            info_s = slicer.stl_info(p)
            vol = info_s["volume_mm3"] / 1000
            g_ = round(vol * 1.27 * (0.78 if p.stem.startswith("PRINT_Shell") else 0.9), 2)
            parts_stats[p.stem] = {"file": p.name, "ok": True, "size_mm": info_s["size_mm"], "model_volume_cm3": round(vol, 2),
                                   "on_bed": abs(info_s["bounds_min"][2]) <= 0.05, "fits_bed": True, "rotate_z_deg": 0,
                                   "filament_g": g_, "print_h": round(g_ / 11.0, 3), "print_time": None,
                                   "supports": p.stem == "PRINT_Shell", "support_g": round(g_ * 0.06, 2) if p.stem == "PRINT_Shell" else 0.0,
                                   "estimate": "DEV FIXTURE estimate from the STL volume (slicer did not finish)"}
        log(f"slicing fallback to estimates ({slice_result.get('error') or 'timeout'})")
    else:
        ev = pdir / "slice"
        ev.mkdir(exist_ok=True)
        for stem, st in parts_stats.items():
            src = WORK / "slice" / f"{stem}.slice.txt"
            if src.exists():
                shutil.copyfile(src, ev / src.name)
            st["evidence"] = f"print/slice/{stem}.slice.txt" if src.exists() else None
        shutil.copyfile(slicer.INI, pdir / "slice.ini")
        log(f"sliced with {slice_result.get('slicer')} in {slice_result.get('seconds')} s")
    bed = list(slicer.bed_from_profile(prof))
    prints = {k: v for k, v in parts_stats.items() if k.startswith("PRINT_")}
    slice_stats = {
        "schema": "gadget-slice-stats/1", "slicer": slice_result.get("slicer") if real_slice else None,
        "profile": slicer.profile_summary(prof), "profile_file": "print/slice.ini" if real_slice else None,
        "printer": {"name": f"generic FDM, {prof.get('nozzle_diameter')} mm nozzle", "build_volume_mm": bed},
        "parts": parts_stats,
        "totals_print_parts": {"parts": len(prints), "sliced": len(prints),
                               "filament_g": round(sum(v["filament_g"] for v in prints.values()), 2),
                               "print_h": round(sum(v["print_h"] for v in prints.values()), 3),
                               "support_g": round(sum(v.get("support_g", 0) for v in prints.values()), 2)},
        "limits": ["Slicer estimates for a generic Prusa-class profile, not a measured print.",
                   "The slicer does not report wall thickness; it is not checked here."],
        "fixture": True,
        "source": ("PrusaSlicer run on these STLs by the dev fixture generator" if real_slice
                   else "DEV FIXTURE estimate from STL volume; no slicer run"),
    }
    wjson(pdir / "slice-stats.json", slice_stats)

    # ---------------------------------------------------------------- purchased, printed, quote (real engine)
    purchased, problems = bom.build_purchased(spec, lib, components)
    if not any(r.get("requiresSoldering") for r in purchased):
        for r in purchased:
            if r.get("module_id") == IDS["speaker"]:
                r["requiresSoldering"] = True
                r["requiresSoldering_basis"] = ("library module_connector: 'solder eyelets on the basket; solder 2 leads' "
                                                "(set by the dev fixture; the bom rule had no explicit field)")
    orient = {"print_orientation": {k: {"orientation_note": v["note"]} for k, v in params["print_pose"].items()}}
    printed = bom.build_printed(parts_stats, components, spec, prof.get("filament_type", "PETG"), orient)
    bbox_mm = assembly_bbox(components)
    prints_ok = {k: v for k, v in parts_stats.items() if k.startswith("PRINT_") and v.get("ok")}
    t = time.time()
    quote = make_quote(purchased, prints_ok, spec, lib, bbox_mm=bbox_mm, draws=4000)
    TIMES["quote"] = round(time.time() - t, 1)
    wjson(OUT / "order" / "quote.json", quote)
    order_block = {"list_price_inr": quote["delivered_price_inr"]["pilot_one_off"],
                   "steady_state_price_inr": quote["delivered_price_inr"]["steady_state"],
                   "status": quote["status"], "lead_time_days": [quote["lead_time_days"]["low"], quote["lead_time_days"]["high"]],
                   "quote": "order/quote.json"}
    log(f"parts list {len(purchased)} rows, quote {quote['status']} one-off INR {order_block['list_price_inr']}")

    # ---------------------------------------------------------------- PCB result (real stage, else fixture)
    remaining = max(5.0, 170 - (time.time() - t_pcb))
    try:
        pcb_proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        pcb_proc.kill()
    pcb_log.close()
    pcb_summary = json.loads(pcb_sum.read_text()) if pcb_sum.exists() and pcb_proc.returncode == 0 else None
    pcb_checks = []
    if pcb_summary:
        pcb_block = {k: pcb_summary.get(k) for k in ("layers", "size_mm", "drc_errors", "erc_errors", "routed", "unrouted", "files")}
        pcb_block.update({k: pcb_summary.get(k) for k in ("drc_warnings", "erc_warnings", "track_length_mm", "vias", "parity_issues")})
        pcb_block["source"] = "gstudio.pcb.stage run by the dev fixture generator on this electrical.json"
        pcb_checks = pcb_summary.get("checks") or []
        log(f"PCB stage done in {pcb_summary.get('fixture_seconds')} s: DRC {pcb_block['drc_errors']} errors, routed {pcb_block['routed']}")
    else:
        tail = (WORK / "pcb.log").read_text(errors="ignore")[-600:]
        log(f"PCB stage unavailable, writing fixture PCB files. Log tail: {tail!r}")
        pcb_block = fixture_pcb(pcb_dir, bp_raw, electrical)
    pcb_block["files"] = sorted(str(p.relative_to(OUT)) for p in pcb_dir.rglob("*") if p.is_file()
                                and p.suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".zip", ".csv", ".pdf", ".svg", ".png", ".glb", ".json"))

    # ---------------------------------------------------------------- build.json
    build = {"schema": "gadget-build/1", "fixture": True, "purchased": purchased, "printed": printed, "steps": steps,
             "wiring": fx_build.wiring_block(electrical, purchased, routes), "tests": fx_build.physical_tests(),
             "firmware": fw_block, "pcb": pcb_block, "order": order_block}
    wjson(OUT / "build.json", build)
    size_mm = bbox_mm
    (OUT / "BUILD_GUIDE.md").write_text(fx_docs.build_guide(spec, build, size_mm, quote))

    # ---------------------------------------------------------------- project.json
    spec_hash = "sha256:" + __import__("hashlib").sha256(json.dumps(spec_in, sort_keys=True).encode()).hexdigest()
    brief_parts = [{"module_id": i["module"], "name": i.get("name"), "role": i.get("role"), "qty": i.get("qty", 1),
                    "implied": i.get("source") == "implied"} for i in spec["derived"]["instances"]]
    project = {"schema": "gadget-project/1", "name": spec["name"], "slug": spec["slug"], "version": 1, "created": created,
               "engine_version": ENGINE_VERSION, "brief": fx_build.brief(spec, size_mm, brief_parts), "brief_version": 1,
               "spec_hash": spec_hash, "fixture": True,
               "fixture_note": "DEV FIXTURE: generated by web/app/dev/fixture/make_fixture.py for frontend development. "
                               "Never shipped. Firmware compile/test results are placeholders.",
               "fixture_changes": L.SPEC_CHANGES, "backplane_source": bp_src}
    wjson(OUT / "project.json", project)

    # ---------------------------------------------------------------- digital checks (real engine joins + this run's checks)
    from gstudio import checks as C
    from gstudio import package as P
    stage_checks = [C.check("cad-valid", "every CAD component is a valid solid",
                            all(components[n]["valid"] for n in comps), f"{len(comps)} solids valid", stage="cad",
                            artifact="cad/components.json"),
                    C.check("cad-edge-round", "front and rear perimeter rounds built as requested",
                            "passed" if not info["warnings"] else "warning",
                            f"front {info['edge_round_front']} mm, rear {info['edge_round_rear']} mm", stage="cad")]
    for stem, st in parts_stats.items():
        stage_checks.append(C.check(f"bed-fit:{stem}", f"{stem} fits the {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm build volume",
                                    bool(st.get("fits_bed")), f"size {st.get('size_mm')} mm", qb="QB-10", stage="order"))
        stage_checks.append(C.check(f"sliced:{stem}", f"{stem} slices", bool(st.get("ok")),
                                    f"{st.get('filament_g')} g, supports {'yes' if st.get('supports') else 'no'}"
                                    + ("" if real_slice else " (fixture estimate, slicer not run)"), qb="QB-11", stage="order"))
    tiers = {tt: sum(1 for r in purchased if r["evidence_tier"] == tt) for tt in ("seen", "estimate", "not_confirmed")}
    stage_checks.append(C.check("price-evidence", "every price is a dated observation or labelled an estimate", True,
                                f"{tiers['seen']} seen, {tiers['estimate']} estimate, {tiers['not_confirmed']} not confirmed",
                                qb="QB-23", stage="order"))
    stage_checks.append(C.check("quote", "delivered price computed", "passed" if quote["complete"] else "warning",
                                f"pilot one-off INR {quote['delivered_price_inr']['pilot_one_off']:,}; status {quote['status']}"
                                + ("" if quote["complete"] else f"; missing prices: {quote['missing_prices']}"), qb="QB-24", stage="order"))
    records = {"pcb": {"state": "done", "summary": {"checks": pcb_checks}}} if pcb_checks else {}
    all_checks = stage_checks + C.normalize_checks(pcb_checks, "pcb") + C.file_checks(OUT, records) \
        + C.cross_checks(OUT, spec, build, electrical)
    for c in all_checks:
        if c["stage"] == "firmware":
            c["detail"] += " (DEV FIXTURE placeholder files)"
        if c["id"] == "fw-tests" and c["status"] == "not_run" and re.match(r"(\d+)/\1 passed", c["detail"]):
            c["status"] = "passed"
            c["detail"] += ("; status corrected by the fixture: gstudio.checks.file_checks passes an int, which "
                            "norm_status reads as not_run")
    build["tests"] = fx_build.physical_tests() + C.digital_tests(all_checks)
    wjson(OUT / "build.json", build)
    counts = C.summarize(all_checks)
    digital = {"schema": "gadget-digital-checks/1", "fixture": True, "summary": counts,
               "all_passed": counts["failed"] == 0 and counts["not_run"] == 0,
               "stages": {}, "checks": all_checks, "limits": C.LIMITS + ["DEV FIXTURE: firmware compile/test files are placeholders."]}
    wjson(OUT / "checks" / "digital-checks.json", digital)
    log(f"digital checks: {counts}")

    # ---------------------------------------------------------------- PROJECT.md, PARTS_EVIDENCE.md, SOFTWARE_SETUP.md
    recs = {sid: {"state": "done", "message": msg, "summary": {}} for sid, msg in (
        ("normalize", "spec normalized"), ("electrical", f"{len(electrical['instances'])} instances"),
        ("cad", f"{len(components)} components (dev fixture CAD)"), ("pcb", "backplane PCB" + ("" if pcb_summary else " (fixture)")),
        ("firmware", "DEV FIXTURE: not compiled"), ("order", f"quote {quote['status']}"), ("manual", "10 steps (dev fixture renders)"),
        ("checks", f"{counts['passed']} passed"), ("docs", "documents"), ("package", "export"))}
    recs["firmware"]["summary"] = {"software_setup_md": software_md(electrical)}
    try:
        from gstudio.docs_gen import parts as dparts
        from gstudio.docs_gen import project as dproject
        from gstudio.docs_gen import software as dsoftware
        from gstudio.docs_gen.util import Bundle
        b = Bundle(OUT, spec, recs)
        docs = {"PROJECT.md": dproject.render(b), "PARTS_EVIDENCE.md": dparts.render(b, lib), "SOFTWARE_SETUP.md": dsoftware.render(b)}
    except Exception as exc:  # noqa: BLE001
        log(f"docs_gen unavailable ({type(exc).__name__}: {exc}); writing short documents")
        docs = {"PROJECT.md": fx_docs.fallback_doc(spec["name"], spec["tagline"]),
                "PARTS_EVIDENCE.md": fx_docs.fallback_doc("Parts evidence", "\n".join(f"- {r['name']}: {r['evidence_tier']}" for r in purchased)),
                "SOFTWARE_SETUP.md": fx_docs.fallback_doc("Software setup", software_md(electrical))}
    for name, text in docs.items():
        lines = text.splitlines()
        head = lines[0] if lines and lines[0].startswith("# ") else f"# {name[:-3]}"
        rest = lines[1:] if lines and lines[0].startswith("# ") else lines
        if name == "PROJECT.md":
            head = f"# DEV FIXTURE: {spec['name']}"
        (OUT / name).write_text("\n".join([head, "", fx_docs.BANNER] + rest) + "\n")

    # ---------------------------------------------------------------- delivery checks, status, manifest, zip (real package code)
    wjson(OUT / "checks" / "delivery-checks.json", dict(P.delivery_checks(OUT, build, digital), fixture=True))
    stages = [{"id": i, "label": lab, "state": "done", "started": created, "ended": now_iso(), "message": msg}
              for i, lab, msg in (("electrical", "Wiring and pins", "real gstudio.electrical"),
                                  ("cad", "Shell and CAD", "dev fixture CAD"),
                                  ("pcb", "Backplane PCB", "real gstudio.pcb" if pcb_summary else "fixture placeholder"),
                                  ("firmware", "Firmware", "DEV FIXTURE placeholder, not compiled"),
                                  ("order", "Print slicing and price", "real quote engine" + (", PrusaSlicer" if real_slice else "")),
                                  ("manual", "Build manual", "dev fixture renders"),
                                  ("checks", "Digital checks", f"{counts['passed']} passed, {counts['warning']} warnings, {counts['failed']} failed"),
                                  ("package", "Export", "manifest + export.zip"))]
    wjson(OUT / "status.json", {"state": "done", "percent": 100, "stages": stages, "error": None, "fixture": True})
    files = P.delivered_files(OUT)
    manifest = P.make_manifest(OUT, files, now_iso())
    manifest["fixture"] = True
    wjson(OUT / "manifest.json", manifest)
    zbytes = P.make_zip(OUT, spec["slug"], files, OUT / "export.zip")
    problems_pkg = P.verify_manifest(OUT, manifest) + P.verify_zip(OUT / "export.zip", spec["slug"], manifest)
    log(f"manifest {len(files)} files, export.zip {zbytes / 1e6:.1f} MB, problems {problems_pkg}")

    report = {"seconds": round(time.time() - T0, 1), "times": TIMES, "glb": glb, "nodes": list(components),
              "quote": {"status": quote["status"], "pilot_one_off": quote["delivered_price_inr"]["pilot_one_off"],
                        "steady_state": quote["delivered_price_inr"]["steady_state"], "missing": quote["missing_prices"],
                        "lead_time_days": order_block["lead_time_days"]},
              "checks": counts, "pcb": {k: pcb_block.get(k) for k in ("size_mm", "drc_errors", "erc_errors", "routed", "unrouted", "source")},
              "real_slice": real_slice, "slice_totals": slice_stats["totals_print_parts"], "bom_problems": problems,
              "tiers": tiers, "package_problems": problems_pkg, "files": len(files), "log": LOG}
    wjson(DEVROOT / "fixture-report.json", report)
    shutil.rmtree(WORK, ignore_errors=True)
    return report


def software_md(electrical: dict) -> str:
    rows = "\n".join(f"| `{k}` | GPIO{v} |" for k, v in sorted(electrical["gpio"].items()))
    return ("DEV FIXTURE firmware: `firmware/` holds a PlatformIO project for `esp32-s3-devkitc-1` (Arduino). It was not "
            "compiled in this fixture.\n\n1. Install PlatformIO.\n2. `pio run -d firmware` builds; `pio run -d firmware -t upload` "
            "flashes over the rear USB-C.\n3. `pio test -d firmware -e native` runs the unit tests.\n4. Copy `firmware/sdcard/` to "
            "a microSD card.\n\n| Signal | Pin |\n|---|---|\n" + rows + "\n")


def fixture_pcb(pcb_dir: Path, bp: dict, electrical: dict) -> dict:
    """Fallback when the PCB stage is unavailable: labelled placeholders, matplotlib board drawings."""
    import csv
    import zipfile
    shutil.rmtree(pcb_dir, ignore_errors=True)
    pcb_dir.mkdir(parents=True, exist_ok=True)
    used = electrical["slots_used"]
    R.board_drawing(pcb_dir / "board-top.png", bp, used, "top")
    R.board_drawing(pcb_dir / "board-bottom.png", bp, used, "bottom")
    note = "DEV FIXTURE placeholder: not a KiCad run"
    for f in ("drc.json", "erc.json"):
        wjson(pcb_dir / f, {"gstudio_fixture": note, "violations": [], "unconnected_items": [], "schematic_parity": []})
    wjson(pcb_dir / "netlist.json", {"fixture": True, "nets": electrical["nets"]})
    for ext in ("kicad_pro", "kicad_sch", "kicad_pcb"):
        (pcb_dir / f"backplane.{ext}").write_text(f"# {note}\n")
    with open(pcb_dir / "bom.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Reference", "Value", "Footprint"])
        for p in bp["parts"]:
            if not p.get("slot") or p["slot"] in used:
                w.writerow([p["ref"], p.get("value", ""), p.get("footprint", "")])
    with open(pcb_dir / "cpl.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Rotation", "Layer"])
        for p in bp["parts"]:
            if not p.get("slot") or p["slot"] in used:
                w.writerow([p["ref"], p["at"][0], p["at"][1], p.get("rotation_deg", 0), p.get("side", "top")])
    with zipfile.ZipFile(pcb_dir / "backplane-gerbers.zip", "w") as z:
        z.writestr("README.txt", note + "\n")
    (pcb_dir / "schematic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80"><text x="10" y="40">'
                                           f"{note}</text></svg>")
    W, H = bp["board"]["size"]
    V = np.array([[x, y, z] for z in (0, 1.6) for y in (0, H) for x in (0, W)], float) - [W / 2, H / 2, 0]
    F = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6],
                  [1, 3, 5], [3, 7, 5]])
    N = V - V.mean(axis=0)
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    M.write_glb(pcb_dir / "board.glb", [{"name": "BOARD", "V": V, "N": N, "F": F, "color": "#1F6B45"}], "dev fixture board placeholder")
    return {"layers": 2, "size_mm": [W, H], "drc_errors": 0, "erc_errors": 0, "routed": True, "unrouted": 0, "fixture": True,
            "source": note}


if __name__ == "__main__":
    rep = main()
    print(json.dumps({k: v for k, v in rep.items() if k != "log"}, indent=1, default=str))
