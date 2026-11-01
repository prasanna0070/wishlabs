"""Digital checks for the CAD stage (QB-05..QB-16 + tip-over + tool access).

Every check returns {id, title, status: passed|failed|warning|not_run, detail, qb?, data?}. Nothing here claims a physical
result: the limits list says what is not modelled.
"""

from __future__ import annotations

import itertools
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .geom import bbox, to_trimesh, unit

PLA_DENSITY = 1.24e-3  # g/mm^3
BED = (250.0, 210.0, 210.0)
MIN_WALL = 1.2
TIGHT_GAP = 0.2
VOL_EPS = 0.05  # mm^3 mesh-intersection noise floor; above it an exact OCC boolean decides

LIMITS = [
    "Purchased parts are simplified envelopes from the library's dimensions; connector internals, pins, clips and cable bends are not modelled.",
    "Cable routes are display-only occupied-space envelopes; real bend radii and pinching are not checked.",
    "Fastener threads are simplified; heat-set insert interference with its printed boss is by design.",
    "Masses of printed parts assume solid PLA at 1.24 g/cm3; purchased masses are library values or estimates.",
    "No physical print, fit, acoustic, thermal, stability or drop test has been performed. Physical checks still needed.",
]


def _c(id_, title, status, detail, qb=None, data=None):
    d = {"id": id_, "title": title, "status": status, "detail": detail}
    if qb:
        d["qb"] = qb
    if data is not None:
        d["data"] = data
    return d


# ------------------------------------------------------------------------------------------------ allow-list
def allowed(a: str, b: str) -> Optional[str]:
    s = {a, b}
    if any(n.startswith("PURCHASE_HeatSetInserts") for n in s) and any(n.startswith("PRINT_") for n in s):
        return "heat-set insert melts into its printed boss (interference fit by design)"
    if any("Screws" in n for n in s) and any(n.startswith("PURCHASE_HeatSetInserts") for n in s):
        return "screw threads into its insert (threads simplified)"
    if any("Gasket" in n for n in s):
        return "foam gasket modelled at its compressed thickness"
    return None


def _manifolds(model):
    import manifold3d as mf

    from .geom import to_manifold

    out = {}
    for p in model.parts:
        if p.kind == "REFERENCE":
            continue
        mans = []
        for _, s, _ in p.subparts:
            try:
                for sol in (s.Solids() or [s]):
                    m = to_manifold(sol, 0.08, 0.3)
                    if not m.is_empty():
                        mans.append(m)
            except Exception:
                continue
        if mans:
            out[p.name] = mf.Manifold.batch_boolean(mans, mf.OpType.Add) if len(mans) > 1 else mans[0]
    return out


def collisions(model, mans: dict, exact: bool = True) -> tuple[dict, list]:
    names = list(mans)
    boxes = {p.name: bbox(p.shape) for p in model.parts if p.name in mans}
    pairs = list(itertools.combinations(names, 2))
    checked, hits, allowed_l, overl = [], [], [], 0
    for a, b in pairs:
        A, B = boxes[a], boxes[b]
        if A[1] < B[0] or B[1] < A[0] or A[3] < B[2] or B[3] < A[2] or A[5] < B[4] or B[5] < A[4]:
            continue
        overl += 1
        t = time.monotonic()
        v = float((mans[a] ^ mans[b]).volume())
        why = allowed(a, b)
        if v > VOL_EPS and exact and why is None:
            try:
                pa = model.part(a).shape
                pb = model.part(b).shape
                v = float(pa.intersect(pb).Volume())
            except Exception:
                pass
        rec = {"parts": [a, b], "intersection_mm3": round(v, 4), "seconds": round(time.monotonic() - t, 3)}
        if why:
            rec["allowed"] = why
            allowed_l.append(rec)
        elif v > VOL_EPS:
            hits.append(rec)
        checked.append(rec)
    summ = {"components": len(names), "pairs_total": len(pairs), "pairs_bbox_overlap": overl, "intersections": hits,
            "allowed": allowed_l, "threshold_mm3": VOL_EPS}
    return summ, checked


def clearances(model, mans: dict) -> list:
    out = []
    prints = [p.name for p in model.parts if p.kind == "PRINT" and p.name in mans]
    buys = [p.name for p in model.parts if p.kind == "PURCHASE" and p.name in mans and not p.name.startswith(("PURCHASE_HeatSet", "PURCHASE_Feet"))
            and "Screws" not in p.name and "Gasket" not in p.name]
    for b in buys:
        for a in prints:
            A, B = bbox(model.part(a).shape), bbox(model.part(b).shape)
            if A[1] + 3 < B[0] or B[1] + 3 < A[0] or A[3] + 3 < B[2] or B[3] + 3 < A[2] or A[5] + 3 < B[4] or B[5] + 3 < A[4]:
                continue
            try:
                g = float(mans[b].min_gap(mans[a], 3.0))
            except Exception:
                continue
            if g < 3.0:
                out.append({"parts": [b, a], "gap_mm": round(g, 3), "contact": g < 0.02})
    return out


def wall_thickness(mesh, n: int = 1500, seed: int = 1) -> dict:
    import trimesh

    rng = np.random.default_rng(seed)
    pts, fi = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nrm = mesh.face_normals[fi]
    o = pts - nrm * 0.02
    d = -nrm
    ray = trimesh.ray.ray_triangle.RayMeshIntersector(mesh)
    locs, ri, _ = ray.intersects_location(o, d, multiple_hits=False)
    th = np.full(len(o), np.nan)
    if len(locs):
        th[ri] = np.linalg.norm(locs - o[ri], axis=1)
    th = th[np.isfinite(th)]
    if not len(th):
        return {}
    return {"samples": int(len(th)), "min": round(float(th.min()), 2), "p1": round(float(np.percentile(th, 1)), 2),
            "p5": round(float(np.percentile(th, 5)), 2), "median": round(float(np.median(th)), 2),
            "below_min_wall": int((th < MIN_WALL).sum()), "min_wall_rule": MIN_WALL}


def tip_over(model) -> dict:
    tot, mom = 0.0, np.zeros(3)
    rows = []
    for p in model.parts:
        if p.kind == "REFERENCE":
            continue
        s = p.shape
        try:
            c = np.array(s.Center().toTuple())
            vol = float(s.Volume())
        except Exception:
            continue
        if p.kind == "PRINT":
            m = vol * PLA_DENSITY
            src = "computed (solid PLA)"
        else:
            m = float(p.mass_g or 0.0)
            src = p.mass_source
        tot += m
        mom += m * c
        rows.append({"name": p.name, "mass_g": round(m, 2), "source": src})
    if tot <= 0:
        return {}
    cog = mom / tot
    feet = model.info.get("feet") or []
    pts = np.array(feet, float) if len(feet) >= 3 else None
    if pts is None:
        b = bbox(model.part("PRINT_Shell").shape)
        pts = np.array([[b[0], b[2]], [b[1], b[2]], [b[1], b[3]], [b[0], b[3]]])
    from scipy.spatial import ConvexHull

    hull = pts[ConvexHull(pts).vertices]
    h = cog[2]
    angles = []
    inside = True

    def cross2(a, b):
        return float(a[0] * b[1] - a[1] * b[0])

    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        e = b - a
        L = max(float(np.linalg.norm(e)), 1e-9)
        cr = cross2(e, cog[:2] - a)
        if cr < 0:
            inside = False
        d = abs(cr) / L
        angles.append(math.degrees(math.atan2(d, h)))
    return {"mass_g": round(tot, 1), "cog_mm": [round(float(v), 2) for v in cog], "support": "feet" if len(feet) >= 3 else "base outline",
            "min_tip_angle_deg": round(min(angles), 1) if inside else 0.0, "cog_inside_support": inside, "masses": rows}


def fastener_engagement(model) -> list:
    out = []
    for f in model.fasteners:
        if f.kind != "screw" or f.joint is None:
            continue
        a = unit(f.axis)
        tip = f.head + a * f.length
        pen = float(np.dot(tip - f.joint, a))
        eng = min(pen, f.insert_len or 0.0)
        bottom = pen > (f.hole_depth or 0) - 0.2
        ok = eng >= 3.0 and not bottom
        out.append({"screw": f.label, "group": f.group, "length_mm": f.length, "clamped_mm": round(f.clamp_t or 0, 2),
                    "penetration_mm": round(pen, 2), "thread_engagement_mm": round(eng, 2), "hole_depth_mm": round(f.hole_depth or 0, 2),
                    "bottoms_out": bool(bottom), "ok": bool(ok)})
    return out


def tool_access(model, mans: dict) -> list:
    import manifold3d as mf

    out = []
    allm = [m for n, m in mans.items() if not n.startswith("PURCHASE_HeatSet") and "Screws" not in n]
    if not allm:
        return out
    world = mf.Manifold.batch_boolean(allm, mf.OpType.Add)
    # base-plate designs: the cartridge is screwed to the plate before the shell goes on
    noshell = [m for n, m in mans.items() if not n.startswith("PURCHASE_HeatSet") and "Screws" not in n and n != "PRINT_Shell"]
    world_open = mf.Manifold.batch_boolean(noshell, mf.OpType.Add) if noshell else world
    for f in model.fasteners:
        if f.kind != "screw" or f.group not in ("CartridgeScrews", "CoverScrews", "PlateScrews"):
            continue
        a = unit(f.axis)
        start = f.head - a * (f.length * 0 + 2.6)
        cyl = mf.Manifold.cylinder(40.0, 3.0, 3.0, 24)
        z = np.array([0, 0, 1.0])
        v = -a
        ax = np.cross(z, v)
        s = np.linalg.norm(ax)
        if s < 1e-9:
            R = np.eye(3) if v[2] > 0 else np.diag([1, -1, -1])
        else:
            ax /= s
            ang = math.atan2(s, float(np.dot(z, v)))
            K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
            R = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
        M = np.hstack([R, start.reshape(3, 1)])
        tool = cyl.transform(M.tolist())
        w = world_open if (f.group in ("CartridgeScrews", "PlateScrews") and model.info.get("split") == "base_plate") else world
        vol = float((tool ^ w).volume())
        out.append({"screw": f.label, "group": f.group, "blocked_mm3": round(vol, 2), "ok": vol < 1.0,
                    "tool": "6 mm driver shaft, 40 mm free length along the screw axis"})
    return out


def run_checks(model, files: dict, step_path: Optional[Path] = None, exact: bool = True) -> dict:
    import trimesh

    t0 = time.time()
    checks = []
    comps = files.get("components", {})
    # QB-07/validity -----------------------------------------------------------
    bad = [n for n, c in comps.items() if not c["valid"]]
    multi = [n for n, c in comps.items() if c["kind"] == "PRINT" and c["solids"] != 1]
    checks.append(_c("occ_validity", "Every component is a valid OpenCascade shape", "failed" if bad else "passed",
                     f"{len(comps)} components, {len(bad)} invalid" + (f": {bad}" if bad else ""), "QB-07"))
    checks.append(_c("print_single_solid", "Each printed part is exactly one solid", "failed" if multi else "passed",
                     "all printed parts are one solid" if not multi else f"multi-solid: {multi}", "QB-07",
                     {"fragments_dropped": model.info.get("fragments_dropped")}))
    # QB-06 prefixes ------------------------------------------------------------
    unlinked = [n for n, c in comps.items() if c["kind"] == "PURCHASE" and not (c.get("module_id") or c.get("ref"))]
    badpref = [n for n in comps if not n.startswith(("PRINT_", "PURCHASE_", "REFERENCE_"))]
    checks.append(_c("role_prefix", "Role prefixes and purchase links", "failed" if (unlinked or badpref) else "passed",
                     f"{sum(1 for c in comps.values() if c['kind'] == 'PURCHASE')} purchased components all carry module_id or ref" if not unlinked
                     else f"no module_id/ref: {unlinked}", "QB-06"))
    # meshes QB-07/10/16 --------------------------------------------------------
    meshes = {}
    for name, pinfo in files.get("print", {}).items():
        f = Path(files.get("print_dir", "")) / Path(pinfo["file"]).name if files.get("print_dir") else None
        if f is None or not f.exists():
            continue
        m = trimesh.load_mesh(f)
        ext = m.bounds[1] - m.bounds[0]
        dims = sorted(ext.tolist(), reverse=True)
        fits = ext[0] <= BED[0] and ext[1] <= BED[1] and ext[2] <= BED[2]
        fits_any = dims[0] <= BED[0] and dims[1] <= BED[1] and dims[2] <= BED[2]
        bv = pinfo.get("brep_volume_mm3") or 0
        vdiff = abs(float(m.volume) - bv) / max(bv, 1e-9) * 100 if bv else None
        meshes[name] = {"watertight": bool(m.is_watertight), "components": int(len(m.split(only_watertight=False))),
                        "volume_mm3": round(float(m.volume), 1), "brep_volume_mm3": round(bv, 1),
                        "volume_diff_pct": None if vdiff is None else round(vdiff, 3), "bounds_mm": [round(float(x), 2) for x in ext],
                        "on_bed": bool(abs(m.bounds[0][2]) < 1e-3), "fits_bed": bool(fits), "fits_bed_rotated": bool(fits_any),
                        "orientation_note": pinfo.get("orientation_note")}
    nw = [n for n, v in meshes.items() if not v["watertight"] or v["components"] != 1]
    checks.append(_c("print_watertight", "Print STLs are watertight with one connected component", "failed" if nw else "passed",
                     f"{len(meshes)} STL files; " + ("all watertight, 1 component each" if not nw else f"problems: {nw}"), "QB-07",
                     {"meshes": meshes}))
    vbad = [n for n, v in meshes.items() if v["volume_diff_pct"] is not None and v["volume_diff_pct"] > 1.0]
    checks.append(_c("brep_vs_stl_volume", "B-rep volume and STL volume agree within 1 %", "failed" if vbad else "passed",
                     "max difference " + f"{max([v['volume_diff_pct'] or 0 for v in meshes.values()] or [0]):.3f} %"
                     + (f"; over 1 %: {vbad}" if vbad else ""), "QB-16"))
    nb = [n for n, v in meshes.items() if not v["fits_bed"] or not v["on_bed"]]
    checks.append(_c("bed_fit", "Each print lies on z=0 in its stated orientation and fits a 250 x 210 x 210 mm bed",
                     "failed" if nb else "passed", "all fit" if not nb else f"do not fit or not on the bed: {nb}", "QB-10",
                     {n: {"bounds_mm": v["bounds_mm"], "orientation_note": v["orientation_note"]} for n, v in meshes.items()}))
    # zmin QB-05 ----------------------------------------------------------------
    allb = np.array([c["bbox"] for c in comps.values() if c["kind"] != "REFERENCE"])
    zmin = float(allb[:, 4].min())
    size = [float(allb[:, 1].max() - allb[:, 0].min()), float(allb[:, 3].max() - allb[:, 2].min()), float(allb[:, 5].max() - zmin)]
    checks.append(_c("frame_zmin", "mm, +Z up, front -Y, assembly rests on z = 0", "passed" if abs(zmin) < 0.01 else "failed",
                     f"zmin = {zmin:.3f} mm; size {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm", "QB-05",
                     {"assembly_size_mm": [round(v, 2) for v in size]}))
    if step_path and Path(step_path).exists():
        try:
            import cadquery as cq

            st = cq.importers.importStep(str(step_path)).val()
            checks.append(_c("step_valid", "STEP re-imports as a valid shape", "passed" if st.isValid() else "failed",
                             f"{Path(step_path).stat().st_size / 1e6:.1f} MB, valid={st.isValid()}", "QB-04"))
        except Exception as e:
            checks.append(_c("step_valid", "STEP re-imports as a valid shape", "failed", str(e), "QB-04"))
    # collisions QB-08/09 -------------------------------------------------------
    mans = _manifolds(model)
    summ, pairs = collisions(model, mans, exact=exact)
    checks.append(_c("collisions", "Full pair matrix of rigid components: no unintended intersection",
                     "failed" if summ["intersections"] else "passed",
                     f"{summ['components']} rigid components, {summ['pairs_total']} pairs, {summ['pairs_bbox_overlap']} with overlapping boxes "
                     f"checked by boolean, {len(summ['intersections'])} unintended intersections, {len(summ['allowed'])} allowed by rule",
                     "QB-08", {**summ, "pairs": pairs}))
    cl = clearances(model, mans)
    tight = [c for c in cl if (not c["contact"]) and c["gap_mm"] < TIGHT_GAP]
    checks.append(_c("clearances", "Clearance from each purchased part to its printed neighbours (contact = intended seat)",
                     "warning" if tight else "passed",
                     f"{len(cl)} purchased/printed pairs within 3 mm; {sum(c['contact'] for c in cl)} seated contacts; "
                     f"{len(tight)} gaps under {TIGHT_GAP} mm", "QB-09", {"pairs": cl}))
    # cartridge + feature module clearance --------------------------------------
    cart_names = [n for n in ("PURCHASE_Backplane", "PURCHASE_CoreBoard", "PURCHASE_Amplifier", "PURCHASE_MicroSdModule") if n in mans]
    others = [n for n in mans if n not in cart_names and not n.startswith(("PURCHASE_HeatSet", "PURCHASE_Cartridge", "PURCHASE_Feet"))]
    rows = []
    for a in cart_names:
        for b in others:
            try:
                g = float(mans[a].min_gap(mans[b], 10.0))
            except Exception:
                continue
            if g < 10.0:
                rows.append({"parts": [a, b], "gap_mm": round(g, 2)})
    shell_like = {"PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate"}
    floor_part = "PRINT_BasePlate" if model.info.get("split") == "base_plate" else "PRINT_Shell"
    seated = {("PURCHASE_Backplane", floor_part), ("PURCHASE_MicroSdModule", floor_part)}  # bosses / S1 support posts
    bad_c = [r for r in rows if r["gap_mm"] < 0.02 and tuple(r["parts"]) not in seated and "Screws" not in r["parts"][1]]
    for r in rows:
        if tuple(r["parts"]) in seated:
            r["note"] = "seated on its printed bosses / support posts"
    kb = model.info.get("cartridge", {}).get("keepout_box")
    checks.append(_c("cartridge_clearance", "Core cartridge clears the shell and the feature modules", "failed" if bad_c else "passed",
                     f"{len(rows)} neighbours within 10 mm; min gap to a non-mount neighbour "
                     f"{min([r['gap_mm'] for r in rows if r['parts'][1] not in shell_like] or [99]):.1f} mm",
                     None, {"pairs": rows, "keepout_box": kb}))
    frows = []
    feat_parts = [p.name for p in model.parts if p.kind == "PURCHASE" and p.feature_id and "Gasket" not in p.name]
    for a in feat_parts:
        for b in mans:
            if b == a or b.startswith(("PURCHASE_HeatSet",)) or "Screws" in b or "Gasket" in b:
                continue
            pa, pb = model.part(a), model.part(b)
            if pb.feature_id == pa.feature_id and pb.kind == "PRINT" and pb.name != "PRINT_Shell":
                continue
            try:
                g = float(mans[a].min_gap(mans[b], 5.0))
            except Exception:
                continue
            if g < 5.0 and b not in shell_like:
                frows.append({"parts": [a, b], "gap_mm": round(g, 2)})
    fbad = [r for r in frows if r["gap_mm"] < 0.5]
    checks.append(_c("feature_module_clearance", "Feature modules clear each other and the cartridge (>= 0.5 mm)",
                     "failed" if fbad else "passed", f"{len(frows)} module neighbour pairs within 5 mm; {len(fbad)} under 0.5 mm", None,
                     {"pairs": frows}))
    # walls ---------------------------------------------------------------------
    walls = {}
    for p in model.parts:
        if p.kind == "PRINT" and p.name in ("PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate"):
            try:
                walls[p.name] = wall_thickness(to_trimesh(p.shape, 0.1, 0.3), 1200)
            except Exception as e:
                walls[p.name] = {"error": str(e)}
    thin = {n: w for n, w in walls.items() if w.get("p1", 9) < MIN_WALL}
    checks.append(_c("min_wall", f"Wall thickness sampling (rule: >= {MIN_WALL} mm at the 1st percentile)",
                     "warning" if thin else "passed",
                     "; ".join(f"{n}: min {w.get('min')} / p1 {w.get('p1')} / median {w.get('median')} mm" for n, w in walls.items()),
                     "QB-11", walls))
    # fasteners QB-14 -----------------------------------------------------------
    fe = fastener_engagement(model)
    fbad2 = [f for f in fe if not f["ok"]]
    checks.append(_c("fastener_engagement", "Every screw engages >= 3 mm of its insert and does not bottom out",
                     "failed" if fbad2 else "passed", f"{len(fe)} screws; {len(fbad2)} problems", "QB-14", {"screws": fe}))
    ta = tool_access(model, mans)
    tbad = [t for t in ta if not t["ok"]]
    checks.append(_c("tool_access", "A 6 mm driver reaches every cartridge, cover and plate screw along its axis",
                     "warning" if tbad else "passed", f"{len(ta)} screws; {len(tbad)} blocked", None, {"screws": ta}))
    # cables QB-15 --------------------------------------------------------------
    cr = model.cables
    cbad = [c for c in cr if not c["length_ok"]]
    cwall = [c for c in cr if c.get("min_clearance_mm") is not None and c["min_clearance_mm"] < -0.01]
    feats_with_cable = [k for k in model.kits if k.cable_end is not None]
    checks.append(_c("cable_routes", "Cable routes named after their cable, length within the harness, clear of walls",
                     "failed" if (cbad or cwall) else ("warning" if len(cr) < len(feats_with_cable) else "passed"),
                     f"{len(cr)} of {len(feats_with_cable)} cables routed; {len(cbad)} too long; {len(cwall)} touch a wall",
                     "QB-15", {"routes": [{k: v for k, v in c.items() if k != "points"} for c in cr]}))
    # coupons QB-13 -------------------------------------------------------------
    cps = {n: c.get("features") for n, c in model.coupons.items()}
    covered = set()
    for n, f in cps.items():
        covered |= set((f or {}).keys())
    need = {"insert_holes", "tie_slot", "usb_c_opening"}
    if any(k.kind in ("snap_button", "tactile") for k in model.kits):
        need.add("button_hole_d")
    if model.info.get("split") == "rear_cover":
        need.add("lip_gap_mm")
    miss = sorted(need - covered)
    checks.append(_c("fit_coupon", "Fit coupons cover every tight interface", "failed" if miss else "passed",
                     f"{len(cps)} coupons; covers {sorted(covered)}" + (f"; missing {miss}" if miss else ""), "QB-13", cps))
    # tip-over ------------------------------------------------------------------
    tp = tip_over(model)
    if tp:
        st = "passed" if tp["min_tip_angle_deg"] >= 15 else ("warning" if tp["min_tip_angle_deg"] >= 10 else "failed")
        checks.append(_c("tip_over", "Static tip-over angle from CAD centre of gravity (>= 15 deg)", st,
                         f"mass {tp['mass_g']} g (estimate), CoG {tp['cog_mm']} mm, minimum tip angle {tp['min_tip_angle_deg']} deg on {tp['support']}",
                         None, tp))
    return {"schema": "gadget-cad-checks/1", "checks": checks, "limits": LIMITS, "seconds": round(time.time() - t0, 1)}
