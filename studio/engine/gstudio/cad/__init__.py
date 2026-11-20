"""gstudio.cad: the CAD stage (ARCHITECTURE §2, §5).

    gstudio.cad.run(ctx) -> dict            full build: cad/ (STEP, GLB, BREP, JSON, gadget_cad.py) and print/ (STL)
    gstudio.cad.preview(spec, lib, out)     fast shell-only product shot -> {"png", "glb", "warnings", "seconds"}

Heavy imports (cadquery, OCP, vtk) happen inside the functions so `import gstudio.cad` stays cheap.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Optional

STAGE = "cad"


def _say(ctx, text: str) -> None:
    fn = getattr(ctx, "say", None)
    if callable(fn):
        try:
            fn(STAGE, text)
        except Exception:
            pass


def run(ctx) -> dict:
    """Build the full parametric CAD for ctx.spec and write §5 outputs under ctx.out/cad and ctx.out/print."""
    from ..errors import StageError
    from ..version import ENGINE_VERSION
    from . import checks as C
    from . import export as E
    from .build import build
    from .params import build_params, jsonable

    t0 = time.time()
    opts = dict(getattr(ctx, "options", None) or {})
    fast = bool(opts.get("fast"))
    out = Path(ctx.out).resolve()
    cad_dir = out / "cad"
    print_dir = out / "print"
    cad_dir.mkdir(parents=True, exist_ok=True)
    print_dir.mkdir(parents=True, exist_ok=True)
    prog = lambda msg, pct=None: ctx.progress(STAGE, msg, pct)  # noqa: E731
    prog("reading the design", 1)
    try:
        params = build_params(ctx.spec, ctx.lib, getattr(ctx, "electrical", None), {"fast": fast})
    except Exception as e:
        raise StageError(f"CAD could not read the design: {e}", {"trace": traceback.format_exc(limit=4)})
    if opts.get("recolor_only"):  # REVIEW-1 MIN-37: colour-only edit on a seeded copy of the base version
        try:
            rec = _recolor(ctx, params, jsonable(params), cad_dir, out, opts, prog, t0)
        except Exception as e:  # never fail the stage on the fast path: rebuild instead
            rec = None
            prog(f"recolour fast path not used ({type(e).__name__}: {e}); rebuilding", 2)
        if rec is not None:
            return rec
    E.write_json(cad_dir / "params.json", jsonable(params))
    draft = {}

    def on_draft(outer, records, colors):
        draft.update(E.write_draft_glb(cad_dir / "draft.glb", outer, records, colors))
        ctx.artifacts["cad.draft"] = str(cad_dir / "draft.glb")
        prog("draft shell ready", 20)

    try:
        model = build(params, progress=prog, on_draft=on_draft)
    except Exception as e:
        raise StageError(f"CAD geometry failed: {e}", {"trace": traceback.format_exc(limit=6)})
    t_build = time.time() - t0
    prog("writing STEP, GLB, BREP and print files", 85)
    files = E.write_all(model, cad_dir, print_dir, vendor=True, engine=ENGINE_VERSION, step=True)
    files["print_dir"] = str(print_dir)
    try:  # REVIEW-1 MIN-23: each purchased envelope names the saved dimensional / pinout sources it was built from
        _stamp_source_files(cad_dir / "components.json", ctx.lib)
    except Exception as e:  # noqa: BLE001 - provenance stamping never fails the CAD
        model.warnings.append(f"component source files not stamped: {e}")
    t_export = time.time() - t0 - t_build
    renders = {}
    if opts.get("cad_renders", True):
        from .render_cli import render_subprocess

        prog("rendering the hero view", 90)
        views = ("hero", "rear_open", "xray") if opts.get("cad_views") == "all" else ("hero",)
        renders = render_subprocess(cad_dir / "assembly.glb", cad_dir / "renders", views, model.info.get("screens"),
                                    model.info.get("split", "rear_cover"), 1200, timeout=float(opts.get("render_timeout", 150)))
        if "error" in renders:
            model.warnings.append(f"hero render skipped: {renders['error'][:200]}")
            renders = {}
    t_render = time.time() - t0 - t_build - t_export
    prog("running digital checks", 94)
    res = C.run_checks(model, files, step_path=None if fast else cad_dir / "assembly.step")
    E.write_json(cad_dir / "digital-checks.json", res)
    mw = next((c for c in res["checks"] if c["id"] == "min_wall"), None)
    if mw and mw["status"] == "failed" and opts.get("cad_renders", True):  # MIN-9: show where the walls are thin
        from .render_cli import render_subprocess

        pts = [p for w in (mw.get("data") or {}).values() if isinstance(w, dict) for p in (w.get("thin_points") or [])]
        r = render_subprocess(cad_dir / "assembly.glb", cad_dir / "renders", ("thin",), None, model.info.get("split", "rear_cover"),
                              1000, timeout=float(opts.get("render_timeout", 150)), name="thin-walls.png", points=pts)
        if r.get("thin"):
            renders["thin_walls"] = r["thin"]
            mw["artifact"] = "cad/renders/thin-walls.png"
            E.write_json(cad_dir / "digital-checks.json", res)
        else:
            model.warnings.append(f"thin-wall overlay skipped: {r.get('error', '')[:200]}")
    t_checks = time.time() - t0 - t_build - t_export - t_render
    for k, v in {"cad.components": "components.json", "cad.anchors": "anchors.json", "cad.cable_routes": "cable-routes.json",
                 "cad.glb": "assembly.glb", "cad.step": "assembly.step", "cad.params": "params.json", "cad.checks": "digital-checks.json",
                 "cad.source": "gadget_cad.py"}.items():
        ctx.artifacts[k] = str(cad_dir / v)
    ctx.artifacts["cad.renders"] = renders
    ctx.artifacts["print.files"] = {n: str(out / v["file"]) for n, v in files["print"].items()}
    for w in model.warnings:
        try:
            ctx.warn(STAGE, w)
        except Exception:
            pass
    failed = [c for c in res["checks"] if c["status"] == "failed"]
    n_print = sum(1 for v in files["print"].values() if not v.get("test"))
    say = (f"Built the enclosure: {len(model.parts)} named parts, {n_print} printed parts plus "
           f"{len(model.coupons)} fit coupons; {len(res['checks']) - len(failed)} of {len(res['checks'])} CAD checks passed.")
    _say(ctx, say)
    summary = {
        "ok": not failed,
        "say": say,
        "components": len(model.parts),
        "files": {"step": "cad/assembly.step", "glb": "cad/assembly.glb", "components": "cad/components.json",
                  "anchors": "cad/anchors.json", "cable_routes": "cad/cable-routes.json", "params": "cad/params.json",
                  "source": "cad/gadget_cad.py", "draft": "cad/draft.glb" if draft else None,
                  "renders": {k: os.path.relpath(v, out) for k, v in renders.items()}},
        "print": {n: {"file": v["file"], "orientation_note": v["orientation_note"],
                      "material": v.get("material") or (params.get("print_material") or {}).get("name") or "PLA",  # MIN-12: coupons too
                      **({"tests": v["tests"], "note": v.get("note")} if v.get("test") else {})}
                  for n, v in files["print"].items()},
        "rebuild": "python cad/gadget_cad.py",
        "checks": [{k: c[k] for k in ("id", "title", "status", "detail") if k in c} | ({"qb": c["qb"]} if c.get("qb") else {})
                   | {"artifact": "cad/digital-checks.json"} for c in res["checks"]],
        "limits": res["limits"],
        "physical_checks": [{"id": "usb-cord-pull", "kind": "physical", "status": "not_run",
                             "title": "Pull the USB-C cord along its axis",
                             "expected": "the plug releases before the receptacle or the board moves; the cord tie takes the load",
                             "why": "REVIEW-1 MIN-3 load path: " + ((model.info.get("usb_c") or {}).get("load_path") or {}).get("feature", "none modelled")}],
        "decor_adjustments": model.info.get("decor_adjustments") or [],
        "info": {k: v for k, v in model.info.items() if k != "kits"},
        "kits": model.info.get("kits"),
        "warnings": model.warnings,
        "glb": files.get("glb"),
        "timings": {"build": round(t_build, 1), "export": round(t_export, 1), "render": round(t_render, 1),
                    "checks": round(t_checks, 1), "total": round(time.time() - t0, 1), "geometry": model.timings,
                    "export_steps": files.get("timings", {}), "checks_seconds": res.get("seconds")},
    }
    E.write_json(cad_dir / "cad-summary.json", summary)
    return summary


def preview(spec: Any, lib: Any = None, out: Any = ".", size=(1024, 1024)) -> dict:
    """Fast product shot of the shell with its apertures, buttons, screens and diffusers (no internals).
    Writes <out>/preview.png and <out>/preview.glb. Returns {"png", "glb", "warnings", "seconds"}."""
    from . import export as E
    from .build import build_shell_preview, classify_faces
    from .features import build_kit
    from .geom import largest_solid
    from .params import build_params
    from .parts import Part
    from .probe import Probe

    t0 = time.time()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    params = build_params(spec, lib, None)
    pv = build_shell_preview(params)
    warnings = list(pv["warnings"]) + list(params.get("warnings", []))
    O = pv["outer"]
    C = pv["bg"].cavity
    if pv["pads"]:
        C = largest_solid(C.cut(*pv["pads"]).clean())
    colors = {k: tuple(v) if isinstance(v, (list, tuple)) else v for k, v in params["colors"].items()}
    probeO, probeC = Probe(O, tol=0.2), Probe(C, tol=0.25)
    cuts, vis, screens = [], [], []
    for f in params["features"]:
        try:
            k = build_kit(f, f["desc"], f["ref"], probeO, probeC, O, params["fasteners"], colors, f["spec_path"],
                          others=params["features"], skin=pv["bg"].skin_front if params["body"]["family"] == "silhouette" else
                          float(params["body"].get("wall", 2.4)))
        except Exception as e:
            warnings.append(f"preview: feature {f['id']} skipped ({e})")
            continue
        warnings += k.warnings
        cuts += k.cuts
        if k.info.get("screen"):
            screens.append(k.info["screen"])
        for p in k.parts:
            if p.name.startswith(("PURCHASE_Display", "PURCHASE_Button", "PRINT_LedDiffuser", "PRINT_ButtonCap")):
                if p.name.startswith("PURCHASE_Display"):
                    p.subparts = [s for s in p.subparts if s[0] in ("Glass", "Screen")]
                if p.name.startswith("PURCHASE_Button"):
                    p.subparts = [s for s in p.subparts if s[0] in ("Bezel", "Plunger", "Pcb")][:2]
                vis.append(p)
    shell = O.cut(*cuts).clean() if cuts else O
    shell = largest_solid(shell) if len(shell.Solids()) > 1 else shell
    sp = Part("PRINT_Shell", "PRINT", "shell", colors["body"])
    sp.add("Shell", shell)
    fc = {"PRINT_Shell": classify_faces(shell, pv["records"], colors)}
    parts = [sp] + vis
    glb_nodes = [(p.name, E.part_primitives(p, fc.get(p.name), tol=0.1, ang=0.28), p.alpha) for p in parts]
    E.write_glb(out / "preview.glb", glb_nodes, generator="Wish Labs CAD preview")
    from .render_cli import render_subprocess

    r = render_subprocess(out / "preview.glb", out, ("hero",), screens, "rear_cover", size[0], timeout=60.0, name="preview.png")
    png = r.get("hero")
    if not png:
        warnings.append(f"preview render failed: {r.get('error', '')[:200]}")
    return {"png": png, "glb": str(out / "preview.glb"), "warnings": warnings, "seconds": round(time.time() - t0, 1)}


__all__ = ["run", "preview"]


# ------------------------------------------------------------------------------------------------ recolour (MIN-37)
_GEOM_SKIP = ("colors", "colors_hex", "warnings")


def _recolor_map(old_cols: dict, new_cols: dict) -> Optional[dict]:
    """sRGB tuple map old -> new over the palette roles; None if two roles shared an old colour but now differ."""
    m: dict = {}
    for k, ov in (old_cols or {}).items():
        nv = (new_cols or {}).get(k)
        if not (isinstance(ov, (list, tuple)) and isinstance(nv, (list, tuple)) and len(ov) >= 3 and len(nv) >= 3):
            continue
        key = tuple(round(float(x), 4) for x in ov[:3])
        val = tuple(float(x) for x in nv[:3])
        if key in m and any(abs(a - b) > 1e-4 for a, b in zip(m[key], val)):
            return None
        m[key] = val
    return m


def _lookup(m: dict, rgb, tol: float = 2e-3):
    for k, v in m.items():
        if all(abs(float(a) - b) <= tol for a, b in zip(rgb[:3], k)):
            return v
    return None


def _recolor_glb(path: Path, m: dict) -> int:
    """Rewrite baseColorFactor of every material whose colour is an old palette colour. Returns materials changed."""
    import struct

    from .export import _srgb_to_linear

    b = path.read_bytes()
    magic, ver, _total = struct.unpack("<4sII", b[:12])
    if magic != b"glTF":
        raise ValueError("not a GLB")
    jlen, jtype = struct.unpack("<II", b[12:20])
    doc = json.loads(b[20:20 + jlen])
    rest = b[20 + jlen:]
    lin_map = {tuple(round(_srgb_to_linear(x), 4) for x in k): tuple(_srgb_to_linear(x) for x in v) for k, v in m.items()}
    n = 0
    for mat in doc.get("materials") or []:
        pbr = mat.get("pbrMetallicRoughness") or {}
        f = pbr.get("baseColorFactor")
        if not f:
            continue
        nv = _lookup(lin_map, f, tol=2e-3)
        if nv is not None and any(abs(a - b) > 1e-6 for a, b in zip(f[:3], nv)):
            pbr["baseColorFactor"] = [round(x, 6) for x in nv] + list(f[3:4] or [1.0])
            n += 1
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", ver, 12 + 8 + len(js) + len(rest)) + struct.pack("<II", len(js), jtype) + js + rest)
    return n


def _recolor(ctx, params: dict, newp: dict, cad_dir: Path, out: Path, opts: dict, prog, t0: float) -> Optional[dict]:
    """Colour-only edit: reuse the seeded geometry, STL, BREP and CAD checks; rewrite the colours in assembly.glb,
    components.json and params.json and re-render the hero view. Returns the stage summary, or None to rebuild."""
    from . import export as E

    need = [cad_dir / n for n in ("params.json", "cad-summary.json", "assembly.glb", "components.json", "digital-checks.json")]
    if not all(p.is_file() for p in need):
        return None
    oldp = json.loads((cad_dir / "params.json").read_text())
    strip = lambda d: {k: v for k, v in d.items() if k not in _GEOM_SKIP}  # noqa: E731
    if json.dumps(strip(oldp), sort_keys=True, default=str) != json.dumps(strip(newp), sort_keys=True, default=str):
        prog("recolour: the geometry inputs differ from the base version; rebuilding", 2)
        return None
    m = _recolor_map(oldp.get("colors") or {}, newp.get("colors") or {})
    if m is None:
        prog("recolour: two colour roles shared one colour and now differ; rebuilding", 2)
        return None
    prog("recolour: reusing the base version's geometry", 10)
    n_mat = _recolor_glb(cad_dir / "assembly.glb", m)
    comps = json.loads((cad_dir / "components.json").read_text())
    n_comp = 0
    for c in comps.values():
        if isinstance(c, dict) and isinstance(c.get("color"), list):
            nv = _lookup(m, c["color"])
            if nv is not None:
                c["color"] = [round(float(x), 4) for x in nv]
                c["color_hex"] = "#%02X%02X%02X" % tuple(int(round(255 * max(0.0, min(1.0, x)))) for x in nv)
                n_comp += 1
    E.write_json(cad_dir / "components.json", comps)
    E.write_json(cad_dir / "params.json", newp)
    summary = json.loads((cad_dir / "cad-summary.json").read_text())
    renders = {}
    if opts.get("cad_renders", True):
        from .render_cli import render_subprocess

        prog("rendering the hero view in the new colours", 60)
        info = summary.get("info") or {}
        renders = render_subprocess(cad_dir / "assembly.glb", cad_dir / "renders", ("hero",), info.get("screens"),
                                    info.get("split", "rear_cover"), 1200, timeout=float(opts.get("render_timeout", 150)))
        if "error" in renders:
            renders = {}
    secs = round(time.time() - t0, 1)
    hexes = {k: [(oldp.get("colors_hex") or {}).get(k), (newp.get("colors_hex") or {}).get(k)] for k in ("body", "accent", "detail")}
    summary["recolored"] = {"from": "the seeded base version (options.recolor_only)", "materials_changed": n_mat,
                            "components_changed": n_comp, "colours": hexes,
                            "unchanged": "geometry, STL, BREP, anchors, cable routes and the CAD digital checks are the base version's",
                            "limits": ["cad/assembly.step keeps the base version's display colours (geometry identical)"]}
    summary["say"] = (f"Recoloured the base version's CAD in {secs:g} s: the geometry did not change, so the parts, print files "
                      f"and CAD checks are the base version's ({n_mat} materials recoloured). " + str(summary.get("say") or ""))
    summary["timings"] = {"total": secs, "recolor": True, "base_timings": summary.get("timings")}
    if renders:
        summary.setdefault("files", {})["renders"] = {k: os.path.relpath(v, out) for k, v in renders.items()}
    E.write_json(cad_dir / "cad-summary.json", summary)
    for k, v in {"cad.components": "components.json", "cad.anchors": "anchors.json", "cad.cable_routes": "cable-routes.json",
                 "cad.glb": "assembly.glb", "cad.step": "assembly.step", "cad.params": "params.json", "cad.checks": "digital-checks.json",
                 "cad.source": "gadget_cad.py"}.items():
        ctx.artifacts[k] = str(cad_dir / v)
    ctx.artifacts["cad.renders"] = renders
    ctx.artifacts["print.files"] = {n: str(out / v["file"]) for n, v in (summary.get("print") or {}).items()
                                    if isinstance(v, dict) and v.get("file")}
    _say(ctx, summary["say"])
    prog("recolour done", 100)
    return summary


# ------------------------------------------------------------------------------------------------ MIN-23 sources
def _stamp_source_files(path: Path, lib) -> int:
    """components.json PURCHASE entries gain `source_files` [{path (in the export), library_path, sha256, url}] from the
    library row's saved `evidence_files` under references/sources/ (datasheets, drawings, pinout pages); the package
    stage copies those files into the export and checks their sha256 (MIN-23). Returns the entries stamped."""
    comps = json.loads(path.read_text())
    n = 0
    for name, c in comps.items():
        mid = c.get("module_id") if isinstance(c, dict) else None
        if not mid or c.get("kind") != "PURCHASE":
            continue
        try:
            m = lib.get(mid) if lib is not None and (not hasattr(lib, "has") or lib.has(mid)) else None
        except Exception:  # noqa: BLE001
            m = None
        ev = [e for e in ((m or {}).get("evidence_files") or []) if isinstance(e, dict)
              and str(e.get("path") or "").startswith("references/sources/") and e.get("sha256")]
        if not ev:
            continue
        c["source_files"] = [{"path": "references/library/" + str(e["path"])[len("references/"):], "library_path": e["path"],
                              "sha256": e["sha256"], "url": e.get("url"), "covers": e.get("covers") or []} for e in ev]
        n += 1
    path.write_text(json.dumps(comps, indent=1))
    return n
