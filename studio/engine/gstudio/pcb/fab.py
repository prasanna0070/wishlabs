"""Fabrication outputs: Gerbers + Excellon drill (zipped for JLCPCB / PCBPower-class fabs), BOM and CPL."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from . import kicad_cli
from .design import Design, _natkey

GERBER_LAYERS = ["F.Cu", "B.Cu", "F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"]
EXPECTED_SUFFIXES = {"F.Cu": "-F_Cu.gtl", "B.Cu": "-B_Cu.gbl", "F.Mask": "-F_Mask.gts", "B.Mask": "-B_Mask.gbs",
                     "F.SilkS": "-F_Silkscreen.gto", "B.SilkS": "-B_Silkscreen.gbo", "F.Paste": "-F_Paste.gtp",
                     "B.Paste": "-B_Paste.gbp", "Edge.Cuts": "-Edge_Cuts.gm1"}


def gerbers(pcb: Path, pcb_dir: Path) -> dict:
    gdir = pcb_dir / "gerbers"
    if gdir.exists():
        for f in gdir.iterdir():
            f.unlink()
    kicad_cli.gerbers(pcb, gdir, GERBER_LAYERS)
    kicad_cli.drill(pcb, gdir)
    files = sorted(p for p in gdir.iterdir() if p.is_file())
    zpath = pcb_dir / "backplane-gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            zi = zipfile.ZipInfo(f.name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, f.read_bytes())
    return {"dir": str(gdir), "zip": str(zpath), "files": [f.name for f in files]}


def zip_layers(zpath: Path) -> dict[str, bool]:
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
    have = {layer: any(n.endswith(suf) for n in names) for layer, suf in EXPECTED_SUFFIXES.items()}
    have["drill_pth"] = any(n.endswith("-PTH.drl") for n in names)
    have["drill_npth"] = any(n.endswith("-NPTH.drl") for n in names)
    return have


def _fitted(p) -> str:
    fp = p.footprint
    if any(k in fp for k in ("PinSocket", "JST", "PinHeader")):
        return "THT (hand/selective solder)"
    return "SMT"


def bom(design: Design, path: Path) -> dict:
    groups: dict[tuple, list] = {}
    for p in design.parts:
        if not p.in_bom or p.board_only:
            continue
        key = (p.value, p.footprint, p.bom.get("mpn", ""), p.bom.get("description", ""))
        groups.setdefault(key, []).append(p.ref)
    rows = []
    for (value, fp, mpn, desc), refs in sorted(groups.items(), key=lambda kv: _natkey(sorted(kv[1], key=_natkey)[0])):
        refs = sorted(refs, key=_natkey)
        part = design.part(refs[0])
        rows.append({
            "Item": len(rows) + 1, "Qty": len(refs), "References": " ".join(refs), "Value": value,
            "Footprint": fp, "Description": desc, "MPN (suggested)": mpn,
            "LCSC part": "to select (estimate)",
            "India sourcing": "to source; not checked (estimate)",
            "Fitting": _fitted(part),
            "Backplane-v1 spec": part.bom.get("backplane_value", ""),
        })
    rows.append({"Item": len(rows) + 1, "Qty": 1, "References": "PCB", "Value": f"{design.backplane_id} bare board",
                 "Footprint": "", "Description": f"2-layer FR-4 {design.thickness} mm, 1 oz, {design.width} x {design.height} mm, "
                 "lead-free HASL, blue mask, white silk", "MPN (suggested)": "fab: JLCPCB / PCBPower class",
                 "LCSC part": "", "India sourcing": "PCBPower or similar (quote not taken; estimate)",
                 "Fitting": "", "Backplane-v1 spec": ""})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue())
    return {"lines": len(rows), "parts": sum(r["Qty"] for r in rows[:-1])}


def cpl(pcb: Path, pcb_dir: Path, design: Design) -> dict:
    raw = pcb_dir / "pos-kicad.csv"
    kicad_cli.pos(pcb, raw)
    rows = list(csv.DictReader(raw.read_text().splitlines()))
    out = pcb_dir / "cpl.csv"
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"])
    n = 0
    for r in sorted(rows, key=lambda r: _natkey(r.get("Ref", ""))):
        ref = r.get("Ref", "")
        w.writerow([ref, r.get("Val", ""), r.get("Package", ""), f"{float(r.get('PosX', 0)):.4f}mm",
                    f"{float(r.get('PosY', 0)):.4f}mm", f"{float(r.get('Rot', 0)):.1f}",
                    "Top" if r.get("Side", "top").lower().startswith("top") else "Bottom"])
        n += 1
    out.write_text(buf.getvalue())
    raw.unlink()
    return {"placements": n,
            "note": "KiCad footprint origins and rotations; a fab may need per-package rotation offsets (check their preview)."}


__all__ = ["gerbers", "zip_layers", "bom", "cpl", "GERBER_LAYERS"]
