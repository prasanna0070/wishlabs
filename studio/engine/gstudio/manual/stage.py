"""gstudio.manual.run(ctx): steps, wiring, tests, illustrations, wiring diagram, BUILD_GUIDE.md, summary, and the
regenerable figure bundle. gstudio.manual.finalize(ctx): re-render the guide's banner, known-issue lines, stop rules,
final checks and manual-summary from checks/digital-checks.json after the checks stage (BLK-7, MAJ-19)."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from .data import ManualData, load, read_json, ring_dims
from .figs import TOP_34, FigureSpec, LabelSpec, PanelSpec
from .finals import build_tests
from .guide import write_guide
from .illustrate import frame_pcb
from .issues import Issues, load_issues
from .model import ROLE_NOUN, Assembly, anchor_owner, anchors_for, classify, comp_qty
from .scene import Scene, SceneComp
from .steps import FOOTER, Step, plan
from .summary import audit, checks_from, figure_fields
from .wiring import build_wiring, harnesses

STAGE = "manual"
SHARED_VISUALS = {
    "manual/backplane-pcb.png": ("KiCad render of this build's routed backplane, top side, with the printed port labels.",
                                 "Rendered by the PCB stage from pcb/backplane.kicad_pcb, not a photo of a made board. "
                                 "Compare the printed labels with the port names in this guide."),
    "manual/wiring.png": ("Top view of the backplane with the used ports highlighted, each module's cable drawn in its wire colours, the module-end housing of each cable, and the pin table.",
                          "Diagram from the saved backplane and electrical data, not a photo. Read the port names on your board; colours are only a cross-check."),
    "manual/ports.png": ("The core cartridge from above with every port this build uses labelled with the module that plugs into it.",
                         "Port positions from the saved CAD and backplane data. It does not prove a plug is seated; push each one fully home."),
}


def cable_end_visual(rel: str, data: ManualData) -> tuple[str, str]:
    from .cables import module_ends

    ref = rel.rsplit("cable-end-", 1)[-1][:-4].upper()
    noun = ROLE_NOUN.get((data.instance(ref).get("role") or ""), "module")
    me = module_ends(data).get(ref)
    cap = "Drawn from the saved harness data, not a photo. Read the pin labels on the module before fitting the cable."
    if me is not None and me.mismatch:
        return (f"The {ref} cable end: the listed cable does not mate the {noun}'s connector, and the table of its wires.", cap)
    if me is not None and me.family == "factory_prewired":
        return (f"The {ref} lead: pre-wired at the factory to the {noun}'s pads, with the wire table.", cap)
    if me is not None and me.family == "spade_2.8":
        return (f"The {ref} cable end: which {noun} terminal each spade receptacle and wire colour goes on.", cap)
    return (f"The module end of the {ref} cable: each housing position with its wire colour and the {noun} pin it goes on, position 1 marked.", cap)


def qty_mismatches(data: ManualData) -> list[dict]:
    hw: dict[str, int] = {}
    for h in data.hardware():
        mid = h.get("module") or h.get("id")
        if mid and isinstance(h.get("qty"), int):
            hw[mid] = hw.get(mid, 0) + h["qty"]
    cad: dict[str, int] = {}
    for c in data.comps.values():
        if c.kind == "PURCHASE" and c.module_id in hw and not c.ref:
            cad[c.module_id] = cad.get(c.module_id, 0) + comp_qty(data, c)[0]
    return [{"module": m, "cad": cad[m], "hardware_list": hw[m]} for m in sorted(cad) if cad[m] != hw[m]]


# --------------------------------------------------------------------------------------------- ports figure
def ports_figure(data: ManualData, A: Assembly) -> Optional[FigureSpec]:
    from .diagram import ports_pin_count

    bp = A.backplane
    if not bp or not bp.bbox:
        return None
    ref = bp.ref or "BP1"
    anchors = anchors_for(A, ref)
    labels = []
    for h in harnesses(data):
        key = f"{ref}.{h.slot}"
        if key in anchors:
            noun = ROLE_NOUN.get(h.role, h.role)
            pins = ports_pin_count(data, h.slot)  # from the slot data, never from the wires used (MIN-30)
            labels.append(LabelSpec(text=f"{h.slot}\n{noun}" + (f" ({pins}-pin port)" if pins else ""), anchor=key, priority=2))
    pk = next((k for k in anchors if "PWR" in k.upper()), None)
    if pk:
        labels.append(LabelSpec(text="J_PWR\nUSB-C power in", anchor=pk, priority=1))
    if not labels:
        return None
    show = {c.name: "ghost" for c in A.cartridge}
    show[bp.name] = "focus"
    return FigureSpec(
        file="manual/ports.png", title="Backplane ports used by this build",
        panels=[PanelSpec(subtitle="Core cartridge seen from above and in front; +X (the gadget's left) is on your right",
                          show=show, view_dir=TOP_34, focus=list(show), labels=labels, label_room=0.27)],
        footer=FOOTER + " | connector latches and pins simplified",
        alt=SHARED_VISUALS["manual/ports.png"][0], caption=SHARED_VISUALS["manual/ports.png"][1],
    )


# --------------------------------------------------------------------------------------------- rendering
def make_scene(data: ManualData, A: Assembly) -> Scene:
    sc = Scene()
    for c in [*data.comps.values(), *data.subcomps.values()]:
        raw = {}
        if c.raw.get("solid_index") is not None:
            raw = {"solid_index": list(c.raw["solid_index"]), "base": c.raw.get("base")}
        sc.comps[c.name] = SceneComp(name=c.name, color=c.color, bbox=c.bbox, qty=comp_qty(data, c)[0], raw=raw)
    sc.anchors = dict(data.anchors)
    for scr in ((data.cad_summary().get("info") or {}).get("screens") or []):  # MAJ-32: draw the firmware's idle face
        if isinstance(scr, dict) and scr.get("component") in sc.comps and scr.get("texture") and all(scr.get(k) for k in ("c", "u", "v")):
            if (Path(data.out) / scr["texture"]).is_file():
                sc.screens.append({k: scr.get(k) for k in ("component", "c", "u", "v", "n", "aa_w", "aa_h", "w", "h", "texture", "ref")})
    for k in data.anchors:
        o = anchor_owner(A, k)
        if o is not None:
            sc.owners[k] = o.name
    return sc


def mesh_sources(data: ManualData) -> dict:
    routes = {}
    for r in data.routes:
        name = r.component if r.component in data.comps else (r.id if r.id in data.comps else None)
        if name and len(r.points) >= 2:
            routes[name] = ([list(p) for p in r.points], float(r.diameter or 3.0))
    out = Path(data.out)

    def rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(out.resolve()).as_posix()
        except ValueError:
            return str(p)

    return {"brep_dir": rel(data.brep_dir), "glb": rel(data.glb) if data.glb else None,
            "ref_bboxes": {c.name: list(c.bbox) for c in data.comps.values() if c.bbox}, "routes": routes,
            "solid_boxes": {k: [list(b) for b in v] for k, v in data.solid_boxes.items()}}


def mesh_store(data: ManualData, cache, workers: int = 0):
    from .render3d import MeshStore

    m = mesh_sources(data)
    return MeshStore(data.brep_dir, cache_dir=cache, workers=workers, glb=data.glb,
                     ref_bboxes={k: tuple(v) for k, v in m["ref_bboxes"].items()},
                     routes={k: ([tuple(p) for p in v[0]], v[1]) for k, v in m["routes"].items()},
                     solid_boxes=data.solid_boxes)


def _render_chunk(job: tuple) -> list:
    """Worker entry (spawned process): one GL context, a batch of figures."""
    data, scene, figs, out, cache, threads = job
    os.environ["LP_NUM_THREADS"] = str(threads)
    from .illustrate import Illustrator
    from .render3d import Renderer

    meshes = mesh_store(data, cache, workers=1)
    meshes.preload(sorted({n.split("#", 1)[0] for _, f in figs for p in f.panels for n in p.show}))
    ill = Illustrator(scene, meshes, Renderer())
    return [(i, ill.render_figure(f, out)) for i, f in figs]


def render_all(data: ManualData, scene: Scene, figs: list[FigureSpec], out: Path, cache: Optional[Path], workers: int,
               progress) -> tuple[list, dict, Path]:
    names = sorted({n.split("#", 1)[0] for f in figs for p in f.panels for n in p.show})
    ms = mesh_store(data, cache)
    t = time.time()
    errs = ms.preload(names)
    progress(f"tessellated {len(names)} components in {time.time() - t:.1f}s" + (f" ({len(errs)} missing)" if errs else ""), 20)
    order = sorted(range(len(figs)), key=lambda i: -len(figs[i].panels))
    workers = max(1, min(workers, len(figs)))
    threads = max(2, (os.cpu_count() or 4) // workers)
    jobs = [(data, scene, [(i, figs[i]) for i in order[k::workers]], out, ms.cache_dir, threads) for k in range(workers)]
    results: dict[int, Any] = {}
    if workers == 1:
        chunks = [_render_chunk(jobs[0])]
    else:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            chunks = list(ex.map(_render_chunk, jobs))
    for chunk in chunks:
        for i, r in chunk:
            results[i] = r
    return [results[i] for i in range(len(figs))], errs, ms.cache_dir


# --------------------------------------------------------------------------------------------- shared preparation
def prepare(ctx: Any, issues_from_checks: bool = False) -> tuple[ManualData, Assembly, Issues, list[Step]]:
    data = load(ctx)
    if not data.comps:
        from ..errors import StageError

        raise StageError("manual needs cad/components.json from the cad stage", {"path": str(Path(ctx.out) / "cad" / "components.json")})
    for c in data.comps.values():  # split fastener groups among their owners by solid position
        if c.kind == "PURCHASE" and not c.ref and c.solids > 1 and (data.brep_dir / f"{c.name}.brep").is_file():
            try:
                from .data import solid_bboxes

                data.solid_boxes[c.name] = solid_bboxes(data.brep_dir / f"{c.name}.brep")
            except Exception:
                pass
    rings = {}
    for c in data.comps.values():  # gasket rings cut from a strip: their sizes from the BREP (MAJ-11)
        if c.kind == "PURCHASE" and re.search(r"gasket", f"{c.name} {c.label}", re.I) and (data.brep_dir / f"{c.name}.brep").is_file():
            rd = ring_dims(data.brep_dir / f"{c.name}.brep")
            if rd:
                rings[c.name] = rd
    data.rings = rings  # type: ignore[attr-defined]
    A = classify(data)
    issues = load_issues(data)
    steps = plan(A, issues)
    return data, A, issues, steps


def module_end_missing(data: ManualData, steps: list[Step]) -> list[str]:
    """Harnesses with no specific module-end instruction in any step (MAJ-10)."""
    from .cables import module_ends

    ends = module_ends(data)
    covered = {r for s in steps for r in s.module_end}
    out = []
    for h in harnesses(data):
        me = ends.get(h.ref)
        if me is None or me.family == "unknown" or h.ref not in covered:
            out.append(f"{h.ref} ({h.slot})")
    return out


def step_visuals(out: Path, data: ManualData, steps: list[Step], ok_files: set[str], alts: dict[str, str]) -> dict[str, list[dict]]:
    visuals: dict[str, list[dict]] = {}
    for s in steps:
        vs = [{"path": f.file, "alt": alts.get(f.file, f.alt), "caption": f.caption} for f in s.figures if f.file in ok_files]
        for p in s.extra_images:
            if (out / p).is_file() and p not in {v["path"] for v in vs}:
                alt, cap = SHARED_VISUALS.get(p) or (cable_end_visual(p, data) if "cable-end-" in p else (p, ""))
                vs.append({"path": p, "alt": alt, "caption": cap})
        visuals[s.id] = vs
    return visuals


def json_steps(steps: list[Step], visuals: dict[str, list[dict]]) -> list[dict]:
    out = []
    for s in steps:
        rows = [r for r in dict.fromkeys(s.rows) if r]
        parts = [p for p in s.parts if p not in rows]  # CAD component names only (steps[].parts joins to components.json)
        st = {"id": s.id, "title": s.title, "instruction": s.instruction, "expected": s.expected,
              "parts": parts, "highlight": s.highlight, "camera": s.camera, "visuals": visuals.get(s.id, [])}
        if rows:
            st["purchased"] = rows  # module ids of purchased rows this step uses that have no CAD part (MAJ-14)
        out.append(st)
    return out


def write_fragment(out: Path, build: dict) -> None:
    fr = out / ".gstudio" / "fragments"
    try:
        fr.mkdir(parents=True, exist_ok=True)
        (fr / "manual.json").write_text(json.dumps({k: build.get(k) for k in ("steps", "wiring", "tests")}, indent=1,
                                                   ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def alt_from(fig: FigureSpec, placed: list[str]) -> str:
    """Alt text that claims only what is drawn: the figure's description plus the labels actually placed (MIN-31)."""
    base = fig.alt.rstrip(".")
    labs = list(dict.fromkeys(re.sub(r"\s+", " ", p).strip() for p in placed if p.strip()))
    if not labs:
        return base + "."
    return base + ". Labelled: " + "; ".join(labs) + "."


# --------------------------------------------------------------------------------------------- run
def run(ctx: Any) -> dict:
    t0 = time.time()

    def progress(msg: str, pct: Optional[float] = None):
        try:
            ctx.progress(STAGE, msg, pct)
        except Exception:
            pass

    data, A, issues, steps = prepare(ctx)
    out = Path(ctx.out)
    opts = getattr(ctx, "options", {}) or {}
    progress(f"planned {len(steps)} assembly steps", 5)
    from ..errors import StageError

    missing_ends = module_end_missing(data, steps)
    if missing_ends and not opts.get("manual_lenient"):
        raise StageError("manual: a harness has no module-end instruction (MAJ-10): " + ", ".join(missing_ends),
                         {"harnesses": missing_ends})
    probe = {"purchased": data.purchased(), "steps": json_steps(steps, {})}
    from .summary import purchased_unused

    unused = purchased_unused(probe, {**data.comps, **data.subcomps})
    if unused and not opts.get("manual_lenient"):
        raise StageError("manual: purchased rows used in no assembly step (MAJ-14): " + "; ".join(unused), {"rows": unused})

    mdir = out / "manual"
    if mdir.is_dir():
        shutil.rmtree(mdir)
    mdir.mkdir(parents=True, exist_ok=True)

    from .diagram import draw_wiring

    if data.spec.get("kind") == "printed":  # catalog package A: no electronics, so no wiring diagram
        wd = {"harnesses": 0, "rows": 0, "overlaps": [], "canvases": {}, "ports_drawn": 0, "module_ends": {}}
    else:
        wd = draw_wiring(data, A, mdir)
    framed = []
    pcb_fig = bool(data.pcb_top_png) and frame_pcb(data.pcb_top_png, mdir / "backplane-pcb.png", data.product_name)
    if pcb_fig:
        framed.append({"file": "manual/backplane-pcb.png", "src": "pcb/board-top.png", "product": data.product_name})
        for s_ in steps:
            if s_.id == "plug-harnesses":
                s_.extra_images.insert(0, "manual/backplane-pcb.png")
    progress(f"wiring diagram: {wd['harnesses']} cables, {wd['rows']} wires, {len(wd['overlaps'])} overlaps", 10)

    figs: list[FigureSpec] = []
    seen = set()
    for s in steps:
        for f in s.figures:
            if f.file not in seen:
                seen.add(f.file)
                figs.append(f)
    pf = ports_figure(data, A)
    if pf:
        figs.append(pf)
    scene = make_scene(data, A)
    results, mesh_errs = [], {}
    if not opts.get("manual_no_render"):
        workers = int(opts.get("manual_workers") or min(8, max(1, (os.cpu_count() or 2) // 2)))
        cache = opts.get("mesh_cache")
        results, mesh_errs, _ = render_all(data, scene, figs, out, Path(cache) if cache else None, workers, progress)
        progress(f"rendered {sum(1 for r in results if r.ok)}/{len(figs)} illustrations", 85)
    ok_files = {r.file for r in results if r.ok}
    fig_by_file = {f.file: f for f in figs}
    alts = {r.file: alt_from(fig_by_file[r.file], r.placed) for r in results if r.ok}

    visuals = step_visuals(out, data, steps, ok_files, alts)
    bboxes = {c.name: c.bbox for c in [*data.comps.values(), *data.subcomps.values()] if c.bbox}
    wiring = build_wiring(data, A, bboxes)
    if pcb_fig and wiring["steps"]:
        alt, cap = SHARED_VISUALS["manual/backplane-pcb.png"]
        wiring["steps"][0].setdefault("visuals", []).append({"path": "manual/backplane-pcb.png", "alt": alt, "caption": cap})
    for ws in wiring["steps"]:
        ws["visuals"] = [v for v in ws.get("visuals", []) if (out / v["path"]).is_file()]
        if not ws["visuals"]:
            ws.pop("visuals")
    tests = build_tests(data, A)
    build_path = out / "build.json"
    build = read_json(build_path, {}) or {}
    build.setdefault("schema", "gadget-build/1")
    build.update({"steps": json_steps(steps, visuals), "wiring": wiring, "tests": tests})
    build_path.write_text(json.dumps(build, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_fragment(out, build)

    md = write_guide(data, A, steps, visuals, tests, wiring, issues)
    (out / "BUILD_GUIDE.md").write_text(md, encoding="utf-8")

    from .bundle import write_figures_json, write_renderer

    write_figures_json(out, [f for f in figs if f.file in ok_files], scene, mesh_sources(data), wd["canvases"], framed, alts)
    write_renderer(out)

    extra = {"wiring_overlaps": wd["overlaps"], "wiring_ports_drawn": wd["ports_drawn"], "module_ends": wd["module_ends"],
             "harness_module_end_missing": missing_ends, "known_issues": [i.line({}) for i in issues.pairs],
             "known_issue_banner": issues.banner, "issues_source": issues.source,
             "qty_mismatches": qty_mismatches(data), "mesh_errors": mesh_errs,
             "unrendered_figures": sorted(set(fig_by_file) - ok_files), "regenerate": "python manual/render_manual.py --compare",
             "_comps": {**data.comps, **data.subcomps}}
    fig = figure_fields(results)
    summary = audit(out, build, md, fig, time.time() - t0, extra)
    summary["checks"] = checks_from(summary)
    (mdir / "manual-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    arts = getattr(ctx, "artifacts", None)
    if isinstance(arts, dict):
        arts["manual.summary"] = str(mdir / "manual-summary.json")
        arts["manual.guide"] = str(out / "BUILD_GUIDE.md")
        arts["manual.figures"] = str(mdir / "figures.json")
        arts["manual.build_sections"] = ["steps", "wiring", "tests"]
    for r in results:
        if not r.ok and hasattr(ctx, "warn"):
            ctx.warn(STAGE, f"{r.file}: {r.error}")
    progress(f"manual done in {summary['seconds']}s: {summary['images_png']} PNG + {summary['images_svg']} SVG, "
             f"{len(summary['orphans'])} orphans, {summary['label_overlaps']} label overlaps, "
             f"{len(summary['wiring_overlaps'])} wiring overlaps", 100)
    return summary


# --------------------------------------------------------------------------------------------- finalize
def finalize(ctx: Any) -> dict:
    """Re-render, after the checks stage, everything in the guide that depends on the checks: the do-not-print
    banner, the known-issue lines and stop rules on the affected steps (from checks/digital-checks.json), the final
    checks (every tests[] record in build.json, failed first) and manual/manual-summary.json. No image is re-rendered;
    the figure counts are kept from the stage's own summary. Deterministic: running it twice gives the same files."""
    data, A, issues, steps = prepare(ctx)
    out = Path(ctx.out)
    mdir = out / "manual"
    prev = read_json(mdir / "manual-summary.json", {}) or {}
    figdoc = read_json(mdir / "figures.json", {}) or {}
    alts = {f["file"]: f.get("alt") for f in figdoc.get("figures") or [] if f.get("alt")}
    ok_files = {f["file"] for f in figdoc.get("figures") or [] if (out / f["file"]).is_file()}
    if not figdoc:  # no bundle (rendering was off): trust the files on disk
        ok_files = {f.file for s in steps for f in s.figures if (out / f.file).is_file()}
    if (out / "manual" / "backplane-pcb.png").is_file():
        for s_ in steps:
            if s_.id == "plug-harnesses":
                s_.extra_images.insert(0, "manual/backplane-pcb.png")
    visuals = step_visuals(out, data, steps, ok_files, alts)
    build = read_json(out / "build.json", {}) or {}
    build.setdefault("schema", "gadget-build/1")
    tests = [t for t in (build.get("tests") or []) if isinstance(t, dict)] or build_tests(data, A)
    wiring = build.get("wiring") if isinstance(build.get("wiring"), dict) else build_wiring(data, A, {})
    build["steps"] = json_steps(steps, visuals)
    build["tests"] = tests
    (out / "build.json").write_text(json.dumps(build, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_fragment(out, build)
    md = write_guide(data, A, steps, visuals, tests, wiring, issues)
    (out / "BUILD_GUIDE.md").write_text(md, encoding="utf-8")
    fig_keys = ("labels_placed", "label_overlaps", "label_violations", "labels_dropped", "labels_dropped_priority",
                "figures_failed", "figure_seconds")
    fig = {k: prev.get(k) for k in fig_keys if k in prev}
    keep = ("wiring_overlaps", "wiring_ports_drawn", "module_ends", "qty_mismatches", "mesh_errors", "unrendered_figures", "regenerate")
    extra = {k: prev[k] for k in keep if k in prev}
    extra.update({"harness_module_end_missing": module_end_missing(data, steps),
                  "known_issues": [i.line({}) for i in issues.pairs], "known_issue_banner": issues.banner,
                  "issues_source": issues.source, "finalized": True, "_comps": {**data.comps, **data.subcomps}})
    summary = audit(out, build, md, fig, prev.get("seconds"), extra)
    summary["checks"] = checks_from(summary)
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "manual-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    failed = sum(1 for t in tests if t.get("status") == "failed")
    return {"tests": len(tests), "failed": failed, "banner": issues.banner, "known_issues": len(issues.pairs),
            "steps_with_known_issues": [s.id for s in steps if s.known], "final_checks_match_tests": summary["final_checks_match_tests"],
            "images_png": summary["images_png"], "images_svg": summary["images_svg"], "ok": summary["ok"],
            "file": "BUILD_GUIDE.md"}
