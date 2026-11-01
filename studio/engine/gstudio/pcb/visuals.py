"""Visual outputs: schematic SVG/PDF/PNG, 3D board renders (top/bottom/iso PNG), copper+silk SVG plots, board GLB.

The kicad-cli calls are independent, so they run concurrently (each render is itself multi-threaded).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import kicad_cli, raster
from .design import Design


def _schematic(sch: Path, pcb_dir: Path) -> dict:
    out = {}
    tmp = pcb_dir / "_sch_svg"
    svgs = kicad_cli.sch_svg(sch, tmp)
    if svgs:
        target = pcb_dir / "schematic.svg"
        svgs[0].replace(target)
        out["schematic_svg"] = target.name
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    kicad_cli.sch_pdf(sch, pcb_dir / "schematic.pdf")
    out["schematic_pdf"] = "schematic.pdf"
    png = raster.svg_to_png(pcb_dir / "schematic.svg", pcb_dir / "schematic.png", width=3000)
    if png:
        out["schematic_png"] = png.name
    return out


def make_all(pcb: Path, sch: Path, pcb_dir: Path, design: Design, options: dict) -> dict:
    W, H = design.width, design.height
    q = "basic" if options.get("fast") else "high"
    w = 1600
    h = int(round(w * (H + 12) / (W + 12)))
    jobs = {
        "schematic": lambda: _schematic(sch, pcb_dir),
        "board_top_png": lambda: kicad_cli.render(pcb, pcb_dir / "board-top.png", "top", width=w, height=h, quality=q).name,
        "board_bottom_png": lambda: kicad_cli.render(pcb, pcb_dir / "board-bottom.png", "bottom", width=w, height=h,
                                                     quality=q).name,
        "board_iso_png": lambda: kicad_cli.run(
            ["pcb", "render", "--side", "top", "--width", "1600", "--height", "1200", "--quality", q, "--background",
             "opaque", "--zoom", "0.9", "--rotate", "-40,0,30", "--perspective", "--floor", "-o",
             str(pcb_dir / "board-iso.png"), str(pcb)], timeout=300) and "board-iso.png",
        "board_top_svg": lambda: kicad_cli.pcb_svg(pcb, pcb_dir / "board-top.svg",
                                                   ["F.Cu", "F.Mask", "F.SilkS", "Edge.Cuts"]).name,
        "board_bottom_svg": lambda: kicad_cli.pcb_svg(pcb, pcb_dir / "board-bottom.svg",
                                                      ["B.Cu", "B.Mask", "B.SilkS", "Edge.Cuts"], mirror=True).name,
        "board_glb": lambda: kicad_cli.glb(pcb, pcb_dir / "board.glb").name,
    }
    out: dict = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {k: ex.submit(fn) for k, fn in jobs.items()}
        for k, f in futs.items():
            r = f.result()
            if isinstance(r, dict):
                out.update(r)
            else:
                out[k] = r
    return out


__all__ = ["make_all"]
