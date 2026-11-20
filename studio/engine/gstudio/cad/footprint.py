"""Kit footprints: where each feature's whole mount kit sits, without building any CAD (pure Python, no cadquery).

The CAD kits in features.py and the layout validator (foundation, normalize/validate_only) both use these numbers, so
the validator sees the same retainers, bosses, clamp straps and speaker clamp-ring tabs that the CAD later builds.

Coordinates. Each box is axis-aligned in the feature's FACE coordinates, which are the coordinates of
`features[].at` for that face:
    front, rear  u = x, v = z
    left, right  u = y, v = z
    top          u = x, v = y
plus `depth` = distance inward from the outer surface at the feature centre (mm, >= 0 inside the body). A box is
{feature, part, kind, what, face, u: [u0, u1], v: [v0, v1], depth: [d0, d1]}.
`to_world(box, s_surface)` turns one into a world AABB (x0, x1, y0, y1, z0, z1) given the outer-surface coordinate
along the face's inward axis (front: the surface y at that point; rear: y; left/right: x; top: z).

Assumptions (stated, not hidden): the wall is flat and `skin` thick at the feature (the CAD adds a flat local pad on
curved walls, so real kits on curved fronts sit slightly deeper); the kit frame normal is the face direction (the CAD
tilts it to the smoothed surface normal); screw heads are 4.5 mm SHCS. Boxes are conservative envelopes of round
parts. Margins between kits are the caller's choice (`layout_conflicts(margin=...)`).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Optional

from .modules import describe, fasteners as _fasteners_from_lib, pick_screw

try:
    from . import kits_ext  # pack kits (cad/kits_*.py)
except ImportError:  # pragma: no cover - a vendored gadget_cad copy without the registry (export.VENDOR)
    kits_ext = None

# face -> (sign of local u in face coordinates); local v always maps to +v (see features.FACE_RAY / Frame.make)
_U_SIGN = {"front": 1.0, "rear": -1.0, "left": 1.0, "right": -1.0, "top": 1.0}
SPEAKER_DEFAULT_TABS = 90.0  # old fixed triad [90, 210, 330]
SPEAKER_ABOVE_TABS = 30.0  # triad [30, 150, 270] when another feature sits above the speaker (REVIEW-1 BLK-1)
COMFORT_GAP = 6.0  # tab/boss gaps above this are all equally good when choosing the triad rotation

_FZ_CACHE: dict = {}


# ------------------------------------------------------------------------------------------------ inputs
def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _feature_dict(f: Any) -> dict:
    """Spec feature (dict or pydantic model) -> plain dict with the keys the footprints use."""
    g = _get(f, "grille")
    if g is not None and not isinstance(g, dict):
        g = {k: _get(g, k) for k in ("pattern", "w", "h", "hole", "pitch")}
    return {"id": _get(f, "id", "?"), "module": _get(f, "module"), "face": _get(f, "face", "front") or "front",
            "at": [float(x) for x in (_get(f, "at") or (0.0, 0.0))], "rotate": float(_get(f, "rotate", 0) or 0.0),
            "grille": g, "tab_rotation": _get(f, "tab_rotation")}


def default_fasteners(lib_get: Optional[Callable] = None) -> dict:
    """Fastener geometry the CAD uses (library rows when available, else the provisional set)."""
    if lib_get is not None:
        return _fasteners_from_lib(lib_get)
    if "lib" not in _FZ_CACHE:
        try:
            from .params import LibAccess

            L = LibAccess(None)
            _FZ_CACHE["lib"] = _fasteners_from_lib(L.get)
        except Exception:  # pragma: no cover - library missing
            _FZ_CACHE["lib"] = _fasteners_from_lib(lambda _id: None)
    return _FZ_CACHE["lib"]


def module_desc(feature: Any, module: Any = None, lib_get: Optional[Callable] = None) -> dict:
    """Accept a CAD descriptor, a library row (dict or model), a module id, or None (looked up from feature.module)."""
    if isinstance(module, dict) and "kind" in module and ("provisional_fields" in module):
        return module
    if module is None or isinstance(module, str):
        mid = module or _get(feature, "module")
        row = None
        if lib_get is not None:
            row = lib_get(mid)
        if row is None:
            from .params import LibAccess

            row = LibAccess(None).get(mid)
        module = row or {"id": mid, "name": mid, "role": "generic"}
    if not isinstance(module, dict):
        from .params import as_dict

        module = as_dict(module)
    return describe(module)


# ------------------------------------------------------------------------------------------------ geometry helpers
def _box(feature: dict, part: str, kind: str, what: str, a0, a1, b0, b1, d0, d1) -> dict:
    """Local kit rectangle (a: local u, b: local v, both before the feature rotation) -> face box."""
    fu, fv = feature["at"]
    rot = math.radians(feature.get("rotate") or 0.0)
    su = _U_SIGN.get(feature["face"], 1.0)
    c, s = math.cos(rot), math.sin(rot)
    us, vs = [], []
    for a in (a0, a1):
        for b in (b0, b1):
            lu, lv = a * c - b * s, a * s + b * c  # rotated frame: u' = cos u + sin v, v' = -sin u + cos v
            us.append(fu + su * lu)
            vs.append(fv + lv)
    return {"feature": feature["id"], "part": part, "kind": kind, "what": what, "face": feature["face"],
            "u": [round(min(us), 3), round(max(us), 3)], "v": [round(min(vs), 3), round(max(vs), 3)],
            "depth": [round(min(d0, d1), 3), round(max(d0, d1), 3)]}


def _disc(feature, part, kind, what, cu, cv, r, d0, d1) -> dict:
    return _box(feature, part, kind, what, cu - r, cu + r, cv - r, cv + r, d0, d1)


def box_gap(a: dict, b: dict) -> float:
    """Clearance between two boxes on the same face: max of the per-axis gaps (negative = overlap depth)."""
    gu = max(a["u"][0] - b["u"][1], b["u"][0] - a["u"][1])
    gv = max(a["v"][0] - b["v"][1], b["v"][0] - a["v"][1])
    gd = max(a["depth"][0] - b["depth"][1], b["depth"][0] - a["depth"][1])
    if gu >= 0 or gv >= 0 or gd >= 0:
        # separated on at least one axis: Euclidean distance over the separated axes
        return math.sqrt(sum(max(g, 0.0) ** 2 for g in (gu, gv, gd)))
    return max(gu, gv, gd)


def to_world(box: dict, s_surface: float) -> tuple:
    """Face box -> world AABB (x0, x1, y0, y1, z0, z1). s_surface: outer-surface coordinate along the inward axis."""
    u0, u1 = box["u"]
    v0, v1 = box["v"]
    d0, d1 = box["depth"]
    f = box["face"]
    if f == "front":
        return (u0, u1, s_surface + d0, s_surface + d1, v0, v1)
    if f == "rear":
        return (u0, u1, s_surface - d1, s_surface - d0, v0, v1)
    if f == "left":  # robot's left = +X side; inward = -X
        return (s_surface - d1, s_surface - d0, u0, u1, v0, v1)
    if f == "right":
        return (s_surface + d0, s_surface + d1, u0, u1, v0, v1)
    return (u0, u1, v0, v1, s_surface - d1, s_surface - d0)  # top


# ------------------------------------------------------------------------------------------------ shared layouts
def clamp_layout(pcb: dict, back_items: list, fz: dict, avoid_axis: Optional[str] = None) -> dict:
    """Strap clamp choice used by features.board_clamp: axis ('u' or 'v'), perpendicular offset, back-item height."""
    w, h = pcb["w"], pcb["h"]
    best = None
    for axis in ("u", "v"):
        if axis == avoid_axis:
            continue
        span = w if axis == "u" else h
        perp = h if axis == "u" else w
        for off in (0.0, perp / 4, -perp / 4):
            hb = 0.0
            for it in back_items:
                ic, isz = it["c"], it["size"]
                b0, b1 = (ic[1], isz[1]) if axis == "u" else (ic[0], isz[0])
                if abs(b0 - off) < (b1 / 2 + 3.6 + 0.3):
                    hb = max(hb, isz[2])
            key = (round(hb, 2), span, abs(off))
            if best is None or key < best[0]:
                best = (key, axis, off, hb, span)
    _, axis, off, hb, span = best
    rp = fz["insert"]["boss_d"] / 2
    return {"axis": axis, "off": off, "hb": hb, "span": span, "reach": span / 2 + 0.6 + rp, "rp": rp}


def clamp_z(z_front: float, t: float, hb: float, z_pad: float, fz: dict) -> tuple[float, float, float]:
    """(strap front z, screw length, insert hole depth) exactly as features.board_clamp computes them."""
    ins, scr = fz["insert"], fz["screw"]
    L, hole_depth = pick_screw(2.4, scr["lengths"], ins["hole_depth"])
    z_sf = z_front - t - hb - 0.5
    need = hole_depth + 1.2
    if z_pad - z_sf < need:
        z_sf = z_pad - need
    return z_sf, L, hole_depth


def speaker_layout(desc: dict, fz: dict, z_m: float) -> dict:
    """Speaker kit dimensions (features.kit_speaker uses this): boss circle, ring, presser tube, posts."""
    s = desc["speaker"]
    ears = s.get("ears")
    ear_r = max((abs(hx) + ears["w"] / 2 for hx, _ in ears["holes"]), default=0.0) if ears else 0.0
    de = max(s["d"], ears["span"] if ears else 0.0, 2 * ear_r + 0.6)
    ins, scr = fz["insert"], fz["screw"]
    rp = ins["boss_d"] / 2
    wall_t = 1.6
    R_b = de / 2 + 0.5 + wall_t + rp - 0.8
    R_pad = R_b + rp + 1.0
    gc = fz["gasket"]["compressed_t"]
    z_s = z_m + 0.8 - gc
    Lr, hd_r = pick_screw(2.4, scr["lengths"], ins["hole_depth"])
    z_pe = min(z_s - s["flange_t"] - 2.0, z_m - (hd_r + 1.5))
    ring_od = de / 2 + 1.2
    ring_id = s["magnet_d"] / 2 + 2.5 + max(0.0, (s["opening"] / 2 - s["magnet_d"] / 2) * 0.35)
    return {"de": de, "rp": rp, "wall_t": wall_t, "R_b": R_b, "R_pad": R_pad, "z_s": z_s, "z_pe": z_pe, "ring_od": ring_od,
            "ring_id": ring_id, "screw_len": Lr, "hole_depth": hd_r, "cup_or": de / 2 + 0.5 + wall_t}


def tab_angles(theta: float) -> list[float]:
    return [round((theta + k * 120.0) % 360.0, 3) for k in range(3)]


# ------------------------------------------------------------------------------------------------ kit footprints
def _clamp_boxes(fd, ref_part, pcb, center, z_front, z_pad, back, fz, avoid_axis=None, standoffs=False):
    """Boxes for the strap, its 2 posts and 2 screws (board_clamp)."""
    lay = clamp_layout(pcb, back, fz, avoid_axis)
    z_sf, L, _ = clamp_z(z_front, pcb["t"], lay["hb"], z_pad, fz)
    cu, cv = center
    rp, reach, off = lay["rp"], lay["reach"], lay["off"]
    head_d, head_h = fz["screw"]["head_d"], fz["screw"]["head_h"]
    out = []

    def P(a, b):
        return (cu + a, cv + b) if lay["axis"] == "u" else (cu + b, cv + a)

    for sgn in (-1, 1):
        pu, pv = P(sgn * reach, off)
        out.append(_disc(fd, "PRINT_Shell", "boss", f"clamp post {('-', '+')[sgn > 0]}", pu, pv, rp, -(z_pad + 0.8), -z_sf))
        out.append(_disc(fd, "PURCHASE_ModuleScrews", "screw", "clamp screw head", pu, pv, head_d / 2, -(z_sf - 2.4), -(z_sf - 2.4 - head_h)))
    if lay["axis"] == "u":
        a0, a1, b0, b1 = cu - reach - rp, cu + reach + rp, cv + off - rp, cv + off + rp
    else:
        a0, a1, b0, b1 = cu + off - rp, cu + off + rp, cv - reach - rp, cv + reach + rp
    out.append(_box(fd, ref_part, "clamp", "clamp strap", a0, a1, b0, b1, -z_sf, -(z_sf - 2.4)))
    if standoffs and z_pad - z_front > 0.05:
        for sgn in (-1, 1):
            pu, pv = P(sgn * (lay["span"] / 2 - 1.4), off)
            sz = (2.4, 5.0) if lay["axis"] == "u" else (5.0, 2.4)
            out.append(_box(fd, "PRINT_Shell", "boss", "standoff", pu - sz[0] / 2, pu + sz[0] / 2, pv - sz[1] / 2, pv + sz[1] / 2,
                            -(z_pad + 0.8), -z_front))
    return out, lay


def _board_boxes(fd, part, pcb, center, z_front, front_items, back_items):
    cu, cv = center
    w, h, t = pcb["w"], pcb["h"], pcb["t"]
    out = [_box(fd, part, "module", "module board", cu - w / 2, cu + w / 2, cv - h / 2, cv + h / 2, -z_front, -(z_front - t))]
    for it in front_items or []:
        c = it["c"]
        z0 = z_front + it.get("z0", 0.0)
        if it.get("kind") == "cyl":
            r = it["d"] / 2
            out.append(_disc(fd, part, "module", it["name"], cu + c[0], cv + c[1], r, -z0, -(z0 + it["h"])))
        else:
            s = it["size"]
            out.append(_box(fd, part, "module", it["name"], cu + c[0] - s[0] / 2, cu + c[0] + s[0] / 2, cv + c[1] - s[1] / 2,
                            cv + c[1] + s[1] / 2, -z0, -(z0 + s[2])))
    zb = z_front - t
    for it in back_items or []:
        c, s = it["c"], it["size"]
        out.append(_box(fd, part, "module", it["name"], cu + c[0] - s[0] / 2, cu + c[0] + s[0] / 2, cv + c[1] - s[1] / 2,
                        cv + c[1] + s[1] / 2, -zb, -(zb - s[2])))
    return out


def _pad_box(fd, w, h, z_pad, skin, round_r=None):
    if -z_pad - skin <= 0.05:
        return []
    if round_r:
        return [_disc(fd, "PRINT_Shell", "pad", "mount pad", 0, 0, round_r, skin, -z_pad)]
    return [_box(fd, "PRINT_Shell", "pad", "mount pad", -w / 2, w / 2, -h / 2, h / 2, skin, -z_pad)]


def _speaker_boxes(fd, d, fz, skin, theta):
    lay = speaker_layout(d, fz, -skin)
    s = d["speaker"]
    rp, R_b, z_s, z_pe = lay["rp"], lay["R_b"], lay["z_s"], lay["z_pe"]
    head_d, head_h = fz["screw"]["head_d"], fz["screw"]["head_h"]
    out = [
        _disc(fd, "PURCHASE_Speaker", "module", "speaker frame", 0, 0, max(s["d"], lay["de"]) / 2, -z_s, -(z_s - s["flange_t"])),
        _disc(fd, "PURCHASE_Speaker", "module", "speaker basket and magnet", 0, 0, s["magnet_d"] / 2 + 1.0, -(z_s - s["flange_t"]), -(z_s - s["depth"])),
        _disc(fd, "PRINT_Shell", "retainer", "speaker cup wall", 0, 0, lay["cup_or"], skin - 0.8, -z_pe),
        _disc(fd, "PRINT_SpeakerRing", "retainer", "clamp ring", 0, 0, lay["ring_od"], -z_pe, -(z_pe - 2.4)),
    ]
    for a in tab_angles(theta):
        pu, pv = R_b * math.cos(math.radians(a)), R_b * math.sin(math.radians(a))
        out.append(_disc(fd, "PRINT_Shell", "boss", f"ring post at {a:g} deg", pu, pv, rp, skin - 0.8, -z_pe))
        # the tab: from the ring outline to the post, rp wide (axis-aligned envelope of the stadium)
        ru, rv = (lay["ring_od"] - 1.0) * math.cos(math.radians(a)), (lay["ring_od"] - 1.0) * math.sin(math.radians(a))
        out.append(_box(fd, "PRINT_SpeakerRing", "tab", f"ring tab at {a:g} deg", min(ru, pu) - rp, max(ru, pu) + rp,
                        min(rv, pv) - rp, max(rv, pv) + rp, -z_pe, -(z_pe - 2.4)))
        out.append(_disc(fd, "PURCHASE_ModuleScrews", "screw", f"ring screw head at {a:g} deg", pu, pv, head_d / 2,
                         -(z_pe - 2.4), -(z_pe - 2.4 - head_h)))
    return out, lay


def speaker_rotation(feature: Any, module: Any = None, others: Iterable = (), *, fasteners: Optional[dict] = None,
                     skin: Optional[float] = None, lib_get: Optional[Callable] = None) -> dict:
    """Choose the speaker clamp-ring tab triad rotation theta in [0, 120): tabs, posts and screws at theta, +120, +240
    (degrees from the kit's local +u). Maximises the minimum clearance to every other kit footprint on the same face,
    capped at COMFORT_GAP mm; among equally good rotations it keeps the default (30 when any feature sits above the
    speaker, else 90). Returns {theta, angles, min_gap_mm, above, default}."""
    fd = _feature_dict(feature)
    d = module_desc(feature, module, lib_get)
    fz = fasteners or default_fasteners(lib_get)
    sk = float(skin if skin is not None else 2.4)
    other_boxes = []
    above = False
    for o in others or ():
        od = _feature_dict(o)
        if od["id"] == fd["id"] or od["face"] != fd["face"]:
            continue
        try:
            ob = kit_footprint(o, _get(o, "desc"), fasteners=fz, skin=sk, lib_get=lib_get, _no_speaker_search=True)
        except Exception:
            continue
        other_boxes += ob
        if od["at"][1] > fd["at"][1] and abs(od["at"][0] - fd["at"][0]) < 30.0:
            above = True
    default = SPEAKER_ABOVE_TABS if above else SPEAKER_DEFAULT_TABS
    if not other_boxes:
        return {"theta": default, "angles": tab_angles(default), "min_gap_mm": None, "above": above, "default": default}
    scored = []
    for k in range(120):
        th = float(k)
        mine, _ = _speaker_boxes(fd, d, fz, sk, th)
        own = [b for b in mine if b["kind"] in ("boss", "tab", "screw")]
        g = min(box_gap(a, b) for a in own for b in other_boxes)
        scored.append((min(g, COMFORT_GAP), -abs(((th - default + 60) % 120) - 60), th, g))
    best = max(scored)
    th = best[2]
    return {"theta": th, "angles": tab_angles(th), "min_gap_mm": round(best[3], 2), "above": above, "default": default}


def kit_footprint(feature: Any, module: Any = None, *, others: Iterable = (), fasteners: Optional[dict] = None,
                  skin: Optional[float] = None, tab_rotation: Optional[float] = None, lib_get: Optional[Callable] = None,
                  _no_speaker_search: bool = False) -> list[dict]:
    """Axis-aligned boxes for one feature's whole kit (module, retainer, posts/bosses, clamp strap, screw heads, speaker
    clamp ring and tabs at the rotation the CAD will choose) in the feature's face coordinates (see module doc).

    feature   spec feature (dict or pydantic): id, module, face, at, rotate, grille
    module    library row (dict/model), a CAD descriptor, a module id, or None (looked up from feature.module)
    others    the other spec features of the design; used only by speakers to pick the tab rotation
    fasteners CAD fastener geometry (default: from library/modules.json)
    skin      wall thickness at the feature (default 2.4)
    tab_rotation  force the speaker triad rotation (degrees) instead of choosing it
    """
    fd = _feature_dict(feature)
    d = module_desc(feature, module, lib_get)
    fz = fasteners or default_fasteners(lib_get)
    sk = float(skin if skin is not None else 2.4)
    k = d["kind"]
    ins = fz["insert"]
    boss = ins["boss_d"]
    out: list[dict] = []
    z_in = -sk
    pack_boxes = kits_ext.footprint_boxes(k, d, fz, sk) if kits_ext is not None else None  # None for built-in kinds
    if k == "speaker":
        th = tab_rotation if tab_rotation is not None else fd.get("tab_rotation")
        if th is None:
            th = SPEAKER_DEFAULT_TABS if _no_speaker_search else speaker_rotation(fd, d, others, fasteners=fz, skin=sk, lib_get=lib_get)["theta"]
        out, lay = _speaker_boxes(fd, d, fz, sk, float(th))
        out += _pad_box(fd, 0, 0, z_in, sk, round_r=lay["cup_or"] + 1.0)  # pad = cup disc + post discs (posts boxed above)
    elif k == "display":
        pcb, g, va = d["pcb"], d["glass"], d["view"]
        z_g = -0.6 - 1.2
        z_p = z_g - g["t"]
        z_pad = min(z_in, z_p - 0.8)
        out += _pad_box(fd, pcb["w"] + 2 * (0.6 + boss) + 4, pcb["h"] + 6, z_pad, sk)
        gc = g["c"]
        out.append(_box(fd, "PURCHASE_Display", "module", "glass", gc[0] - g["w"] / 2, gc[0] + g["w"] / 2, gc[1] - g["h"] / 2,
                        gc[1] + g["h"] / 2, -z_g, -z_p))
        out += _board_boxes(fd, "PURCHASE_Display", pcb, (0, 0), z_p, [], d["back"])
        cb, _ = _clamp_boxes(fd, "PRINT_DisplayClip", pcb, (0, 0), z_p, z_pad, d["back"], fz)
        out += cb
    elif k in ("led", "led_ring"):
        ring = k == "led_ring"
        pcb = d["pcb"]
        if ring:
            od = d["ring"]["od"]
            win, tube_id, flange_d, gap = od - 6.0, od - 2.0, od - 2.6, 6.0
        else:
            win = max(float(d["aperture"].get("w") or 8.0), 5.0)
            flange_d, gap = win + 2.4, 3.0
            tube_id = flange_d + 0.6
        z_pad = min(z_in, -2.0)
        out += _pad_box(fd, max(pcb["w"], tube_id) + 2 * (0.6 + boss) + 6, max(pcb["h"], tube_id) + 6, z_pad, sk)
        out.append(_disc(fd, "PRINT_LedDiffuser", "retainer", "diffuser plug and flange", 0, 0, flange_d / 2, 0.0, -(z_pad - 1.2)))
        z_front = z_pad - 1.2 - gap - 1.6
        tube_od = max(tube_id + 4.0, (pcb["w"] if not ring else od) * (1.0 if ring else 1.42) + 0.5)
        out.append(_disc(fd, "PRINT_Shell", "retainer", "light tube", 0, 0, tube_od / 2, -(z_pad + 0.8), -z_front))
        part = "PURCHASE_LedRing" if ring else "PURCHASE_Led"
        out += _board_boxes(fd, part, pcb, (0, 0), z_front, d.get("front", []), d.get("back", []))
        cb, _ = _clamp_boxes(fd, "PRINT_LedClip", pcb, (0, 0), z_front, z_pad, d.get("back", []), fz)
        out += cb
    elif k == "mic":
        pcb = d["pcb"]
        z_pad = min(z_in, -2.0)
        side = pcb["w"] + 2 * (0.6 + boss) + 4
        out += _pad_box(fd, side, side, z_pad, sk)
        z_front = z_pad - 0.5
        out.append(_disc(fd, "PURCHASE_MicGasket", "retainer", "mic gasket ring", 0, 0, 3.8, -(z_pad + 0.5), -z_front))
        out += _board_boxes(fd, "PURCHASE_Microphone", pcb, (0, 0), z_front, [], d["back"])
        cb, _ = _clamp_boxes(fd, "PRINT_MicClip", pcb, (0, 0), z_front, z_pad, d["back"], fz, standoffs=True)
        out += cb
    elif k == "snap_button":
        b = d["snap_button"]
        lo, hi = b["panel_t"]
        t_p = min(max(3.0, lo), hi)
        z_pb = -t_p
        z_pad = min(z_in, z_pb - 1.5)
        out += _pad_box(fd, 0, 0, z_pad, sk, round_r=b["clip_w"] / 2 + 4.0)
        out.append(_disc(fd, "PURCHASE_Button", "module", "button bezel (outside)", 0, 0, b["flange_d"] / 2, -(b["flange_h"] + b["cap_h"]), 0.0))
        out.append(_disc(fd, "PURCHASE_Button", "clearance", "snap latch space", 0, 0, b["clip_w"] / 2 + 0.3, t_p, -(z_pad - 1.0)))
        out.append(_disc(fd, "PURCHASE_Button", "module", "button housing", 0, 0, b["hole_d"] / 2 - 0.4, 0.0, b["body_len"] - 9.0))
        out.append(_box(fd, "PURCHASE_Button", "module", "microswitch", -6.4, 6.4, -3.25, 3.25, b["body_len"] - 9.0, b["body_len"]))
    elif k == "tactile":
        pcb, sw = d["pcb"], d["switch"]
        cap_d = 10.0
        z_pad = min(z_in, -2.4)
        out += _pad_box(fd, pcb["w"] + 2 * (0.6 + boss) + 4, pcb["h"] + 6, z_pad, sk)
        out.append(_disc(fd, "PRINT_ButtonCap", "retainer", "plunger cap", 0, 0, cap_d / 2 + 1.6, -1.2, -(z_pad - 2.2)))
        z_front = z_pad - 2.5 - sw["body_h"] - sw["stem_h"]
        sc = d["front"][0]["c"]
        center = (-sc[0], -sc[1])
        out += _board_boxes(fd, "PURCHASE_Button", pcb, center, z_front, d["front"], d["back"])
        cb, _ = _clamp_boxes(fd, "PRINT_ButtonClip", pcb, center, z_front, z_pad, d["back"], fz, standoffs=True)
        out += cb
    elif pack_boxes is not None:  # pack kits (cad/kits_*.py), before the generic board
        for b in pack_boxes:
            out.append(_box(fd, b["part"], b["kind"], b["what"], b["u"][0], b["u"][1], b["v"][0], b["v"][1], b["depth"][0], b["depth"][1]))
    else:  # touch / sensor / sensor_window / generic: kit_board
        pcb = d.get("pcb") or {"w": 15.0, "h": 15.0, "t": 1.6}
        if k == "touch":
            z_front = -1.5
            out += _pad_box(fd, pcb["w"] + 6, pcb["h"] + 6, min(z_in, -2.4), sk)
            out += _board_boxes(fd, "PURCHASE_TouchPad", pcb, (0, 0), z_front, [], d.get("back", []))
        else:
            z_pad = min(z_in, -2.0)
            out += _pad_box(fd, pcb["w"] + 2 * (0.6 + boss) + 4, pcb["h"] + 6, z_pad, sk)
            z_front = z_pad - 0.3
            name = "PURCHASE_Sensor" if k == "sensor_window" else "PURCHASE_Module"
            out += _board_boxes(fd, name, pcb, (0, 0), z_front, d.get("front", []), d.get("back", []))
            cb, _ = _clamp_boxes(fd, "PRINT_SensorClip", pcb, (0, 0), z_front, z_pad, d.get("back", []), fz, standoffs=True)
            out += cb
    return out


# ------------------------------------------------------------------------------------------------ layout helpers
def layout_conflicts(features: Iterable, *, margin: float = 0.5, fasteners: Optional[dict] = None, skin: Optional[float] = None,
                     lib_get: Optional[Callable] = None) -> list[dict]:
    """Same-face kit-vs-kit conflicts for a list of spec features. Pads (shell material) may overlap each other and
    another kit's shell bosses, since both are the same printed shell. Each conflict:
    {a, b, a_part, a_what, b_part, b_what, gap_mm, overlap_v_mm, nudge}."""
    feats = [f for f in features]
    fz = fasteners or default_fasteners(lib_get)
    boxes = {}
    for f in feats:
        fd = _feature_dict(f)
        try:
            boxes[fd["id"]] = (fd, kit_footprint(f, _get(f, "desc"), others=feats, fasteners=fz, skin=skin, lib_get=lib_get))
        except Exception:
            continue
    out = []
    ids = list(boxes)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            fa, ba = boxes[ids[i]]
            fb, bb = boxes[ids[j]]
            if fa["face"] != fb["face"]:
                continue
            worst = None
            for a in ba:
                for b in bb:
                    shell_a = a["part"] == "PRINT_Shell"
                    shell_b = b["part"] == "PRINT_Shell"
                    if shell_a and shell_b:
                        continue  # both are shell material
                    if (a["kind"] == "pad" and shell_b) or (b["kind"] == "pad" and shell_a):
                        continue
                    g = box_gap(a, b)
                    if g < margin and (worst is None or g < worst[0]):
                        worst = (g, a, b)
            if worst:
                g, a, b = worst
                # how far b must move along v (up if it is above, else down) to clear by margin
                if fb["at"][1] >= fa["at"][1]:
                    dv = (a["v"][1] - b["v"][0]) + margin
                    direction = "up"
                else:
                    dv = (b["v"][1] - a["v"][0]) + margin
                    direction = "down"
                out.append({"a": fa["id"], "b": fb["id"], "a_part": a["part"], "a_what": a["what"], "b_part": b["part"],
                            "b_what": b["what"], "gap_mm": round(g, 2), "overlap_v_mm": round(max(dv - margin, 0.0), 2),
                            "nudge": f"{fb['id']} touches the {fa['id']} {a['what']}: move {fb['id']} {direction} "
                                     f"{math.ceil(max(dv, 1.0)):.0f} mm or more"})
    return out


def find_clear_at(feature: Any, others: Iterable, module: Any = None, *, fits: Optional[Callable] = None,
                  candidates: Optional[Iterable] = None, margin: float = 3.0, fasteners: Optional[dict] = None,
                  skin: Optional[float] = None, lib_get: Optional[Callable] = None, step: float = 2.0,
                  search_radius: float = 80.0, faces: Optional[Iterable] = None, fits_face: Optional[Callable] = None,
                  face_centres: Optional[dict] = None) -> Optional[dict]:
    """Nearest position for `feature` whose kit footprint clears every other kit by `margin` mm.
    fits(at) -> bool lets the caller reject positions off the face or too shallow (normalize's surface checks); it is
    only called for candidates whose footprint is already clear (it is the expensive part).
    candidates: explicit list of [u, v] to try in order; otherwise a ring search around feature.at.
    faces: other faces to try, in order, when the feature's own face has no room (e.g. ("rear",)); each needs
    fits_face(at, face) -> bool and a start point face_centres[face] = [u, v].
    Returns {at, face, moved_mm, min_gap_mm} or None when nothing fits."""
    fd = _feature_dict(feature)
    fz = fasteners or default_fasteners(lib_get)
    oth = [o for o in others if _feature_dict(o)["id"] != fd["id"]]
    d = module_desc(feature, module, lib_get)

    def ring_cands(u0, v0):
        out = [(u0, v0)]
        r = step
        while r <= search_radius:
            n = max(8, int(2 * math.pi * r / step))
            ring = [(u0 + r * math.cos(2 * math.pi * i / n), v0 + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
            ring.sort(key=lambda p: (abs(p[0] - u0), -p[1]))  # prefer staying on the same vertical line
            out += ring
            r += step
        return out

    def search(face, u0, v0, cands, ok):
        other_boxes = []
        for o in oth:
            od = _feature_dict(o)
            if od["face"] != face:
                continue
            try:
                other_boxes += kit_footprint(o, _get(o, "desc"), others=oth + [{**fd, "face": face}], fasteners=fz, skin=skin, lib_get=lib_get)
            except Exception:
                continue
        for (u, v) in cands:
            f2 = {**fd, "face": face, "at": [round(u, 1), round(v, 1)]}
            mine = kit_footprint(f2, d, others=oth, fasteners=fz, skin=skin, lib_get=lib_get)
            g = float("inf")
            for a in mine:
                for b in other_boxes:
                    if a["part"] == "PRINT_Shell" and b["part"] == "PRINT_Shell":
                        continue
                    if (a["kind"] == "pad" and b["part"] == "PRINT_Shell") or (b["kind"] == "pad" and a["part"] == "PRINT_Shell"):
                        continue
                    g = min(g, box_gap(a, b))
                    if g < margin:
                        break
                if g < margin:
                    break
            if g < margin:
                continue
            if ok is not None and not ok(f2["at"]):
                continue
            return {"at": f2["at"], "face": face, "moved_mm": round(math.hypot(u - u0, v - v0), 1),
                    "min_gap_mm": None if g == float("inf") else round(g, 2)}
        return None

    u0, v0 = fd["at"]
    cands = [tuple(map(float, c)) for c in candidates] if candidates is not None else ring_cands(u0, v0)
    hit = search(fd["face"], u0, v0, cands, fits)
    if hit or not faces:
        return hit
    for face in faces:
        if face == fd["face"] or fits_face is None:
            continue
        c0 = (face_centres or {}).get(face) or [0.0, v0]
        hit = search(face, float(c0[0]), float(c0[1]), ring_cands(float(c0[0]), float(c0[1])), lambda at, _f=face: fits_face(at, _f))
        if hit:
            return hit
    return None


__all__ = ["kit_footprint", "speaker_rotation", "speaker_layout", "clamp_layout", "clamp_z", "tab_angles", "box_gap", "to_world",
           "layout_conflicts", "find_clear_at", "default_fasteners", "module_desc"]
