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
    rec["bed_mm"] = list(bed)
    if not fits:
        rec["error"] = (f"part is {info['size_mm'][0]:.1f} x {info['size_mm'][1]:.1f} x {info['size_mm'][2]:.1f} mm and does not fit the "
                        f"{bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm build volume in this orientation")
        return rec
    exe = exe or find_slicer()
    if not exe:
        rec["error"] = "prusa-slicer not found on PATH (set GS_PRUSA_SLICER)"
        return rec
    gcode = work / (stl.stem + ".gcode")
    cmd = [exe, "--export-gcode", "--load", str(ini), "--output", str(gcode)]
    if rot:
        cmd += ["--rotate", str(rot)]
    cmd.append(str(stl))
    env = dict(os.environ)
    env.setdefault("HOME", str(work))
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        rec["error"] = f"slicer timed out after {timeout:.0f} s"
        return rec
    rec["slice_seconds"] = round(time.perf_counter() - t0, 2)
    log = (r.stdout or "") + (r.stderr or "")
    (work / (stl.stem + ".slicer.log")).write_text(" ".join(cmd) + "\n\n" + log)
    rec["slicer_warnings"] = sorted({ln.strip() for ln in log.splitlines()
                                     if re.search(r"warning|error|empty layer|not ?manifold|repaired", ln, re.I)})[:20]
    if r.returncode != 0 or not gcode.exists():
        rec["error"] = f"slicer exit code {r.returncode}: " + (log.strip().splitlines() or ["no output"])[-1][:300]
        return rec
    stats = parse_gcode(gcode, float(prof.get("filament_diameter", 1.75)), float(prof.get("filament_density", 1.27)))
    if "filament_g" not in stats or stats.get("print_h") is None:
        rec["error"] = "slicer G-code had no filament/time summary"
        return rec
    # the tail summary block is the evidence record for these numbers
    (work / (stl.stem + ".slice.txt")).write_text(_summary_block(gcode))
    if not keep_gcode:
        gcode.unlink(missing_ok=True)
    rec.update(stats)
    rec["ok"] = True
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
