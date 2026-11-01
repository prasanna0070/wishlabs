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
        "print": {n: {"file": v["file"], "orientation_note": v["orientation_note"], "material": v.get("material", "PLA"),
                      **({"tests": v["tests"], "note": v.get("note")} if v.get("test") else {})}
                  for n, v in files["print"].items()},
        "rebuild": "python cad/gadget_cad.py",
        "checks": [{k: c[k] for k in ("id", "title", "status", "detail") if k in c} | ({"qb": c["qb"]} if c.get("qb") else {})
                   | {"artifact": "cad/digital-checks.json"} for c in res["checks"]],
        "limits": res["limits"],
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
            k = build_kit(f, f["desc"], f["ref"], probeO, probeC, O, params["fasteners"], colors, f["spec_path"])
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
    E.write_glb(out / "preview.glb", glb_nodes, generator="Gadget Studio CAD preview")
    from .render_cli import render_subprocess

    r = render_subprocess(out / "preview.glb", out, ("hero",), screens, "rear_cover", size[0], timeout=60.0, name="preview.png")
    png = r.get("hero")
    if not png:
        warnings.append(f"preview render failed: {r.get('error', '')[:200]}")
    return {"png": png, "glb": str(out / "preview.glb"), "warnings": warnings, "seconds": round(time.time() - t0, 1)}


__all__ = ["run", "preview"]
