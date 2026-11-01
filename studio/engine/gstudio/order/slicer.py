"""Slice printed parts with the PrusaSlicer CLI and read grams, hours and support use from the G-code.

The G-code summary parsing reuses the regexes of ../sim/run_sim.py (`parse_slice`). On top of that we sum the
filament extruded per `;TYPE:` feature, so support material is measured, not guessed.

Nothing here invents a number: a part that cannot be sliced is reported with `ok: false` and the reason.
"""
from __future__ import annotations

import concurrent.futures as cf
import math
import os
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

INI = Path(__file__).with_name("slice.ini")

# ------------------------------------------------------------------ profile


def read_profile(ini: Path = INI) -> dict:
    """key = value pairs of a PrusaSlicer ini (comments dropped)."""
    out: dict = {}
    for line in Path(ini).read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def bed_from_profile(prof: dict) -> tuple[float, float, float]:
    """(x, y, z) build volume in mm from bed_shape + max_print_height."""
    pts = [tuple(float(c) for c in p.split("x")) for p in prof.get("bed_shape", "0x0,250x0,250x210,0x210").split(",")]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys), float(prof.get("max_print_height", 210)))


PRINTER_CLASS = "Prusa i3 MK3S-class FDM printer"  # its published build volume is 250 x 210 x 210 mm, as in slice.ini


def printer_profile(prof: dict | None = None) -> dict:
    """MIN-12: the named target printer the bed-fit check and the guide use (bed and nozzle from slice.ini)."""
    prof = prof if prof is not None else read_profile()
    bed = bed_from_profile(prof)
    return {"name": f"{PRINTER_CLASS}, {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm build volume, {prof.get('nozzle_diameter', '0.4')} mm nozzle",
            "class": PRINTER_CLASS, "build_volume_mm": list(bed), "nozzle_mm": float(prof.get("nozzle_diameter", 0.4)),
            "profile_file": "print/slice.ini"}


def profile_material(prof: dict | None = None) -> str:
    """The one print material every part is sliced, priced and described with (slice.ini filament_type)."""
    prof = prof if prof is not None else read_profile()
    return str(prof.get("filament_type") or "PETG")


def profile_summary(prof: dict) -> str:
    return (f"{prof.get('nozzle_diameter', '?')} mm nozzle, {prof.get('layer_height', '?')} mm layers, "
            f"{prof.get('perimeters', '?')} perimeters, {prof.get('fill_density', '?')} {prof.get('fill_pattern', '')}, "
            f"{prof.get('filament_type', 'PETG')}, supports "
            + ("auto from the build plate only" if prof.get("support_material_buildplate_only") == "1" else "auto"))


# ------------------------------------------------------------------ slicer binary


def find_slicer() -> str | None:
    for c in (os.environ.get("GS_PRUSA_SLICER"), "prusa-slicer", "PrusaSlicer", "prusa-slicer-console"):
        if c:
            p = shutil.which(c)
            if p:
                return p
    return None


def slicer_version(exe: str) -> str:
    try:
        r = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=60)
        m = re.search(r"PrusaSlicer-([\w.+-]+)", r.stdout + r.stderr)
        return f"PrusaSlicer {m.group(1).split('+')[0]}" if m else "PrusaSlicer (version unknown)"
    except Exception:  # noqa: BLE001 - version string is informational only
        return "PrusaSlicer (version unknown)"


# ------------------------------------------------------------------ STL reading


def read_stl_triangles(path: str | Path):
    """Return a list (or numpy array) of triangles [[v0, v1, v2], ...] from an ASCII or binary STL."""
    data = Path(path).read_bytes()
    is_ascii = data[:5] == b"solid" and b"facet" in data[:2000]
    if not is_ascii and len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 != len(data) and b"facet" in data[:2000]:
            is_ascii = True
    try:
        import numpy as np  # fast path on the builder
    except ImportError:  # pragma: no cover - the builder always has numpy
        np = None
    if is_ascii:
        verts = [tuple(float(v) for v in ln.split()[1:4])
                 for ln in data.decode("ascii", "ignore").splitlines() if ln.strip().startswith("vertex")]
        tris = [verts[i:i + 3] for i in range(0, len(verts) - len(verts) % 3, 3)]
        return np.asarray(tris, dtype=float).reshape(-1, 3, 3) if np is not None else tris
    n = struct.unpack_from("<I", data, 80)[0]
    if np is not None:
        rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        arr = np.frombuffer(data, dtype=rec, count=n, offset=84)
        return arr["v"].astype(float)
    tris = []
    for i in range(n):
        v = struct.unpack_from("<9f", data, 84 + i * 50 + 12)
        tris.append([v[0:3], v[3:6], v[6:9]])
    return tris


def stl_info(path: str | Path) -> dict:
    """Triangle count, bounds and signed-tetrahedron volume (mm3) of an STL."""
    t = read_stl_triangles(path)
    try:
        import numpy as np

        t = np.asarray(t, dtype=float).reshape(-1, 3, 3)
        if len(t) == 0:
            raise ValueError("empty STL")
        lo, hi = t.reshape(-1, 3).min(0), t.reshape(-1, 3).max(0)
        vol = float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)
        lo, hi = [float(x) for x in lo], [float(x) for x in hi]
        n = int(len(t))
    except ImportError:  # pragma: no cover
        if not t:
            raise ValueError("empty STL")
        pts = [p for tri in t for p in tri]
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        vol = sum(a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
                  for a, b, c in t) / 6.0
        n = len(t)
    return {"triangles": n, "bounds_min": lo, "bounds_max": hi,
            "size_mm": [round(hi[i] - lo[i], 3) for i in range(3)], "volume_mm3": round(abs(vol), 1)}


def bed_fit(size_mm: list[float], bed: tuple[float, float, float]) -> tuple[bool, int]:
    """(fits, rotate_z_deg). Tries the part as placed, then turned 90 degrees about Z."""
    x, y, z = size_mm
    if z > bed[2]:
        return False, 0
    if x <= bed[0] and y <= bed[1]:
        return True, 0
    if y <= bed[0] and x <= bed[1]:
        return True, 90
    return False, 0


# ------------------------------------------------------------------ print orientation (live failure 2026-11-02)
# A part that does not fit as exported (e.g. a 212.7 mm tall shell on a 210 mm Z printer) is tipped 90 degrees about X or
# Y, then turned about Z if that helps, so its longest side lies along the 250 mm axis. Angles follow the right-hand rule
# about the CAD axes (+Z up, front faces -Y, +X is the robot's left), as PrusaSlicer's --rotate-x / --rotate-y apply them.
TRAPPED_LIMIT_MM2 = 500.0  # above this a tipped part is sliced with TIPPED_OVERRIDE
# PrusaSlicer leaves bridges unsupported by default (dont_support_bridges = 1) and slice.ini builds supports from the plate
# only; a ceiling over the part's own cavity needs both relaxed or it prints as a long unsupported bridge
TIPPED_OVERRIDE = {"support_material_buildplate_only": "0", "dont_support_bridges": "0"}
# Every tipped pose is one the CAD did not design for, so none may price a long span as a bridge in mid air: with the stock
# profile an open-rear 120 x 80 x 212.7 mm box laid on its open back sliced to 0 g of support (its 115 x 208 mm front wall
# "bridged") and beat face down (dev VM, 2026-11-02). Tipped parts are always sliced with supports under bridges.
BRIDGE_OVERRIDE = {"dont_support_bridges": "0"}
TIPS = (
    # key, rotate_x_deg, rotate_y_deg, plain words (which face goes on the bed)
    ("rear_down", -90, 0, "lying on its back, the rear face on the bed"),
    ("left_down", 0, 90, "lying on its left side (the robot's left, +X) on the bed"),
    ("right_down", 0, -90, "lying on its right side (the robot's right, -X) on the bed"),
    ("front_down", 90, 0, "lying face down, the front on the bed"),
)


def _rotation(rx_deg: float, ry_deg: float, rz_deg: float = 0.0):
    """3x3 matrix applying rotate X, then Y, then Z (right-hand rule, degrees)."""
    import numpy as np

    def R(axis: int, deg: float):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        c, s = round(c, 12), round(s, 12)
        m = np.eye(3)
        i, j = [(1, 2), (2, 0), (0, 1)][axis]
        m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
        return m
    return R(2, rz_deg) @ R(1, ry_deg) @ R(0, rx_deg)


def bed_contact_mm2(tris) -> float:
    """Area of the flat triangles lying on the bed (within 0.2 mm of the lowest point, normal within 8 deg of vertical)."""
    import numpy as np

    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
    zmin = float(t[:, :, 2].min())
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    a = np.linalg.norm(n, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        nz = np.abs(n[:, 2]) / np.where(a > 0, a, 1.0)
    flat = (nz > 0.99) & (t[:, :, 2].max(axis=1) < zmin + 0.2)
    return round(float(a[flat].sum() / 2.0), 1)


def overhang_stats(tris, cell: float = 3.0, per_mm2: float = 1.0, min_drop: float = 1.0, steep: float = 0.77) -> dict:
    """Mesh estimates for one print orientation (not slicer results):
      trapped_overhang_mm2  down-facing area flatter than 40 deg from horizontal (the CAD designs 45 deg roofs to print
                            without support, so those do not count) with part material under it (an
                            up-facing surface lower in the same column); the
                            profile builds supports from the build plate only, so this prints as an unsupported ceiling
      support_column_cm3    the other down-facing area times its height over the bed: a proxy for support material
    Surfaces are sampled at about `per_mm2` points per mm2 (every triangle at least once) and binned on a `cell` mm X/Y grid,
    so each cell holds about nine samples of every surface crossing it."""
    import numpy as np

    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    a2 = np.linalg.norm(n, axis=1)
    keep = a2 > 1e-12
    t, n, a2 = t[keep], n[keep], a2[keep]
    if len(t) == 0:
        return {"trapped_overhang_mm2": 0.0, "support_column_cm3": 0.0}
    area = a2 / 2.0
    per_mm2 = min(per_mm2, 3_000_000 / max(float(area.sum()), 1.0))  # bounded work for very large parts
    k = np.maximum(1, np.ceil(area * per_mm2)).astype(int)
    idx = np.repeat(np.arange(len(t)), k)
    rng = np.random.default_rng(0)
    u, v = rng.random(len(idx)), rng.random(len(idx))
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    T = t[idx]
    P = T[:, 0] + (T[:, 1] - T[:, 0]) * u[:, None] + (T[:, 2] - T[:, 0]) * v[:, None]
    w = (area / k)[idx]
    nz = (n[:, 2] / a2)[idx]
    zmin = float(P[:, 2].min())
    ij = np.floor((P[:, :2] - P[:, :2].min(0)) / cell).astype(np.int64)
    key = ij[:, 0] * (int(ij[:, 1].max()) + 1) + ij[:, 1]
    uniq, inv = np.unique(key, return_inverse=True)
    # part material under a point shows as an up-facing surface below it in the same column (the top of what is below);
    # counting any surface would let a sloped overhang shadow itself inside one cell
    up = nz > 0.2
    low = np.full(len(uniq), np.inf)
    np.minimum.at(low, inv[up], P[up, 2])
    down = (nz < -steep) & (P[:, 2] > zmin + 0.2)
    trapped = down & (low[inv] < P[:, 2] - min_drop)
    free = down & ~trapped
    return {"trapped_overhang_mm2": round(float(w[trapped].sum()), 1),
            "support_column_cm3": round(float((w[free] * (P[free, 2] - zmin)).sum()) / 1000.0, 1)}


def choose_orientation(tris, bed: tuple[float, float, float]) -> dict:
    """The print orientation for a part: as exported (turned about Z if needed) when that fits, otherwise the 90 degree tip
    about X or Y that fits with, in order: the least overhang trapped over the part itself (supports come from the build
    plate only), the least support column under the rest, the largest flat area on the bed (ties: rear, left, right,
    front). Estimates from the mesh; the slicer then measures the real supports. Never invents a fit: `fits` is False when
    no tip fits either."""
    import numpy as np

    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
    size = (t.reshape(-1, 3).max(0) - t.reshape(-1, 3).min(0)).tolist()
    ok, rz = bed_fit(size, bed)
    if ok:
        return {"key": "as_exported", "tipped": False, "fits": True, "rotate_x_deg": 0, "rotate_y_deg": 0, "rotate_z_deg": rz,
                "label": "as exported" + (", turned 90 deg about Z" if rz else ""), "printed_size_mm": [round(v, 1) for v in
                                                                                                     (size if not rz else [size[1], size[0], size[2]])]}
    cands = []
    for key, rx, ry, words in TIPS:
        R = _rotation(rx, ry)
        t2 = t @ R.T
        sz = (t2.reshape(-1, 3).max(0) - t2.reshape(-1, 3).min(0)).tolist()
        fits, rz2 = bed_fit(sz, bed)
        printed = [sz[1], sz[0], sz[2]] if rz2 else sz
        c = {"key": key, "tipped": True, "fits": fits, "rotate_x_deg": rx, "rotate_y_deg": ry, "rotate_z_deg": rz2, "label": words,
             "printed_size_mm": [round(v, 1) for v in printed]}
        if fits:
            c["bed_contact_mm2"] = bed_contact_mm2(t2)
            c.update(overhang_stats(t2))
        cands.append(c)
    fitting = [c for c in cands if c["fits"]]
    shown = ("key", "fits", "rotate_x_deg", "rotate_y_deg", "rotate_z_deg", "label", "printed_size_mm", "trapped_overhang_mm2",
             "support_column_cm3", "bed_contact_mm2")
    if not fitting:
        return {"key": "none", "tipped": False, "fits": False, "rotate_x_deg": 0, "rotate_y_deg": 0, "rotate_z_deg": 0,
                "label": "no orientation fits", "printed_size_mm": [round(v, 1) for v in size],
                "candidates": [{k: c.get(k) for k in shown if k in c} for c in cands]}

    def near_best(cs, key, rel, floor):  # keep the candidates within a tolerance of the best value of `key`
        lo = min(c[key] for c in cs)
        tol = max(floor, rel * max(c[key] for c in cs))
        return [c for c in cs if c[key] <= lo + tol]
    pool = near_best(fitting, "trapped_overhang_mm2", 0.02, 200.0)
    pool = near_best(pool, "support_column_cm3", 0.05, 5.0)
    best = dict(max(pool, key=lambda c: (c["bed_contact_mm2"], -cands.index(c))))
    best["candidates"] = [{k: c.get(k) for k in shown if k in c} for c in cands]
    best["basis"] = ("least overhang trapped over the part (supports from the build plate only), then the least support column, then "
                     "the largest flat area on the bed; mesh estimates, the slicer then measures the real supports")
    return best


def orient_mesh(tris, o: dict):
    """The triangles rotated as `o` says, resting on Z=0 and centred on X/Y = 0 (the way the CAD exports its STLs)."""
    import numpy as np

    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3) @ _rotation(o["rotate_x_deg"], o["rotate_y_deg"], o["rotate_z_deg"]).T
    v = t.reshape(-1, 3)
    lo, hi = v.min(0), v.max(0)
    shift = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return t + shift


def write_stl(path: str | Path, tris) -> Path:
    """Binary STL of triangles [[v0, v1, v2], ...] (facet normals recomputed)."""
    import numpy as np

    t = np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(t), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = n, t
    path = Path(path)
    with open(path, "wb") as fh:
        fh.write(b"Gadget Studio oriented print mesh".ljust(80, b" "))
        fh.write(struct.pack("<I", len(t)))
        fh.write(rec.tobytes())
    return path


def orientation_sentence(o: dict, as_exported_mm: list[float], bed: tuple[float, float, float], supports: bool | None = None,
                         support_g: float | None = None, supports_on_part: bool = False) -> str:
    """Plain words for a tipped part: how it lies, whether the slicer added supports, the rotation to apply, and why."""
    turns = []
    if o.get("rotate_x_deg"):
        turns.append(f"{o['rotate_x_deg']:+d} deg about X")
    if o.get("rotate_y_deg"):
        turns.append(f"{o['rotate_y_deg']:+d} deg about Y")
    if o.get("rotate_z_deg"):
        turns.append(f"then {o['rotate_z_deg']} deg about Z")
    x, y, z = as_exported_mm
    why = (f"as exported it is {z:.1f} mm tall and the printer's Z is {bed[2]:.0f} mm" if z > bed[2]
           else f"as exported it is {x:.1f} x {y:.1f} mm on a {bed[0]:.0f} x {bed[1]:.0f} mm bed")
    ps = o.get("printed_size_mm") or []
    sup = " with supports" if supports else " and the slicer did not add support material" if supports is False else ""
    trapped = float(o.get("trapped_overhang_mm2") or 0)
    inside = (f". About {trapped / 100:.0f} cm2 then faces down over the part itself (mesh estimate), so supports must also stand "
              "on the part there (this part is sliced with supports allowed on the part and under bridges): remove them from "
              "inside and check that surface" if supports_on_part else
              f". About {trapped / 100:.0f} cm2 then faces down over the part itself (mesh estimate), where supports from the build "
              "plate cannot reach: check it in the sliced preview" if trapped > TRAPPED_LIMIT_MM2 else "")
    return (f"prints on its side, not upright{sup}: {o['label']}. In your slicer rotate it {', '.join(turns)} (right-hand rule, "
            f"it then takes {' x '.join(f'{v:.1f}' for v in ps)} mm of the build volume) because {why}{inside}")


# ------------------------------------------------------------------ G-code parsing

_E = re.compile(r"\bE(-?\d*\.?\d+)")


def parse_time(text: str) -> float | None:
    """'8h 35m 26s' / '1d 2h 3m 4s' / '45m 2s' -> hours (as in run_sim.parse_slice)."""
    m = re.search(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(\d+)s", text)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return round(d * 24 + h + mi / 60 + s / 3600, 3)


def parse_gcode_summary(text: str) -> dict:
    """The PrusaSlicer end-of-file comment block, parsed the way ../sim/run_sim.py does."""
    out: dict = {}
    for key, pat in (("filament_g", r"filament used \[g\] = ([\d.]+)"),
                     ("filament_cm3", r"filament used \[cm3\] = ([\d.]+)"),
                     ("filament_mm", r"filament used \[mm\] = ([\d.]+)"),
                     ("filament_cost", r"total filament cost = ([\d.]+)")):
        m = re.search(pat, text)
        if m:
            out[key] = float(m.group(1))
    m = re.search(r"estimated printing time \(normal mode\) = ([^\n]+)", text)
    if m:
        out["print_time"] = m.group(1).strip()
        out["print_h"] = parse_time(m.group(1))
    return out


def parse_gcode(path: str | Path, filament_diameter: float = 1.75, density: float = 1.27) -> dict:
    """Stream a G-code file: summary block + filament per feature type + layer count + top Z."""
    by_type: dict[str, float] = {}
    cur = "Unknown"
    relative = False
    last_e = 0.0
    layers = 0
    max_z = 0.0
    tail: list[str] = []
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            c = line[:1]
            if c == ";":
                if line.startswith(";TYPE:"):
                    cur = line[6:].strip()
                elif line.startswith(";LAYER_CHANGE"):
                    layers += 1
                elif line.startswith(";Z:"):
                    try:
                        max_z = max(max_z, float(line[3:]))
                    except ValueError:
                        pass
                if "=" in line:
                    tail.append(line)
                    if len(tail) > 400:
                        tail = tail[-300:]
                continue
            if c == "M":
                if line.startswith("M83"):
                    relative = True
                elif line.startswith("M82"):
                    relative = False
                continue
            if c == "G":
                if line.startswith("G92"):
                    m = _E.search(line)
                    if m:
                        last_e = float(m.group(1))
                    continue
                if line.startswith(("G1 ", "G0 ", "G1\t", "G2 ", "G3 ")):
                    m = _E.search(line.split(";", 1)[0])
                    if not m:
                        continue
                    e = float(m.group(1))
                    d = e if relative else e - last_e
                    if not relative:
                        last_e = e
                    by_type[cur] = by_type.get(cur, 0.0) + d
    summary = parse_gcode_summary("".join(tail))
    area = math.pi * (filament_diameter / 2) ** 2  # mm2
    to_g = lambda mm: mm * area / 1000.0 * density  # noqa: E731
    support_mm = sum(v for k, v in by_type.items() if k.lower().startswith("support"))
    total_mm = sum(v for v in by_type.values() if v > 0)
    summary.update({
        "layers": layers,
        "top_z_mm": round(max_z, 3),
        "by_type_mm": {k: round(v, 1) for k, v in sorted(by_type.items())},
        "support_mm": round(support_mm, 1),
        "support_g": round(to_g(support_mm), 2),
        "supports": support_mm > 0.5,
        "support_fraction": round(support_mm / total_mm, 4) if total_mm > 0 else 0.0,
    })
    return summary


# ------------------------------------------------------------------ slicing


def slice_part(stl: Path, work: Path, ini: Path = INI, exe: str | None = None, timeout: float = 900,
               bed: tuple[float, float, float] | None = None, keep_gcode: bool = False) -> dict:
    """Slice one STL. Returns a stats dict; `ok: false` + `error` if it could not be sliced."""
    stl, work = Path(stl), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    prof = read_profile(ini)
    bed = bed or bed_from_profile(prof)
    rec: dict = {"file": stl.name, "ok": False}
    try:
        info = stl_info(stl)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"could not read STL: {exc}"
        return rec
    rec.update({"size_mm": info["size_mm"], "triangles": info["triangles"], "model_volume_cm3": round(info["volume_mm3"] / 1000, 2),
                "on_bed": abs(info["bounds_min"][2]) <= 0.05})
    fits, rot = bed_fit(info["size_mm"], bed)
    rec["fits_bed"], rec["rotate_z_deg"] = fits, rot
    rec["fits_bed_as_exported"] = fits
    rec["bed_mm"] = list(bed)
    src = stl
    orient = None
    if not fits:
        # tip it 90 deg about X or Y before giving up (live failure 2026-11-02: a 212.7 mm tall shell, 210 mm Z)
        try:
            orient = choose_orientation(read_stl_triangles(stl), bed)
        except ImportError:  # pragma: no cover - numpy is always on the builder
            orient = None
        if orient and orient["fits"]:  # _slice_tipped slices every tip that fits and writes the chosen <stem>.oriented.stl
            rec["fits_bed"], rec["rotate_z_deg"] = True, orient["rotate_z_deg"]
        rec["orientation"] = orient
    else:
        rec["orientation"] = {"key": "as_exported", "tipped": False, "fits": True, "rotate_x_deg": 0, "rotate_y_deg": 0,
                              "rotate_z_deg": rot, "label": "as exported" + (", turned 90 deg about Z" if rot else ""),
                              "printed_size_mm": [round(v, 1) for v in (info["size_mm"] if not rot else
                                                                        [info["size_mm"][1], info["size_mm"][0], info["size_mm"][2]])]}
    rec["tipped"] = bool(orient and orient.get("tipped") and orient.get("fits"))
    if not rec["fits_bed"]:
        rec["error"] = (f"part is {info['size_mm'][0]:.1f} x {info['size_mm'][1]:.1f} x {info['size_mm'][2]:.1f} mm and does not fit the "
                        f"{bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm build volume in any orientation (as exported, turned about Z, "
                        "or tipped 90 deg onto its back, front or either side)")
        return rec
    exe = exe or find_slicer()
    if not exe:
        rec["error"] = "prusa-slicer not found on PATH (set GS_PRUSA_SLICER)"
        return rec
    if rec["tipped"]:
        return _slice_tipped(rec, stl, work, ini, exe, timeout, prof, info, bed, orient, keep_gcode)
    gcode = work / (stl.stem + ".gcode")
    res = _run_slicer(exe, ini, src, gcode, rot, timeout, prof, work / (stl.stem + ".slicer.log"), work)
    rec.update({k: v for k, v in res.items() if k != "stats"})
    if "error" in res:
        return rec
    # the tail summary block is the evidence record for these numbers
    (work / (stl.stem + ".slice.txt")).write_text(_summary_block(gcode))
    if not keep_gcode:
        gcode.unlink(missing_ok=True)
    rec.update(res["stats"])
    rec["ok"] = True
    return rec


def _run_slicer(exe: str, ini: Path, src: Path, gcode: Path, rot: int, timeout: float, prof: dict, log_path: Path,
                home: Path) -> dict:
    """One PrusaSlicer run: {slice_seconds, slicer_warnings, stats} or {..., error}. The command line and output go to log_path."""
    cmd = [exe, "--export-gcode", "--load", str(ini), "--output", str(gcode)]
    if rot:
        cmd += ["--rotate", str(rot)]
    cmd.append(str(src))
    env = dict(os.environ)
    env.setdefault("HOME", str(home))
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"error": f"slicer timed out after {timeout:.0f} s"}
    res: dict = {"slice_seconds": round(time.perf_counter() - t0, 2)}
    log = (r.stdout or "") + (r.stderr or "")
    log_path.write_text(" ".join(cmd) + "\n\n" + log)
    res["slicer_warnings"] = sorted({ln.strip() for ln in log.splitlines()
                                     if re.search(r"warning|error|empty layer|not ?manifold|repaired", ln, re.I)})[:20]
    if r.returncode != 0 or not gcode.exists():
        res["error"] = f"slicer exit code {r.returncode}: " + (log.strip().splitlines() or ["no output"])[-1][:300]
        return res
    stats = parse_gcode(gcode, float(prof.get("filament_diameter", 1.75)), float(prof.get("filament_density", 1.27)))
    if "filament_g" not in stats or stats.get("print_h") is None:
        res["error"] = "slicer G-code had no filament/time summary"
        return res
    res["stats"] = stats
    return res


def _tipped_profile(ini: Path, path: Path, stem: str, o: dict) -> tuple[Path, dict]:
    """The profile a tipped orientation is sliced (and priced) with, written to `path` (it ships with the part): the stock
    profile with supports under bridges (BRIDGE_OVERRIDE), and when part of it faces down over the part itself, where
    build-plate-only supports cannot reach, with supports allowed on the part too (TIPPED_OVERRIDE)."""
    trapped = float(o.get("trapped_overhang_mm2") or 0)
    cm2 = trapped / 100
    on_part = trapped > TRAPPED_LIMIT_MM2
    keys = TIPPED_OVERRIDE if on_part else BRIDGE_OVERRIDE
    if on_part:
        why = (f"tipped onto its side with about {cm2:.0f} cm2 facing down over the part itself, where supports from the build "
               "plate cannot reach (mesh estimate)")
        note = (f"tipped onto its side with about {cm2:.0f} cm2 facing down over the part (mesh estimate), so supports may stand "
                "on the part and go under bridges")
    else:
        why = "tipped onto its side, a pose the CAD did not design for, so no span may print as an unsupported bridge"
        note = "tipped onto its side (a pose the CAD did not design for), so supports go under bridges"
    lines = [ln for ln in Path(ini).read_text().splitlines() if ln.split("=", 1)[0].strip() not in keys]
    path.write_text("\n".join(lines) + f"\n# gstudio.order.slicer override for {stem}: {note}\n"
                    + "".join(f"{k} = {v}\n" for k, v in keys.items()))
    return path, {**keys, "file": path.name, "why": why}


def supports_on_part(override: dict | None) -> bool:
    return isinstance(override, dict) and str(override.get("support_material_buildplate_only")) == "0"


def _slice_tipped(rec: dict, stl: Path, work: Path, ini: Path, exe: str, timeout: float, prof: dict, info: dict,
                  bed: tuple[float, float, float], orient: dict, keep_gcode: bool) -> dict:
    """A part that fits only tipped onto a side: slice every 90 degree tip that fits, each with the profile it would print
    with, and keep the shortest print (within 3 % is a tie: then the least filament, supports included, then the largest
    flat area on the bed). The mesh ranking in choose_orientation
    picked the worst of the four for the live shell (on its right side: 867 g with 541 g of support, 58.9 h, 11.7 cm2 on
    the bed) where face down sliced to 470 g, 151 g of support, 38.3 h (dev VM, 2026-11-02), so the slicer decides."""
    tris = read_stl_triangles(stl)
    fitting = [c for c in orient.get("candidates") or [] if c.get("fits")]

    def one(c: dict) -> dict:
        d = work / f"{stl.stem}.tip-{c['key']}"
        d.mkdir(parents=True, exist_ok=True)
        src = write_stl(d / f"{stl.stem}.oriented.stl", orient_mesh(tris, c))
        ini_c, ov = _tipped_profile(ini, d / f"{stl.stem}.slice.ini", stl.stem, c)
        res = _run_slicer(exe, ini_c, src, d / f"{stl.stem}.gcode", 0, timeout, prof, d / f"{stl.stem}.slicer.log", work)
        return {"cand": c, "dir": d, "override": ov, "res": res}

    with cf.ThreadPoolExecutor(max_workers=max(1, len(fitting))) as pool:
        runs = list(pool.map(one, fitting))
    for r in runs:  # every candidate's measured numbers stay on the record
        st = r["res"].get("stats") or {}
        r["cand"]["sliced"] = ({"print_h": st.get("print_h"), "filament_g": st.get("filament_g"), "support_g": st.get("support_g"),
                                "profile": ("supports allowed on the part and under bridges" if supports_on_part(r["override"])
                                            else "supports from the build plate, also under bridges")}
                               if st else {"error": r["res"].get("error")})
    ok = [r for r in runs if "stats" in r["res"]]
    if not ok:
        bad = runs[0]["res"] if runs else {"error": "no tipped orientation to slice"}
        rec.update({k: v for k, v in bad.items() if k != "stats"})
        rec["orientation"] = orient
        return rec
    order = [c["key"] for c in fitting]
    h_min = min(float(r["res"]["stats"]["print_h"]) for r in ok)
    # within 3 % (+0.1 h) of the shortest counts as a tie: a solid 167.6 x 97.6 x 212.7 mm box sliced 51.3 h on a side and
    # 51.8 h flat but 15 g lighter flat, so the tie goes to the least filament, then the largest flat area on the bed
    pool = [r for r in ok if float(r["res"]["stats"]["print_h"]) <= h_min * 1.03 + 0.1]
    best = min(pool, key=lambda r: (round(float(r["res"]["stats"]["filament_g"])), -float(r["cand"].get("bed_contact_mm2") or 0),
                                    order.index(r["cand"]["key"])))
    d = best["dir"]
    # the chosen orientation's files under the part's own names (the log keeps the command exactly as it ran)
    shutil.copyfile(d / f"{stl.stem}.oriented.stl", work / f"{stl.stem}.oriented.stl")
    shutil.copyfile(d / f"{stl.stem}.slicer.log", work / f"{stl.stem}.slicer.log")
    gcode = d / f"{stl.stem}.gcode"
    (work / (stl.stem + ".slice.txt")).write_text(_summary_block(gcode))
    if keep_gcode:
        shutil.copyfile(gcode, work / (stl.stem + ".gcode"))
    shutil.copyfile(d / f"{stl.stem}.slice.ini", work / f"{stl.stem}.slice.ini")  # a tipped part always has its own profile
    rec["profile_override"] = {**best["override"], "file": f"{stl.stem}.slice.ini"}
    for r in runs:
        shutil.rmtree(r["dir"], ignore_errors=True)
    o = {**best["cand"], "tipped": True, "fits": True, "candidates": orient.get("candidates"),
         "basis": ("sliced every 90 degree tip that fits, each with the profile it prints with (supports under bridges, and "
                   f"allowed on the part where the mesh shows more than {TRAPPED_LIMIT_MM2 / 100:.0f} cm2 facing down over the part "
                   "itself), and kept "
                   "the shortest print (within 3 % counts as a tie), then the least filament (supports included), then the "
                   "largest flat area on the bed; slicer measurements, see candidates[].sliced"),
         "mesh_pick": orient.get("key")}
    o.pop("sliced", None)
    res = best["res"]
    rec.update({k: v for k, v in res.items() if k != "stats"})
    rec.update(res["stats"])
    rec["ok"] = True
    rec["rotate_z_deg"] = o["rotate_z_deg"]
    o["sentence"] = orientation_sentence(o, info["size_mm"], bed, res["stats"].get("supports"), res["stats"].get("support_g"),
                                         supports_on_part=supports_on_part(best["override"]))
    o["method"] = ("the order stage rotated the mesh before slicing (gstudio.order.slicer.orient_mesh); the delivered "
                   f"print/{stl.name} stays as the CAD exported it")
    rec["orientation"] = o
    return rec


def _summary_block(gcode: Path, max_lines: int = 400) -> str:
    """The trailing `; key = value` comment block of a G-code file (what run_sim saved as *.slice.txt)."""
    with open(gcode, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 200_000))
        lines = fh.read().decode("ascii", "ignore").splitlines()
    keep = [ln for ln in lines[-max_lines:] if ln.startswith("; ") and " = " in ln]
    return "\n".join(keep) + "\n"


def slice_all(stls: list[Path], work: Path, ini: Path = INI, jobs: int | None = None, timeout: float = 900) -> dict:
    """Slice parts in parallel processes (PrusaSlicer itself is multi-threaded). Returns {stem: stats}."""
    exe = find_slicer()
    jobs = jobs or max(1, min(len(stls), (os.cpu_count() or 2) // 2))
    out: dict = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futs = {pool.submit(slice_part, s, work, ini, exe, timeout): s for s in stls}
        for f in cf.as_completed(futs):
            s = futs[f]
            try:
                out[s.stem] = f.result()
            except Exception as exc:  # noqa: BLE001
                out[s.stem] = {"file": s.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return dict(sorted(out.items()))
