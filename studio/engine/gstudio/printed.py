"""Printed-only designs (catalog package A, pack `printed`): normalize a spec with `kind: "printed"`.

A printed object has no electronics at all: no core, backplane, harness, cartridge, firmware or PCB. It is the body
(silhouette, revolve or blob) with its decor, printed as one part (`split: "none"`), optionally with an open top
(`printed.opening: "top"`, a vessel or planter) and a drain hole (`printed.drain_hole`), standing on adhesive feet when
`body.feet` > 0. normalize() hands such a spec here (its first lines, E2 dispatch hook, plan 0.5.5).

What is checked (logic copied from normalize.py:521-566 and 686-703, not imported, so the gadget path stays untouched):
cross-field spec checks, nothing electronic listed, the overall size 20-260 mm per axis, the printer bed fit in some
orientation (upright preferred), decor on the body. The derived block has no instances, no harness and no cartridge;
`hardware` holds only the feet. Same return shape as normalize(): (spec copy with `derived`, report).

Pure Python (light-engine rule, plan 0.5.5): no CadQuery, firmware, manual, order or pcb imports.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .envelope import fit_orientations, full_envelope
from .errors import SpecError
from .spec import PRINTED_MIN_SIZE, Derived, DHardware, Spec, cross_check, load_spec
from .version import ENGINE_VERSION

PRINTED_SIZE_MIN = float(PRINTED_MIN_SIZE)  # mm per axis (E2: the printed floor, 20 mm, instead of the gadget's 60)
REASON = "printed-only design: no electronics"


def is_printed(spec: Any) -> bool:
    """True for a spec (model or dict) with kind "printed"."""
    if isinstance(spec, dict):
        return spec.get("kind") == "printed"
    return getattr(spec, "kind", "gadget") == "printed"


def printed_options(spec: Any) -> dict:
    """{opening: "none"|"top", drain_hole: bool} of a printed spec (model or dict), defaults filled."""
    p = spec.get("printed") if isinstance(spec, dict) else getattr(spec, "printed", None)
    if hasattr(p, "model_dump"):
        p = p.model_dump(mode="json")
    p = p if isinstance(p, dict) else {}
    opening = p.get("opening") or "none"
    return {"opening": opening if opening in ("none", "top") else "none", "drain_hole": bool(p.get("drain_hole")) and opening == "top"}


def _err(errors: list, path: str, msg: str, fix: Optional[str] = None, code: Optional[str] = None) -> None:
    e = {"path": path, "msg": msg}
    if fix:
        e["fix"] = fix
    if code:
        e["code"] = code
    errors.append(e)


def _r(v: float, n: int = 1) -> float:
    return round(float(v), n)


def _feet_hardware(sp: Spec, lib) -> list[DHardware]:
    """The only purchased part of a printed object: its adhesive feet (normalize.planned_hardware's feet row)."""
    if not getattr(sp.body, "feet", 0):
        return []
    from .normalize import PURCHASED_FEET

    feet = lib.get(PURCHASED_FEET) if lib.has(PURCHASED_FEET) else next(iter(lib.by_role("feet")), None)
    if not feet:
        return []
    return [DHardware(id="Feet", name="adhesive bumper foot", qty=sp.body.feet, group="Feet", use="underside",
                      module=feet["id"], catalog_row=feet.get("catalog_row"))]


def normalize_printed(spec: Any, lib, raise_on_error: bool = False) -> tuple[Spec, dict]:
    from .normalize import BED, ENVELOPE_MARGIN, SIZE_MAX, ShellGeometry

    sp = load_spec(spec) if not isinstance(spec, Spec) else spec
    sp = sp.model_copy(deep=True)
    errors: list[dict] = list(cross_check(sp))
    warnings: list[str] = []
    added: list[str] = []

    # nothing electronic, the pack gate and drain hole rules: spec.cross_check (pack_checks, E2) already reported them
    split = getattr(sp.body, "split", None)
    if split not in (None, "none"):
        warnings.append(f"body.split {split!r}: a printed-only object is printed as one part; the split is ignored")
    opts = printed_options(sp)

    # ---------------------------------------------------------------- safety flags (no electronics: only the audience)
    flags = set(sp.safety.flags)
    if sp.audience.child_directed and "CHILD_DIRECTED" not in flags:
        flags.add("CHILD_DIRECTED")
        added.append("safety.flags: CHILD_DIRECTED (audience.child_directed is true)")
    sp.safety.flags = sorted(flags)

    # ---------------------------------------------------------------- geometry (normalize.py:521-566, printed floor)
    geom = ShellGeometry(sp)
    env = full_envelope(sp, geom)
    x0, x1, y0, y1, z0, z1 = env["bbox"]
    warnings += [f"envelope: {n}" for n in env["notes"]]
    size = (x1 - x0, y1 - y0, z1 - z0)
    for axis, v in zip("XYZ", size):
        if v < PRINTED_SIZE_MIN - 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; minimum is {PRINTED_SIZE_MIN:g} mm", "make the body bigger",
                 "size")
        if v > SIZE_MAX + 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; maximum is {SIZE_MAX:g} mm", "scale the body down", "size")
    fit_margin = 0.0 if env["source"].startswith("CAD spline") else ENVELOPE_MARGIN
    fit = fit_orientations(tuple(v + fit_margin for v in size), BED)
    dims = f"{size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm"
    if not fit["any"]:
        k = math.floor(fit["scale_to_fit"] * 0.98 * 100) / 100
        _err(errors, "body", f"the object is {dims} and does not fit the {BED[0]:.0f} x {BED[1]:.0f} x {BED[2]:.0f} mm printer in any "
             "orientation", f"scale the whole design by {k:.2f} or less (every body point, depth and decor size and position)", "bed")
    elif not fit["upright"]:
        k = math.floor(fit["scale_to_fit_upright"] * 0.98 * 100) / 100
        warnings.append(f"bed: the object is {dims}; it does not fit the {BED[0]:.0f} x {BED[1]:.0f} x {BED[2]:.0f} mm printer "
                        f"upright, so it prints on its side with supports. To print it upright, scale the whole design by {k:.2f}")
    if opts["opening"] == "top":
        top_w = _top_opening_width(geom)
        need = 2 * sp.body.wall + MOUTH_MIN
        if top_w is not None and top_w < need:
            _err(errors, "printed.opening", f"the top of the body is only about {top_w:.0f} mm across, too narrow for an open top "
                 f"(the CAD opens the highest part of the body straight down)",
                 f"give the body a flat top at least {need:.0f} mm across in both directions (a cup, pot or vase shape) with nothing "
                 "sticking up above it, or use opening \"none\"", "opening")
        _check_open_rim(sp, geom, errors)
        ears = [k for k, d in enumerate(sp.decor) if getattr(d, "type", None) == "ear"]
        if ears:  # ears sit on the top edge, where the CAD's straight-down mouth would slice them into slivers
            _err(errors, f"decor[{ears[0]}]", "ears sit on the top edge, and an open top would slice them",
                 "remove the ears (decor type ear) or use printed.opening \"none\"", "opening_decor")

    # ---------------------------------------------------------------- cuts (pack printed_cuts; the pack gate is in cross_check)
    for k, c in enumerate(getattr(sp, "cuts", None) or []):
        _check_cut(geom, k, c, errors, warnings)

    # ---------------------------------------------------------------- decor on the body (normalize.py:701-703)
    for dk, d in enumerate(sp.decor):
        if d.type in ("face_plate", "ear"):
            continue
        if geom.surface_depth("front", d.at[0], d.at[1]) is None:
            warnings.append(f"decor[{dk}] ({d.type}) at {list(d.at)} is outside the front silhouette")

    # ---------------------------------------------------------------- derived (no electronics)
    hw = _feet_hardware(sp, lib)
    warnings.append(REASON)
    sp.derived = Derived(engine_version=ENGINE_VERSION, size_mm=tuple(_r(v) for v in size),
                         bbox=tuple(_r(v) for v in (x0, x1, y0, y1, z0, z1)), instances=[], harness=[], hardware=hw,
                         cartridge=None, warnings=warnings)
    report = {"ok": not errors, "errors": errors, "warnings": warnings, "added": added, "nudges": [],
              "size_mm": [_r(v) for v in size], "kind": "printed", "printed": opts,
              "envelope": {"bbox": [_r(v) for v in (x0, x1, y0, y1, z0, z1)], "source": env["source"],
                           "fits_upright": fit["upright"], "fits_any_orientation": fit["any"],
                           "ears": [{k: (list(map(_r, v)) if k == "bounds" else v) for k, v in e.items()} for e in env["ears"]]},
              "instances": [], "slots": {}}
    if raise_on_error and errors:
        raise SpecError(errors)
    return sp, report


def _check_open_rim(sp: Spec, geom, errors: list) -> None:
    """An open top must keep a real rim. The CAD opens the cavity section a few mm below the cavity top straight up, so
    (1) on a blob, the primitives that make the top edge must not be rounded more than the wall (BENCH pre-check 20 Nov: an
    agent's cat cap, cylinder radius 4 on a 2.4 mm wall, 0.03 mm at the rim, CAD min_wall failed), and (2) for any family,
    the body's own top section must stay a minimum wall wider than that mouth on every side (a domed or inward-curving top
    tapers to a knife edge); (2) compares the X and Y extents of the outer body (ShellGeometry), decor excluded."""
    b = sp.body
    wall = float(b.wall)
    top = geom.bbox[5]
    if getattr(b, "family", None) == "blob":
        n0 = len(errors)
        for k, pr in enumerate(b.primitives):
            if pr.aabb()[5] >= top - 1.0 and pr.kind in ("rounded_box", "cylinder") and float(pr.radius) > wall + 1e-6:
                _err(errors, f"body.primitives[{k}].radius", f"an open top cuts straight down through this primitive's rounded "
                     f"top edge (radius {float(pr.radius):g} mm on a {wall:g} mm wall), leaving a knife-thin rim",
                     f"set its radius to {wall:g} or less, or use printed.opening \"none\"", "opening_rim")
        if len(errors) > n0:
            return
    w_top = _extents(geom, top - 0.6)
    w_mouth = _extents(geom, top - wall - 3.0)
    if w_top and w_mouth and any(a < (m - 2 * wall) + 2 * MIN_FLOOR - 1e-6 for a, m in zip(w_top, w_mouth)):
        _err(errors, "printed.opening", f"the top of the body curves in too fast for an open top: just under the top it is "
             f"{w_top[0]:.0f} x {w_top[1]:.0f} mm, while the opening the CAD cuts is about {max(0.0, w_mouth[0] - 2 * wall):.0f} x "
             f"{max(0.0, w_mouth[1] - 2 * wall):.0f} mm, so the rim would be thinner than {MIN_FLOOR:g} mm",
             "make the top of the body straight-sided for its last few mm (a cup or pot rim), or use opening \"none\"",
             "opening_rim")


CUT_EDGE_MARGIN = 1.5  # mm of material kept around a cut on its face
MIN_FLOOR = 1.2  # mm under a blind pocket or slot (the CAD min-wall rule)


def _through(geom, face: str, u: float, v: float) -> Optional[tuple[float, float]]:
    """(entry, exit) distances along the inward ray at face coords (u, v), measured from the face's start plane; None on a miss.
    Solid body assumed (outer surface only)."""
    t_in = geom.surface_depth(face, u, v)
    if t_in is None:
        return None
    t = t_in + 0.3
    for _ in range(4000):
        p = geom.point(face, u, v, t)
        if geom.outer_sdf(*p) > 0.05:
            return t_in, t
        t += 0.25
    return t_in, t


def _check_cut(geom, k: int, c: Any, errors: list, warnings: list) -> None:
    face, (u, v), (w, h), depth = c.face, c.at, c.size, float(c.depth)
    path = f"cuts[{k}]"
    span = _through(geom, face, u, v)
    if span is None:
        _err(errors, f"{path}.at", f"the {c.kind} at {list(c.at)} on the {face} face is not on the body",
             f"put it where the {face} face is (front/rear [x, z], left/right [y, z], top [x, y] in mm)", "cut_off_body")
        return
    m = CUT_EDGE_MARGIN
    corners = [(u + sx * (w / 2 + m), v + sy * (h / 2 + m)) for sx in (-1, 1) for sy in (-1, 1)]
    if any(geom.surface_depth(face, cu, cv) is None for cu, cv in corners):
        _err(errors, f"{path}.size", f"the {c.kind} ({w:g} x {h:g} mm) runs off the edge of the {face} face",
             f"move it inward or make it smaller so {m:g} mm of material stays around it", "cut_edge")
    thick = span[1] - span[0]
    if depth >= thick - 1e-6:
        warnings.append(f"{path}: the {c.kind} goes all the way through ({depth:g} mm deep, the body is about {thick:.0f} mm there)")
    elif c.kind in ("pocket", "slot") and thick - depth < MIN_FLOOR:
        _err(errors, f"{path}.depth", f"the {c.kind} leaves only {thick - depth:.1f} mm of material under it (minimum {MIN_FLOOR:g} mm)",
             f"make it at most {max(0.6, thick - MIN_FLOOR):.0f} mm deep, or at least {thick:.0f} mm to cut right through", "cut_floor")


MOUTH_MIN = 10.0  # mm: the smallest open-top mouth, inside the walls


def _extents(geom, z: float) -> Optional[tuple[float, float]]:
    """(X extent, Y extent) in mm of the outer body's section at height z (ShellGeometry sampling); None if empty."""
    try:
        x0, x1, y0, y1, _, _ = geom.bbox
        pts = [(x0 + (x1 - x0) * i / 120.0, y0 + (y1 - y0) * j / 120.0) for i in range(121) for j in range(121)]
        inside = [(x, y) for x, y in pts if geom.outer_sdf(x, y, z) <= 0.0]
        if not inside:
            return None
        xs, ys = [p[0] for p in inside], [p[1] for p in inside]
        return (max(xs) - min(xs), max(ys) - min(ys))
    except Exception:  # noqa: BLE001 - a hint only
        return None


def _top_opening_width(geom) -> Optional[float]:
    """Outer width (mm) of the body's highest part, 1 mm below its top: the smaller of the X and Y extents there."""
    try:
        x0, x1, y0, y1, _, z1 = geom.bbox
        z = z1 - 1.0
        pts = [(x0 + (x1 - x0) * i / 120.0, y0 + (y1 - y0) * j / 120.0) for i in range(121) for j in range(121)]
        inside = [(x, y) for x, y in pts if geom.outer_sdf(x, y, z) <= 0.0]
        if not inside:
            return 0.0
        xs, ys = [p[0] for p in inside], [p[1] for p in inside]
        return min(max(xs) - min(xs), max(ys) - min(ys))
    except Exception:  # noqa: BLE001 - a hint only
        return None


def empty_electrical(spec: Any = None) -> dict:
    """electrical.json for a printed-only design (the pipeline writes it; plan A): the keys of
    gstudio.electrical.build_electrical with nothing in them, power total 0 and ok true."""
    from .version import ELECTRICAL_SCHEMA

    power = {"rails": [], "caps": {}, "budget": [], "total_ma_typ": 0.0, "total_ma_max": 0.0, "total_ma_max_capped": 0.0,
             "rail_3v3_ma_typ": 0.0, "rail_3v3_ma_max": 0.0, "limit_ma": 0, "ok": True, "ok_uncapped": True, "ok_5v": True,
             "ok_3v3": True, "firmware_caps_required": False, "notes": REASON}
    return {"schema": ELECTRICAL_SCHEMA, "engine_version": ENGINE_VERSION, "kind": "printed", "printed_only": True,
            "backplane": None, "core": {}, "instances": [], "slots_used": [], "gpio": {}, "gpio_paths": {}, "buses": {},
            "nets": [], "connections": [], "core_socket_pins": [], "support_parts": [], "power": power, "warnings": [REASON]}


__all__ = ["PRINTED_SIZE_MIN", "REASON", "is_printed", "printed_options", "normalize_printed", "empty_electrical"]
