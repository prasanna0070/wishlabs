"""Regenerable manual images (MAJ-27): manual/figures.json + manual/render_manual.py (+ manual/render-requirements.txt).

figures.json holds every figure the stage rendered (FigureSpec as JSON), the Scene (component colours, boxes,
quantities, anchors and their owners), the mesh sources (cad/components/*.brep, cad/assembly.glb, cable routes) and
the 2D drawings (wiring diagram and cable ends, as the recorded Canvas operations). render_manual.py embeds the exact
renderer modules the stage used (geom, scene, figs, labels, render3d, illustrate, draw) and re-renders every image from
the export; `--compare` measures SSIM against the shipped PNGs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .figs import FigureSpec, figure_to_json
from .scene import Scene

BUNDLED = ["geom", "scene", "figs", "labels", "render3d", "illustrate", "draw"]
PKG = Path(__file__).resolve().parent
REQ_DISTS = [("numpy", "numpy"), ("pillow", "PIL"), ("scipy", "scipy"), ("vtk", "vtk"), ("cadquery-ocp", "OCP"),
             ("pygltflib", "pygltflib")]


def _rel(p: str, root: Path) -> str:
    try:
        return Path(p).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)


def _jsonable(x: Any) -> Any:
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return round(x, 4)
    return x


def requirements() -> list[str]:
    """Exact versions of the renderer's dependencies in the environment that rendered the shipped images."""
    from importlib import metadata

    out = []
    for dist, _mod in REQ_DISTS:
        for name in (dist, dist.replace("-", "_")):
            try:
                out.append(f"{dist}=={metadata.version(name)}")
                break
            except metadata.PackageNotFoundError:
                continue
        else:
            if dist == "cadquery-ocp":
                try:
                    out.append(f"cadquery-ocp=={metadata.version('cadquery_ocp')}")
                except metadata.PackageNotFoundError:
                    out.append("cadquery-ocp  # version not found in the build environment")
    return out


def write_figures_json(out: Path, figs: list[FigureSpec], scene: Scene, mesh: dict, canvases: dict, framed: list[dict],
                       alts: dict[str, str]) -> Path:
    fj = []
    for f in figs:
        d = figure_to_json(f)
        if f.file in alts:
            d["alt"] = alts[f.file]
        for p in d["panels"]:
            for m in p["meshes"]:
                if not str(m["path"]).startswith("glyph:"):
                    m["path"] = _rel(m["path"], out)
        fj.append(d)
    doc = {
        "schema": "gadget-manual-figures/1",
        "renderer": "manual/render_manual.py",
        "requirements": requirements(),
        "mesh": mesh,
        "scene": scene.to_json(),
        "figures": fj,
        "canvases": [{"file": rel, "w": cv.w, "h": cv.h, "bg": list(cv.bg), "ops": _jsonable(cv.ops),
                      "svg": rel[:-4] + ".svg" if (out / (rel[:-4] + ".svg")).is_file() else None}
                     for rel, cv in sorted(canvases.items())],
        "framed": framed,
    }
    p = out / "manual" / "figures.json"
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


TEMPLATE = r'''#!/usr/bin/env python3
"""Re-render this gadget's build-manual images from the delivered files (no Wish Labs install needed).

    python manual/render_manual.py                 # re-render every image into ./manual (overwrites)
    python manual/render_manual.py --out /tmp/x    # render into another folder
    python manual/render_manual.py --out /tmp/x --compare   # also report SSIM against the shipped images

Reads manual/figures.json and cad/components/*.brep (plus cad/assembly.glb, print/*.stl, pcb/board-top.png).
The renderer below is the exact code the build used, embedded as modules of a private package.
Requirements (the versions that rendered the shipped images): __REQS__
VTK renders off-screen (EGL, or OSMesa/X). Fonts: Inter (else DejaVu Sans) from the usual system font folders;
a different font changes text widths, so a machine without Inter will not match the shipped images exactly.
"""

import argparse
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path

PKG = "gs_manual_render"
SOURCES = __SOURCES__


class _Loader(importlib.abc.Loader):
    def __init__(self, name):
        self.name = name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        short = module.__name__.split(".", 1)[1] if "." in module.__name__ else ""
        code = SOURCES[short] if short else ""
        exec(compile(code, f"<{module.__name__}>", "exec"), module.__dict__)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == PKG:
            return importlib.util.spec_from_loader(fullname, _Loader(fullname), is_package=True)
        if fullname.startswith(PKG + ".") and fullname.split(".", 1)[1] in SOURCES:
            return importlib.util.spec_from_loader(fullname, _Loader(fullname))
        return None


sys.meta_path.insert(0, _Finder())


def _tup(x):
    if isinstance(x, list):
        return tuple(_tup(v) for v in x)
    return x


def ssim(a, b):
    import numpy as np
    from scipy.ndimage import gaussian_filter

    if a.shape != b.shape:
        return 0.0
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = gaussian_filter(a, 1.5), gaussian_filter(b, 1.5)
    va = gaussian_filter(a * a, 1.5) - mu_a ** 2
    vb = gaussian_filter(b * b, 1.5) - mu_b ** 2
    cov = gaussian_filter(a * b, 1.5) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(m.mean())


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Re-render the build-manual images from the delivered files.")
    ap.add_argument("--root", default=str(here.parent), help="delivered project folder (default: this file's parent's parent)")
    ap.add_argument("--out", default=None, help="output root (default: the project folder itself)")
    ap.add_argument("--compare", action="store_true", help="report SSIM of each re-rendered PNG against the shipped one")
    ap.add_argument("--min-ssim", type=float, default=0.98)
    ap.add_argument("--cache", default=None, help="mesh cache folder")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    out = Path(a.out).resolve() if a.out else root
    doc = json.loads((root / "manual" / "figures.json").read_text(encoding="utf-8"))
    from gs_manual_render.draw import Canvas
    from gs_manual_render.figs import figure_from_json
    from gs_manual_render.illustrate import Illustrator, frame_pcb
    from gs_manual_render.render3d import MeshStore, Renderer
    from gs_manual_render.scene import Scene

    done = []
    for c in doc.get("canvases") or []:
        cv = Canvas(int(c["w"]), int(c["h"]), bg=_tup(c["bg"]), ops=[_tup(op) for op in c["ops"]])
        cv.to_png(out / c["file"])
        if c.get("svg"):
            (out / c["svg"]).write_text(cv.to_svg(), encoding="utf-8")
        done.append(c["file"])
    for f in doc.get("framed") or []:
        if frame_pcb(root / f["src"], out / f["file"], f["product"]):
            done.append(f["file"])
    m = doc.get("mesh") or {}
    scene = Scene.from_json(doc["scene"])
    figs = [figure_from_json(f, rebase=lambda p: p if p.startswith("glyph:") else str(root / p)) for f in doc["figures"]]
    if figs:
        routes = {k: ([tuple(p) for p in v[0]], float(v[1])) for k, v in (m.get("routes") or {}).items()}
        store = MeshStore(root / m.get("brep_dir", "cad/components"), cache_dir=Path(a.cache) if a.cache else None,
                          glb=(root / m["glb"]) if m.get("glb") else None,
                          ref_bboxes={k: tuple(v) for k, v in (m.get("ref_bboxes") or {}).items()}, routes=routes,
                          solid_boxes={k: [tuple(b) for b in v] for k, v in (m.get("solid_boxes") or {}).items()})
        if m.get("glb") and store.glb is None:
            print("WARNING: cad/assembly.glb could not be read (" + str(store.errors.get("assembly.glb")) + "); "
                  "falling back to BREP meshes, so part colours will differ from the shipped images", file=sys.stderr)
        store.preload(sorted({n.split("#", 1)[0] for f in figs for p in f.panels for n in p.show}))
        ill = Illustrator(scene, store, Renderer())
        for f in figs:
            r = ill.render_figure(f, out)
            if r.ok:
                done.append(f.file)
            else:
                print(f"FAILED {f.file}: {r.error}", file=sys.stderr)
    print(f"rendered {len(done)} images into {out}")
    if not a.compare:
        return 0
    import numpy as np
    from PIL import Image

    worst, bad = 1.0, []
    for rel in sorted(x for x in done if x.endswith(".png")):
        shipped = root / rel
        if not shipped.is_file():
            print(f"{rel}: no shipped image to compare")
            bad.append(rel)
            continue
        s = ssim(np.asarray(Image.open(shipped).convert("L")), np.asarray(Image.open(out / rel).convert("L")))
        worst = min(worst, s)
        print(f"{rel}: SSIM {s:.4f}")
        if s < a.min_ssim:
            bad.append(rel)
    print(f"compared {len([x for x in done if x.endswith('.png')])} PNGs; worst SSIM {worst:.4f}; below {a.min_ssim}: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def write_renderer(out: Path) -> tuple[Path, Path]:
    sources = {m: (PKG / f"{m}.py").read_text(encoding="utf-8") for m in BUNDLED}
    reqs = requirements()
    code = TEMPLATE.replace("__REQS__", ", ".join(reqs)).replace("__SOURCES__", repr(sources))
    p = out / "manual" / "render_manual.py"
    p.write_text(code, encoding="utf-8")
    r = out / "manual" / "render-requirements.txt"
    r.write_text("# exact versions of the packages that rendered the shipped manual images\n" + "\n".join(reqs) + "\n", encoding="utf-8")
    return p, r
