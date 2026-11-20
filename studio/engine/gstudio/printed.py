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
from .spec import Derived, DHardware, Spec, cross_check, load_spec
from .version import ENGINE_VERSION

PRINTED_SIZE_MIN = 20.0  # mm per axis (catalog plan E2: printed floor 20 mm instead of the gadget's 60)
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

    # ---------------------------------------------------------------- nothing electronic (the spec validator says so too)
    if getattr(sp, "features", None):
        _err(errors, "features", "a printed-only design has no features (no electronics)", "remove every feature", "printed_features")
    elec = getattr(sp, "electronics", None)
    if elec is not None and getattr(elec, "extra_modules", None):
        _err(errors, "electronics.extra_modules", "a printed-only design has no modules", "remove electronics.extra_modules",
             "printed_features")
    if getattr(sp, "behaviour", None) is not None:
        _err(errors, "behaviour", "a printed-only design has no behaviour (no firmware)", "remove behaviour", "printed_features")
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
        if top_w is not None and top_w < 2 * sp.body.wall + 4.0:
            _err(errors, "printed.opening", f"the top is only about {top_w:.0f} mm across inside the walls, too narrow for an open top",
                 "give the body a flat, wider top (a cup or pot profile) or use opening \"none\"", "opening")

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


def _top_opening_width(geom) -> Optional[float]:
    """Approximate inner width (mm) of the body's top, sampled 1 mm below the top along X at y = 0."""
    try:
        x0, x1, _, _, _, z1 = geom.bbox
        z = z1 - 1.0
        xs = [x0 + (x1 - x0) * i / 200.0 for i in range(201)]
        inside = [x for x in xs if geom.outer_sdf(x, 0.0, z) <= 0.0]
        if not inside:
            return 0.0
        return max(inside) - min(inside)
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
