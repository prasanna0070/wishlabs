"""gstudio.manual.run(ctx): steps, wiring, tests, illustrations, wiring diagram, BUILD_GUIDE.md, summary."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from .data import ManualData, load
from .figs import TOP_34, FigureSpec, LabelSpec, PanelSpec
from .finals import build_tests
from .guide import write_guide
from .model import ROLE_NOUN, Assembly, anchors_for, classify, role_of_ref
from .steps import FOOTER, Step, plan
from .summary import audit
from .wiring import build_wiring, harnesses

STAGE = "manual"
SHARED_VISUALS = {
    "manual/backplane-pcb.png": ("KiCad render of this build's routed backplane, top side, with the printed port labels.",
                                 "Rendered by the PCB stage from pcb/backplane.kicad_pcb, not a photo of a made board. "
                                 "Compare the printed labels with the port names in this guide."),
    "manual/wiring.png": ("Top view of the backplane with the used ports highlighted, each module's cable drawn in its wire colours, and the pin table.",
                          "Diagram from the saved slot map and electrical data, not a photo. Read the port names on your board; colours are only a cross-check."),
    "manual/ports.png": ("The core cartridge from above with every port this build uses labelled with the module that plugs into it.",
                         "Port positions from the saved CAD and slot map. It does not prove a plug is seated; push each one fully home."),
}


def qty_mismatches(data: ManualData) -> list[dict]:
    """Items whose CAD count differs from spec.derived.hardware (summed per library module). The guide uses the CAD
    count, because that is what the pictures and steps show; a parts list built from the hardware list would differ."""
    from .model import comp_qty

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
            pins = len(h.wires) or None
            labels.append(LabelSpec(text=f"{h.slot}\n{noun}" + (f" ({pins}-pin)" if pins else ""), anchor=key, priority=2))
    pk = next((k for k in anchors if "PWR" in k.upper()), None)
    if pk:
        labels.append(LabelSpec(text="J_PWR\nUSB-C power in", anchor=pk, priority=1))
    if not labels:
        return None
    show = {c.name: "ghost" for c in A.cartridge}  # modules above the board would hide the port tops
    show[bp.name] = "focus"
    return FigureSpec(
        file="manual/ports.png", title="Backplane ports used by this build",
        panels=[PanelSpec(subtitle="Core cartridge seen from above and in front; +X (the gadget's left) is on your right",
                          show=show, view_dir=TOP_34, focus=list(show), labels=labels, label_room=0.27)],
        footer=FOOTER + " | connector latches and pins simplified",
        alt=SHARED_VISUALS["manual/ports.png"][0], caption=SHARED_VISUALS["manual/ports.png"][1],
    )


def frame_pcb(src: Path, dst: Path, product: str) -> bool:
    """Frame pcb/board-top.png like the other manual images (title band + footer)."""
    try:
        from PIL import Image, ImageDraw

        from .illustrate import INK, MUTED, PANEL, PAPER, font

        im = Image.open(src).convert("RGB")
        W = 1400
        scale = min((W - 80) / im.width, 1500 / im.height)
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        H = im.height + 70 + 40 + 46 + 30
        cv = Image.new("RGB", (W, H), PAPER)
        d = ImageDraw.Draw(cv)
        d.rectangle([0, 0, W, 110], fill=PANEL)
        d.text((28, 18), f"Your backplane: the routed board for the {product}", font=font("medium", 31), fill=INK)
        d.text((28, 76), "Top side, USB-C at the rear edge (top of the picture); +X (the gadget's left) is on your right",
               font=font("regular", 21), fill=MUTED)
        cv.paste(im, ((W - im.width) // 2, 125))
        d.rectangle([0, H - 46, W, H], fill=PANEL)
        d.text((28, H - 33), "KiCad 3D render of pcb/backplane.kicad_pcb from the PCB stage | not a photo | not an electrical test",
               font=font("regular", 16), fill=MUTED)
        dst.parent.mkdir(parents=True, exist_ok=True)
        cv.save(dst, "PNG", compress_level=6)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------------- rendering
def _render_chunk(job: tuple) -> list:
    """Worker entry (spawned process): one GL context, a batch of figures."""
    data, A, figs, out, cache, threads = job
    os.environ["LP_NUM_THREADS"] = str(threads)
    from .illustrate import Illustrator
    from .render3d import MeshStore, Renderer

    meshes = mesh_store(data, cache, workers=1)
    meshes.preload(sorted({n.split("#", 1)[0] for _, f in figs for p in f.panels for n in p.show}))
    ill = Illustrator(data, A, meshes, Renderer())
    return [(i, ill.render_figure(f, out)) for i, f in figs]


def mesh_store(data: ManualData, cache, workers: int = 0):
    from .render3d import MeshStore

    routes = {}
    for r in data.routes:
        name = r.component if r.component in data.comps else (r.id if r.id in data.comps else None)
        if name and len(r.points) >= 2:
            routes[name] = (r.points, r.diameter or 3.0)
    return MeshStore(data.brep_dir, cache_dir=cache, workers=workers, glb=data.glb,
                     ref_bboxes={c.name: c.bbox for c in data.comps.values() if c.bbox}, routes=routes,
                     solid_boxes=data.solid_boxes)


def render_all(data: ManualData, A: Assembly, figs: list[FigureSpec], out: Path, cache: Optional[Path], workers: int,
               progress) -> tuple[list, dict]:
    from .render3d import MeshStore

    names = sorted({n.split("#", 1)[0] for f in figs for p in f.panels for n in p.show})
    ms = mesh_store(data, cache)
    t = time.time()
    errs = ms.preload(names)
    progress(f"tessellated {len(names)} components in {time.time() - t:.1f}s" + (f" ({len(errs)} missing)" if errs else ""), 20)
    order = sorted(range(len(figs)), key=lambda i: -len(figs[i].panels))
    workers = max(1, min(workers, len(figs)))
    threads = max(2, (os.cpu_count() or 4) // workers)
    jobs = [(data, A, [(i, figs[i]) for i in order[k::workers]], out, ms.cache_dir, threads) for k in range(workers)]
    results: dict[int, Any] = {}
    if workers == 1:
        chunks = [_render_chunk(jobs[0])]
    else:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        # spawn (see MeshStore.preload): never fork a process that may hold OCCT or GL threads
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            chunks = list(ex.map(_render_chunk, jobs))
    for chunk in chunks:
        for i, r in chunk:
            results[i] = r
    return [results[i] for i in range(len(figs))], errs


# --------------------------------------------------------------------------------------------- run
def run(ctx: Any) -> dict:
    t0 = time.time()

    def progress(msg: str, pct: Optional[float] = None):
        try:
            ctx.progress(STAGE, msg, pct)
        except Exception:
            pass

    data = load(ctx)
    out = Path(ctx.out)
    if not data.comps:
        from ..errors import StageError

        raise StageError("manual needs cad/components.json from the cad stage", {"path": str(out / "cad" / "components.json")})
    opts = getattr(ctx, "options", {}) or {}
    for c in data.comps.values():  # split fastener groups among their owners by solid position
        if c.kind == "PURCHASE" and not c.ref and c.solids > 1 and (data.brep_dir / f"{c.name}.brep").is_file():
            try:
                from .data import solid_bboxes

                data.solid_boxes[c.name] = solid_bboxes(data.brep_dir / f"{c.name}.brep")
            except Exception:
                pass
    A = classify(data)
    steps: list[Step] = plan(A)
    progress(f"planned {len(steps)} assembly steps", 5)

    mdir = out / "manual"
    if mdir.is_dir():
        shutil.rmtree(mdir)
    mdir.mkdir(parents=True, exist_ok=True)

    from .diagram import draw_wiring

    wd = draw_wiring(data, A, mdir)
    pcb_fig = bool(data.pcb_top_png) and frame_pcb(data.pcb_top_png, mdir / "backplane-pcb.png", data.product_name)
    if pcb_fig:
        for s_ in steps:
            if s_.id == "plug-harnesses":
                s_.extra_images.insert(0, "manual/backplane-pcb.png")
    progress(f"wiring diagram: {wd['harnesses']} cables, {wd['rows']} wires", 10)

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
    results, mesh_errs = [], {}
    if not opts.get("manual_no_render"):
        workers = int(opts.get("manual_workers") or min(8, max(1, (os.cpu_count() or 2) // 2)))
        cache = opts.get("mesh_cache")
        results, mesh_errs = render_all(data, A, figs, out, Path(cache) if cache else None, workers, progress)
        progress(f"rendered {sum(1 for r in results if r.ok)}/{len(figs)} illustrations", 85)
    ok_files = {r.file for r in results if r.ok}
    fig_by_file = {f.file: f for f in figs}

    # ---- build.json sections
    bboxes = {c.name: c.bbox for c in [*data.comps.values(), *data.subcomps.values()] if c.bbox}
    visuals: dict[str, list[dict]] = {}
    jsteps = []
    for s in steps:
        vs = [{"path": f.file, "alt": f.alt, "caption": f.caption} for f in s.figures if f.file in ok_files]
        for p in s.extra_images:
            if (out / p).is_file():
                alt, cap = SHARED_VISUALS.get(p, (p, ""))
                vs.append({"path": p, "alt": alt, "caption": cap})
        visuals[s.id] = vs
        jsteps.append({"id": s.id, "title": s.title, "instruction": s.instruction, "expected": s.expected,
                       "parts": s.parts, "highlight": s.highlight, "camera": s.camera, "visuals": vs})
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
    build = {}
    if build_path.is_file():
        try:
            build = json.loads(build_path.read_text(encoding="utf-8"))
        except ValueError:
            build = {}
    build.setdefault("schema", "gadget-build/1")
    build.update({"steps": jsteps, "wiring": wiring, "tests": tests})
    build_path.write_text(json.dumps(build, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = write_guide(data, A, steps, visuals, tests, wiring)
    (out / "BUILD_GUIDE.md").write_text(md, encoding="utf-8")

    summary = audit(out, build, md, results, time.time() - t0)
    summary["qty_mismatches"] = qty_mismatches(data)
    summary["mesh_errors"] = mesh_errs
    summary["figure_seconds"] = {r.file: round(r.seconds, 2) for r in results}
    summary["unrendered_figures"] = sorted(set(fig_by_file) - ok_files)
    (mdir / "manual-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    arts = getattr(ctx, "artifacts", None)
    if isinstance(arts, dict):
        arts["manual.summary"] = str(mdir / "manual-summary.json")
        arts["manual.guide"] = str(out / "BUILD_GUIDE.md")
        arts["manual.build_sections"] = ["steps", "wiring", "tests"]
    for r in results:
        if not r.ok and hasattr(ctx, "warn"):
            ctx.warn(STAGE, f"{r.file}: {r.error}")
    progress(f"manual done in {summary['seconds']}s: {summary['images_rendered']} images, "
             f"{len(summary['orphans'])} orphans, {summary['label_overlaps']} label overlaps", 100)
    return summary
