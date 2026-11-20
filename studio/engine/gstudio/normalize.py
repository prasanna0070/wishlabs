"""Normalization (ARCHITECTURE §3): complete a validated spec and check the fit rules. Owned by foundation.

    from gstudio.normalize import normalize
    spec2, report = normalize(spec_or_dict, lib)          # never raises for design problems; see report["errors"]
    spec2, report = normalize(spec, lib, raise_on_error=True)   # raises SpecError(report["errors"])

It fills `spec.derived` (engine-only):
  instances  U1 core, BP1 backplane, PWR1 USB-C, features (DISP1, SPK1, MIC1, BTN1, LED1, ...), implied AMP1 / SD1,
             extra modules, in-box CBL1 / PSU1; each with its backplane slot and an approximate world position
  harness    one cable per cabled instance: JST-PH pin count + length class 100/150/200/300 mm from a routed-distance estimate
  hardware   backplane screws + inserts, cover screws + inserts, module screws, S1 support-post screws, feet, tie kit,
             speaker gasket, ID label
  cartridge  board origin in world coordinates (flat, centred in X, as far rear as the shell allows), keep-out box
  size_mm / bbox / warnings

Fit rules (errors carry a `fix` the LLM can apply): outline simple (spec.py), size 60-260 mm per axis, printer bed
250 x 210 x 210, the cartridge keep-out box inside the inner shell with 3 mm clearance, every feature module fits its
depth behind its face, features do not overlap each other / decor cut-outs / the cartridge (3 mm aperture margin),
template-required modules, port capacity, I2C address clashes, child-directed rules.

Geometry helpers are public for other stages: ShellGeometry(spec).outer_sdf(p), .inside(p, margin), .bbox,
.surface_depth(face, u, v).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .envelope import fit_orientations, full_envelope, rounded_extrusion_sdf, silhouette_rounds
from .errors import SpecError
from .spec import (
    PRINTED_MIN_SIZE, SCULPT_KINDS, _rot_matrix, BlobBody, DCartridge, DHardware, DHarness, DInstance, Derived, Feature, RevolveBody, SilhouetteBody,
    Spec, cross_check, load_spec, outline_points, polygon_area,
)
from .version import ENGINE_VERSION

SIZE_MIN, SIZE_MAX = 60.0, 260.0
SIZE_MIN_PRINTED = PRINTED_MIN_SIZE  # 20 mm per axis for kind printed (gstudio.printed uses it instead of SIZE_MIN)
BED = (250.0, 210.0, 210.0)
ENVELOPE_MARGIN = 3.0  # mm per axis kept in hand when the envelope is a distance-field estimate (blob, revolve)
CLEAR = 3.0  # cartridge clearance to the inner wall
APERTURE_MARGIN = 3.0
LENGTH_CLASSES = (100, 150, 200, 300)
BASE_PLATE_T = 3.5  # CAD base plate thickness (gstudio.cad.build t_bp)
SEAM_BOSS_MARGIN = 3.6 + 0.8  # rear cartridge boss radius + gap to the rear-cover seam (gstudio.cad.enclosure.place_cartridge)
FACES = ("front", "rear", "left", "right", "top")
NIGHT_LIGHT_RING = "LED_RING_12_WS2812_50MM"  # REVIEW-1 MAJ-29: NIGHT_USE needs a diffused light >= 40 mm across
NIGHT_LIGHT_MIN_MM = 40.0
KIT_MARGIN = 0.5  # mm between kit footprints (gstudio.cad.footprint.layout_conflicts default)
SPEAKER_KIT_FALLBACK = 6.0  # mm around a speaker envelope when gstudio.cad.footprint cannot be imported (cad's fallback)
ROUTE_FACTOR = 1.0  # CAD cable routes measured 0.50-0.96 x the Manhattan estimate (v3 benchmark + r1 example, 10 routes)
ROUTE_SLACK = 40.0  # plug ends + service loop


# =============================================================================================== geometry
def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    qx, qy = ax + t * dx - px, ay + t * dy - py
    return math.sqrt(qx * qx + qy * qy)


def _inside_poly(px, py, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class Poly2D:
    """Signed distance to a closed polygon (negative inside). `skip` = edge indices excluded from the distance."""

    def __init__(self, pts, skip: Optional[set] = None):
        self.pts = list(pts)
        n = len(self.pts)
        self.segs = [(self.pts[i], self.pts[(i + 1) % n]) for i in range(n) if not skip or i not in skip]
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        self.bb = (min(xs), max(xs), min(ys), max(ys))

    def sdf(self, x, y):
        d = min(_seg_dist(x, y, a[0], a[1], b[0], b[1]) for a, b in self.segs)
        return -d if _inside_poly(x, y, self.pts) else d


def _ellipsoid_sdf(px, py, pz, rx, ry, rz):
    k0 = math.sqrt((px / rx) ** 2 + (py / ry) ** 2 + (pz / rz) ** 2)
    k1 = math.sqrt((px / rx ** 2) ** 2 + (py / ry ** 2) ** 2 + (pz / rz ** 2) ** 2)
    if k1 < 1e-9:
        return -min(rx, ry, rz)
    return k0 * (k0 - 1.0) / k1


def _ellipse_sdf(px, py, rx, ry):
    k0 = math.sqrt((px / rx) ** 2 + (py / ry) ** 2)
    k1 = math.sqrt((px / rx ** 2) ** 2 + (py / ry ** 2) ** 2)
    if k1 < 1e-9:
        return -min(rx, ry)
    return k0 * (k0 - 1.0) / k1


def _prim_sdf(pr, x, y, z):
    if _sculpted(pr):  # pack `sculpt` (CATALOG-PLAN 0.5.5, E4)
        try:
            from .sdf_ext import prim_sdf as _ext  # package D: turned primitives and cone, torus, tube
        except ImportError:
            _ext = None
        if _ext is not None:
            return _ext(pr, x, y, z)
        return _turned_box_sdf(pr, x, y, z)  # without sdf_ext: the turned bounding box (never smaller than the shape)
    (cx, cy, cz), (sx, sy, sz) = pr.center, pr.size
    p = [x - cx, y - cy, z - cz]
    h = [sx / 2, sy / 2, sz / 2]
    k = pr.kind
    if k == "rounded_box":
        r = min(pr.radius, min(h) - 0.01)
        q = [abs(p[i]) - (h[i] - r) for i in range(3)]
        out = math.sqrt(sum(max(v, 0.0) ** 2 for v in q))
        return out + min(max(q), 0.0) - r
    if k in ("sphere", "ellipsoid"):
        return _ellipsoid_sdf(p[0], p[1], p[2], h[0], h[1], h[2])
    ax = "xyz".index(pr.axis)
    rad = [i for i in range(3) if i != ax]
    if k == "cylinder":
        dr = _ellipse_sdf(p[rad[0]], p[rad[1]], h[rad[0]], h[rad[1]])
        da = abs(p[ax]) - h[ax]
        return min(max(dr, da), 0.0) + math.sqrt(max(dr, 0.0) ** 2 + max(da, 0.0) ** 2)
    # capsule
    r = min(h[rad[0]], h[rad[1]])
    half = max(h[ax] - r, 0.0)
    q = list(p)
    q[ax] = p[ax] - max(-half, min(half, p[ax]))
    return math.sqrt(sum(v * v for v in q)) - r


def _sculpted(pr) -> bool:
    return pr.kind in SCULPT_KINDS or any(getattr(pr, "rot", None) or ())


def _prim_bounds(pr) -> tuple:
    """World bounds of a blob primitive: package D's exact sdf_ext.prim_bounds for sculpted ones when it has landed,
    else the primitive's own (turned, conservative) bounding box."""
    if _sculpted(pr):
        try:
            from .sdf_ext import prim_bounds as _bounds
        except ImportError:
            _bounds = None
        if _bounds is not None:
            return tuple(_bounds(pr))
    return pr.aabb()


def _turned_box_sdf(pr, x, y, z):
    """Exact distance to the primitive's bounding box turned by pr.rot (a box SDF in the primitive's own frame)."""
    (cx, cy, cz), (sx, sy, sz) = pr.center, pr.size
    R = _rot_matrix(getattr(pr, "rot", None) or (0.0, 0.0, 0.0))
    d = (x - cx, y - cy, z - cz)
    p = [sum(R[j][i] * d[j] for j in range(3)) for i in range(3)]  # R^T d
    q = [abs(p[i]) - h for i, h in enumerate((sx / 2, sy / 2, sz / 2))]
    return math.sqrt(sum(max(v, 0.0) ** 2 for v in q)) + min(max(q), 0.0)


class ShellGeometry:
    """Approximate outer shape of the body as a signed distance field (mm, world coordinates)."""

    def __init__(self, spec: Spec):
        self.spec = spec
        b = spec.body
        self.body = b
        self.wall = b.wall
        self.family = b.family
        self.rounds = None  # silhouette (front, rear) perimeter rounds, for fit_sdf
        if isinstance(b, SilhouetteBody):
            self.poly = Poly2D(outline_points(b, 6))
            x0, x1, _, z1 = self.poly.bb
            self.bbox = (x0, x1, -b.depth / 2, b.depth / 2, 0.0, z1)
            self.rounds = silhouette_rounds(b)
        elif isinstance(b, RevolveBody):
            pts = list(b.profile)
            n = len(pts)
            self.poly = Poly2D(pts, skip={n - 1})  # the closing edge is the axis, not a surface
            self.poly_pts = pts
            rmax = max(r for r, _ in pts)
            self.bbox = (-rmax, rmax, -rmax * b.squash_y, rmax * b.squash_y, 0.0, max(z for _, z in pts))
        elif getattr(b, "family", None) == "csg":  # pack `freeform`: the compiled CSG tree (gstudio.csg, pure Python)
            from .csg import CsgError, compile_tree, solid_checks

            self.csg_info: dict = {}
            try:
                self.csg = compile_tree(b.csg, "body.csg")
            except CsgError:
                self.csg = None  # the spec checks report the tree errors; nothing is inside an invalid tree
            if self.csg is not None:  # cached grid analysis: which hollow piece the CAD keeps (inside() honours it)
                try:
                    solid_checks(b.csg, "printed" if getattr(spec, "kind", "gadget") == "printed" else "gadget", "body.csg",
                                 info=self.csg_info, wall=b.wall)
                except Exception:  # noqa: BLE001 - without it inside() is the plain distance test
                    self.csg_info = {}
            bb = self.csg.bounds if self.csg is not None else None
            self.bbox = tuple(bb) if bb else (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        else:
            boxes = [_prim_bounds(p) for p in b.primitives]
            self.bbox = (min(q[0] for q in boxes), max(q[1] for q in boxes), min(q[2] for q in boxes),
                         max(q[3] for q in boxes), 0.0, max(q[5] for q in boxes))
        # inner floor: the shell's own bottom skin (rear_cover) or the top of the CAD's 3.5 mm base plate (base_plate)
        self.floor_z = BASE_PLATE_T if b.split == "base_plate" else self.wall

    # ---- distance field
    def outer_sdf(self, x: float, y: float, z: float) -> float:
        b = self.body
        if isinstance(b, SilhouetteBody):
            return max(self.poly.sdf(x, z), abs(y) - b.depth / 2)
        if isinstance(b, RevolveBody):
            rho = math.sqrt(x * x + (y / b.squash_y) ** 2)
            d = self.poly.sdf(rho, z)
            return d * min(1.0, b.squash_y) if d < 0 else d
        if b.family == "csg":
            return self.csg.sdf(x, y, z) if self.csg is not None else 1.0
        d = min(_prim_sdf(p, x, y, z) for p in b.primitives)
        return max(d, -z)

    def fit_sdf(self, x: float, y: float, z: float) -> float:
        """outer_sdf with a silhouette's front and rear perimeter rounds (envelope.rounded_extrusion_sdf), for fit checks
        next to those rounds: the core cartridge sits on the floor right behind the front-bottom round."""
        if self.rounds is not None:
            return rounded_extrusion_sdf(self.poly.sdf(x, z), y, self.body.depth, self.rounds[0], self.rounds[1])
        return self.outer_sdf(x, y, z)

    def inside(self, x: float, y: float, z: float, margin: float = 0.0) -> bool:
        """True when the point lies in the inner cavity with `margin` mm to the inner wall surface."""
        ok = self.outer_sdf(x, y, z) <= -(self.wall + margin) and z >= self.floor_z + margin - 1e-9
        if ok and self.family == "csg":  # a csg piece whose hollow does not join the main one prints solid (cad.csg_body)
            from .csg import in_main_hollow

            return in_main_hollow(getattr(self, "csg_info", None) or {}, x, y, z)
        return ok

    def solid(self, x: float, y: float, z: float) -> bool:
        return self.outer_sdf(x, y, z) <= 0.0

    # ---- rays
    def surface_depth(self, face: str, u: float, v: float, far: float = 400.0) -> Optional[float]:
        """Distance from the face's bounding plane to the outer surface along the inward ray at face coords (u, v),
        and the 3D hit point. Returns None if the ray misses."""
        o, d = self._ray(face, u, v, far)
        t = 0.0
        for _ in range(400):
            p = (o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t)
            s = self.outer_sdf(*p)
            if s <= 0.05:
                return t
            t += max(s * 0.9, 0.25)
            if t > 2 * far:
                return None
        return None

    def _ray(self, face, u, v, far):
        if face == "front":
            return (u, -far, v), (0.0, 1.0, 0.0)
        if face == "rear":
            return (u, far, v), (0.0, -1.0, 0.0)
        if face == "top":
            return (u, v, far), (0.0, 0.0, -1.0)
        if face == "left":  # robot's left = +X side
            return (far, u, v), (-1.0, 0.0, 0.0)
        return (-far, u, v), (1.0, 0.0, 0.0)  # right = -X side

    def point(self, face, u, v, s, far=400.0):
        """World point at inward distance s from the face's start plane (o + d*s)."""
        o, d = self._ray(face, u, v, far)
        return (o[0] + d[0] * s, o[1] + d[1] * s, o[2] + d[2] * s)


# =============================================================================================== small helpers
def _box(c, half):
    return (c[0] - half[0], c[0] + half[0], c[1] - half[1], c[1] + half[1], c[2] - half[2], c[2] + half[2])


def _overlap3(a, b, gap=0.0):
    return (a[0] < b[1] + gap and b[0] < a[1] + gap and a[2] < b[3] + gap and b[2] < a[3] + gap
            and a[4] < b[5] + gap and b[4] < a[5] + gap)


def _overlap2(a, b, gap=0.0):
    return a[0] < b[1] + gap and b[0] < a[1] + gap and a[2] < b[3] + gap and b[2] < a[3] + gap


def _err(errors, path, msg, fix=None, code=None):
    e = {"path": path, "msg": msg}
    if fix:
        e["fix"] = fix
    if code:
        e["code"] = code
    errors.append(e)


def _r(v, n=1):
    return round(float(v), n)


# =============================================================================================== templates
TEMPLATE_RULES = {
    "story_player": {"need": [("speaker", "a speaker feature (stories are played aloud)"), ("button", "a button feature (tap = story)")],
                     "recommend": [("led", "a night-light LED feature")]},
    "desk_companion": {"need": [("display", "a display feature for the face")], "need_any": [(("button", "touch"), "a button or touch control")],
                       "recommend": [("speaker", "a speaker for chimes"), ("led", "a light")]},
    # need_any: (roles, why, why when a role's pack is off): roles with no visible library row are left out of the fix
    "notifier_clock": {"need": [("display", "a display feature for the time")],
                       "need_any": [(("led", "servo"), "an LED or a servo flag for notifications", "an LED feature for notifications")],
                       "recommend": [("buzzer", "a buzzer or speaker for alerts")]},
    "ambient_light": {"need": [("led", "an LED (ring or strip) feature")], "need_any": [(("button", "touch"), "a button or touch control")],
                      "recommend": [("light_sensor", "a light sensor for auto-dim")]},
    "sensor_display": {"need": [("display", "a display feature")],
                       "need_any": [(("temp_humidity", "pressure", "light_sensor", "soil_moisture", "level_switch", "air_quality"),
                                     "at least one sensor (environment, air quality, soil moisture or water level)",
                                     "at least one environment sensor")]},
    # pack motion2 (CATALOG-PLAN E4): a known tag or a button opens a servo latch for a few seconds
    "latch": {"need": [("servo", "a servo arm for the latch")],
              "need_any": [(("nfc", "button", "touch"), "a tag reader, button or touch control to open it",
                            "a button or touch control to open it")]},
}
ACTUATOR_ROLES = ("servo", "servo_continuous")  # CATALOG-PLAN E1.5: never in a child-directed design
# parts that only the sensor_display firmware reads (pack `sensing`, hand-off c-to-e 3): elsewhere they would be bought,
# wired and never read
SENSOR_DISPLAY_ONLY = {"soil_moisture": "soil probe", "level_switch": "float switch"}


def _role_available(lib, role: str) -> bool:
    """True when the agent can design with a row of this role (its parts table lists it; pack rows only while on)."""
    fn = getattr(lib, "role_available", None)
    if callable(fn):
        return bool(fn(role))
    return any((x.get("backplane_v1") or {}).get("supported") for x in lib.by_role(role))


# =============================================================================================== main
def _bare_report(errors: list[dict]) -> dict:
    """The report shape for a spec that cannot be normalized at all (same keys as a full report)."""
    return {"ok": not errors, "errors": errors, "warnings": [], "added": [], "nudges": [], "size_mm": [0.0, 0.0, 0.0],
            "envelope": {"bbox": [0.0] * 6, "source": "none", "fits_upright": False, "fits_any_orientation": False, "ears": []},
            "instances": [], "slots": {}}


def normalize(spec: Any, lib, raise_on_error: bool = False) -> tuple[Spec, dict]:
    sp = load_spec(spec) if not isinstance(spec, Spec) else spec
    if getattr(sp, "kind", "gadget") == "printed":  # CATALOG-PLAN 0.5.5: printed objects (pack `printed`, gstudio.printed)
        try:
            from .printed import normalize_printed
        except ImportError:
            errs = list(cross_check(sp)) + [{"path": "kind", "msg": "a printed object with no electronics (kind 'printed') is "
                                             "not available yet", "fix": "design a gadget (kind 'gadget')", "code": "not_available"}]
            if raise_on_error:
                raise SpecError(errs)
            return sp, _bare_report(errs)
        return normalize_printed(sp, lib, raise_on_error)
    sp = sp.model_copy(deep=True)
    errors: list[dict] = list(cross_check(sp))
    if sp.behaviour is None or (sp.body.family == "csg" and any(  # pack `freeform`: no geometry from a bad CSG tree
            str(e.get("path", "")).startswith("body.csg") or (e.get("path") == "body.family") for e in errors)):
        if raise_on_error:
            raise SpecError(errors)
        return sp, _bare_report(errors)
    warnings: list[str] = []
    added: list[str] = []
    bp = lib.backplane

    # ---------------------------------------------------------------- modules exist and are allowed
    def module(mid, path, role_hint=None):
        if not lib.has(mid):
            sug = lib.suggest(mid, role=role_hint)
            _err(errors, path, f"module '{mid}' is not in the library", f"use one of: {', '.join(sug)}" if sug else "pick an id from the library table",
                 "unknown_module")
            return None
        return lib.get(mid)

    core = module(sp.electronics.core, "electronics.core", "core")
    if core is not None and core.get("role") != "core":
        _err(errors, "electronics.core", f"{core['id']} is a {core.get('role')}, not a core board", "use CORE_ESP32S3_DEVKITC1_N8R8")
    feat_mods: dict[str, dict] = {}
    for k, f in enumerate(sp.features):
        m = module(f.module, f"features[{k}].module")
        if m is None:
            continue
        ap = m.get("aperture") or {}
        if not ap.get("feature_allowed"):
            what = "implied automatically" if m.get("role") in ("amp", "storage") else "internal"
            _err(errors, f"features[{k}].module", f"{m['id']} ({m.get('role')}) has no aperture; it is {what}",
                 "remove it from features" + ("" if what.startswith("implied") else " and list it in electronics.extra_modules"))
            continue
        if not (m.get("backplane_v1") or {}).get("supported"):
            alt = [x["id"] for x in lib.by_role(m.get("role")) if (x.get("backplane_v1") or {}).get("supported")]
            _err(errors, f"features[{k}].module", f"{m['id']} cannot connect on backplane-v1: {(m.get('backplane_v1') or {}).get('why', '')}",
                 f"use {', '.join(alt)}" if alt else "drop this feature", "unsupported_module")
            continue
        if f.grille is not None and m.get("role") not in ("speaker", "buzzer", "mic", "fan", "temp_humidity", "pressure"):
            _err(errors, f"features[{k}].grille", f"a grille is only for speaker, buzzer, mic or vent features, not {m.get('role')}",
                 "remove the grille")
        if m.get("role") == "speaker" and f.grille is None:
            from .spec import Grille

            s = (m.get("aperture") or {}).get("size_mm") or {}
            w = float(s.get("w") or (m.get("face_mm") or [30, 30])[0] * 0.85)
            h = float(s.get("h") or w)
            sp.features[k].grille = Grille(pattern="hex_holes", w=max(8.0, min(150.0, w)), h=max(8.0, min(150.0, h)), hole=2.6, pitch=4.4)
            added.append(f"features[{k}].grille: default hex grille {w:g} x {h:g} mm over the speaker opening")
        feat_mods[f.id] = m
    extra_mods: list[tuple[int, dict]] = []
    for k, mid in enumerate(sp.electronics.extra_modules):
        m = module(mid, f"electronics.extra_modules[{k}]")
        if m is None:
            continue
        role = m.get("role")
        if role in ("amp", "storage"):
            warnings.append(f"electronics.extra_modules[{k}]: {m['id']} is implied automatically; listed once")
            continue
        if role in ("temp_humidity", "pressure"):
            warnings.append(f"electronics.extra_modules[{k}]: {m['id']} reads the air inside the shell (warmed by the electronics); "
                            "a vent feature on the face reads the room more accurately")
        elif (m.get("aperture") or {}).get("required") and role not in ("touch",):
            _err(errors, f"electronics.extra_modules[{k}]", f"{m['id']} ({role}) needs an opening in the shell",
                 "move it to features with a face and position")
            continue
        if not (m.get("backplane_v1") or {}).get("supported"):
            _err(errors, f"electronics.extra_modules[{k}]", f"{m['id']} cannot connect on backplane-v1: {(m.get('backplane_v1') or {}).get('why', '')}",
                 "remove it", "unsupported_module")
            continue
        extra_mods.append((k, m))

    # ---------------------------------------------------------------- NIGHT_USE: a diffused light >= 40 mm (REVIEW-1 MAJ-29)
    night: list[int] = []
    if "NIGHT_USE" in set(sp.safety.flags) and lib.has(NIGHT_LIGHT_RING):
        leds = [(k, f) for k, f in enumerate(sp.features) if (feat_mods.get(f.id) or {}).get("role") == "led"]
        for k, f in leds:
            m = feat_mods[f.id]
            if min(float(x or 0) for x in (m.get("face_mm") or [0, 0])) >= NIGHT_LIGHT_MIN_MM:
                continue
            old_mod = f.module
            sp.features[k].module = NIGHT_LIGHT_RING
            feat_mods[f.id] = lib.get(NIGHT_LIGHT_RING)
            night.append(k)
            added.append(f"features[{k}].module: {old_mod} -> {NIGHT_LIGHT_RING} (NIGHT_USE needs a diffused light at least "
                         f"{NIGHT_LIGHT_MIN_MM:g} mm across; the firmware caps its brightness)")
        if not leds:
            warnings.append(f"NIGHT_USE: no light feature; add a {NIGHT_LIGHT_RING} feature (a diffused night light at least "
                            f"{NIGHT_LIGHT_MIN_MM:g} mm across)")
    roles_present = [m.get("role") for m in feat_mods.values()] + [m.get("role") for _, m in extra_mods]

    # ---------------------------------------------------------------- template rules
    tmpl = sp.behaviour.template
    rules = TEMPLATE_RULES.get(tmpl, {})
    for role, why in rules.get("need", []):
        if role not in roles_present:
            cand = ", ".join(x["id"] for x in lib.by_role(role) if (x.get("aperture") or {}).get("feature_allowed")
                             and (x.get("backplane_v1") or {}).get("supported"))
            _err(errors, "features", f"{tmpl} needs {why}", f"add a feature with module {cand}" if cand else None, "template_requirement")
    for rule in rules.get("need_any", []):
        roles, why = rule[0], rule[1]
        if not any(r in roles_present for r in roles):
            avail = [r for r in roles if _role_available(lib, r)] or list(roles)
            if len(avail) < len(roles) and len(rule) > 2:
                why = rule[2]
            cand = [x["id"] for r in avail for x in lib.by_role(r) if (x.get("aperture") or {}).get("feature_allowed")
                    and (x.get("backplane_v1") or {}).get("supported")]
            _err(errors, "features", f"{tmpl} needs {why}", f"add a feature whose module role is one of {', '.join(avail)}"
                 + (f" (e.g. {', '.join(cand[:6])})" if cand else ""), "template_requirement")
    only = [SENSOR_DISPLAY_ONLY[r] for r in sorted(set(roles_present) & set(SENSOR_DISPLAY_ONLY))]
    if only and tmpl != "sensor_display":
        _err(errors, "behaviour.template", f"a {' and a '.join(only)} {'are' if len(only) > 1 else 'is'} read only by the "
             "sensor_display behaviour", "set behaviour.template to sensor_display, or remove that part", "template_requirement")
    for role, why in rules.get("recommend", []):
        if role not in roles_present:
            warnings.append(f"{tmpl}: consider {why}")
    settings = sp.behaviour.settings or {}
    capture = settings.get("capture_mode")
    if tmpl == "story_player" and capture == "child_voice_clip" and "mic" not in roles_present:
        _err(errors, "behaviour.settings.capture_mode", "child_voice_clip needs a microphone feature",
             "add a mic feature (MIC_INMP441_I2S) or use capture_mode parent_web", "template_requirement")
    # firmware-owned settings validation (optional hook)
    try:
        from . import firmware as _fw  # type: ignore

        vfn = getattr(_fw, "validate_settings", None)
    except Exception:
        vfn = None
    if callable(vfn):
        try:
            try:  # safety_flags: the consent gate (voice_clip_consent + CHILD_VOICE_STORED) is reported here, not first at firmware
                res = vfn(tmpl, settings, child_directed=sp.audience.child_directed, safety_flags=list(sp.safety.flags))
            except TypeError:
                try:
                    res = vfn(tmpl, settings, child_directed=sp.audience.child_directed)
                except TypeError:
                    res = vfn(tmpl, settings)
            if isinstance(res, tuple) and len(res) == 2:
                new_settings, serrs = res
                if isinstance(new_settings, dict):
                    sp.behaviour.settings = new_settings
            else:
                serrs = res
            for e in serrs or []:
                if isinstance(e, dict):
                    e = dict(e)
                    pth = str(e.get("path") or "")
                    e["path"] = pth if pth.startswith("behaviour.") else "behaviour.settings" + ("." + pth if pth else "")
                    errors.append(e)
                else:
                    txt = str(e)
                    head, sep, rest = txt.partition(": ")
                    if sep and head.startswith("behaviour.") and " " not in head:
                        _err(errors, head, rest, code="settings")
                    else:
                        _err(errors, "behaviour.settings", txt, code="settings")
        except Exception as exc:  # the hook must never crash normalize
            warnings.append(f"firmware.validate_settings failed: {exc}")

    # ---------------------------------------------------------------- safety
    flags = set(sp.safety.flags)
    if "mic" in roles_present and "LOCAL_VOICE_CAPTURE" not in flags:
        flags.add("LOCAL_VOICE_CAPTURE")
        added.append("safety.flags: LOCAL_VOICE_CAPTURE (design has a microphone)")
    if sp.audience.child_directed and "CHILD_DIRECTED" not in flags:
        flags.add("CHILD_DIRECTED")
        added.append("safety.flags: CHILD_DIRECTED (audience.child_directed is true)")
    if sp.audience.child_directed:
        moving = [(f"features[{k}].module", feat_mods[f.id]) for k, f in enumerate(sp.features)
                  if (feat_mods.get(f.id) or {}).get("role") in ACTUATOR_ROLES]
        moving += [(f"electronics.extra_modules[{k}]", m) for k, m in extra_mods if m.get("role") in ACTUATOR_ROLES]
        for pth, m in moving:
            _err(errors, pth, f"no moving parts in a child-directed design: {m['id']} is a {m.get('role')}", "remove the servo",
                 "actuator_child")
    for _, m in extra_mods:
        if m["id"] == "RTC_DS3231_ZS042":
            if sp.audience.child_directed:
                _err(errors, "electronics.extra_modules", "RTC_DS3231_ZS042 carries a coin cell; batteries are not allowed in child-directed designs",
                     "remove the RTC (the clock re-syncs from NTP over Wi-Fi)", "child_safety")
            else:
                warnings.append("RTC_DS3231_ZS042 carries a coin cell: blueprint launch rule says no batteries; prefer NTP time")
                flags.add("COIN_CELL")
    sp.safety.flags = sorted(flags)

    # ---------------------------------------------------------------- instances + refs
    counters: dict[str, int] = {}

    def ref_for(prefix):
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}{counters[prefix]}"

    inst: list[dict] = []
    if core is not None:
        inst.append({"ref": "U1", "module": core["id"], "role": "core", "name": core.get("name", ""), "source": "core", "path": "electronics.core"})
        counters["U"] = 1
    bpm = lib.by_role("backplane")
    if bpm:
        inst.append({"ref": "BP1", "module": bpm[0]["id"], "role": "backplane", "name": bpm[0].get("name", ""), "source": "implied",
                     "reason": "standard core cartridge"})
        counters["BP"] = 1
    pwr = next((m for m in lib.by_role("power_in") if (m.get("backplane_v1") or {}).get("slots") == ["J_PWR"]), None)
    if pwr:
        inst.append({"ref": "PWR1", "module": pwr["id"], "role": "power_in", "name": pwr.get("name", ""), "source": "implied",
                     "slot": "J_PWR", "reason": "USB-C 5 V input on the backplane"})
        counters["PWR"] = 1
    for k, f in enumerate(sp.features):
        m = feat_mods.get(f.id)
        if m is None:
            continue
        inst.append({"ref": ref_for(m.get("ref_prefix") or "M"), "module": m["id"], "role": m.get("role"), "name": m.get("name", ""),
                     "source": "feature", "feature": f.id, "face": f.face, "path": f"features[{k}]"})
    has_speaker = "speaker" in roles_present
    needs_sd = tmpl == "story_player" or bool(settings.get("audio_files") or settings.get("audio_playback"))
    if has_speaker:
        amp = next((m for m in lib.by_role("amp") if (m.get("backplane_v1") or {}).get("supported")), None)
        if amp:
            inst.append({"ref": ref_for("AMP"), "module": amp["id"], "role": "amp", "name": amp.get("name", ""), "source": "implied",
                         "reason": "a speaker needs the I2S amplifier"})
            added.append("AMP1: I2S amplifier (speaker present)")
    elif "amp" in [m.get("role") for _, m in extra_mods]:
        warnings.append("amplifier listed without a speaker")
    if needs_sd:
        sd = next((m for m in lib.by_role("storage") if (m.get("backplane_v1") or {}).get("supported")), None)
        if sd:
            inst.append({"ref": ref_for("SD"), "module": sd["id"], "role": "storage", "name": sd.get("name", ""), "source": "implied",
                         "reason": "story audio files live on microSD" if tmpl == "story_player" else "audio file playback"})
            added.append(f"{inst[-1]['ref']}: microSD module ({'story_player' if tmpl == 'story_player' else 'audio playback'})")
    for k, m in extra_mods:
        inst.append({"ref": ref_for(m.get("ref_prefix") or "M"), "module": m["id"], "role": m.get("role"), "name": m.get("name", ""),
                     "source": "extra", "path": f"electronics.extra_modules[{k}]"})
    # parts a module needs bought with it (library `requires`, REVIEW-1 BLK-5: the microSD card for SD1)
    for i in list(inst):
        for req in (lib.get(i["module"]).get("requires") or []):
            if not isinstance(req, dict) or not req.get("module") or not lib.has(req["module"]):
                if isinstance(req, dict) and req.get("module"):
                    warnings.append(f"{i['ref']}: required part {req['module']} is not in the library")
                continue
            rm = lib.get(req["module"])
            for _ in range(int(req.get("qty") or 1)):
                inst.append({"ref": ref_for(rm.get("ref_prefix") or "M"), "module": rm["id"], "role": rm.get("role"), "name": rm.get("name", ""),
                             "source": "implied", "required_by": i["ref"],
                             "reason": f"required by {i['ref']} ({i['module']}): {req.get('why') or 'library requires'}"})
                added.append(f"{inst[-1]['ref']}: {rm['id']} (required by {i['ref']})")
    # in the box
    cbl = next(iter(lib.by_role("usb_cable")), None)
    if cbl:
        inst.append({"ref": "CBL1", "module": cbl["id"], "role": "usb_cable", "name": cbl.get("name", ""), "source": "implied",
                     "reason": "USB-C cable in the box"})
    if sp.audience.child_directed and not sp.electronics.power.adapter_in_box:
        sp.electronics.power.adapter_in_box = True
        added.append("electronics.power.adapter_in_box: true (child-directed products ship with a certified 5 V USB-C adapter; REVIEW-1 MAJ-5)")
    if sp.electronics.power.adapter_in_box:
        psu = next(iter(lib.by_role("adapter")), None)
        if psu:
            inst.append({"ref": "PSU1", "module": psu["id"], "role": "adapter", "name": psu.get("name", ""), "source": "implied",
                         "reason": "adapter_in_box"})

    # ---------------------------------------------------------------- slots
    from .electrical import assign_slots

    cand = [i for i in inst if i["source"] in ("feature", "implied", "extra")
            and i["role"] not in ("backplane", "usb_cable", "adapter", "power_in", "storage_card")]
    smap, serr = assign_slots(cand, lib)
    errors += serr
    for i in inst:
        if i["ref"] in smap:
            i["slot"] = smap[i["ref"]]
    if core is not None:
        inst[0]["slot"] = "U1"
    # J_LED holds an LED or a servo, not both (single slot already enforced); I2C clashes
    addr_owner: dict[str, str] = {}
    for i in inst:
        if not (i.get("slot") or "").startswith("J_I2C"):
            continue
        for a in (lib.get(i["module"]).get("electrical") or {}).get("i2c_addr") or []:
            if a in addr_owner:
                other = addr_owner[a]
                fix = "remove one of them"
                if {"imu", "rtc"} >= {i["role"], next(x["role"] for x in inst if x["ref"] == other)}:
                    fix = "remove the RTC (time comes from NTP) or the IMU; strapping the IMU to 0x69 is not modelled yet"
                _err(errors, i.get("path", "features"), f"I2C address {a} clash between {other} and {i['ref']} ({i['module']})", fix, "i2c_clash")
            else:
                addr_owner[a] = i["ref"]

    # ---------------------------------------------------------------- geometry
    geom = ShellGeometry(sp)
    # the full envelope the CAD builds: its own outline spline plus every ear and antenna where decor.apply_ears puts them
    # (live failure 2026-11-02: the old estimate said 148 x 105 x 195 mm for a shell the CAD made 167.6 x 97.6 x 212.7 mm)
    env = full_envelope(sp, geom)
    if sp.body.family == "csg":  # pack `freeform`: engine adjustments, and pieces that print solid
        warnings += list(getattr(getattr(geom, "csg", None), "notes", None) or [])
        for pc in (getattr(geom, "csg_info", None) or {}).get("solid_pieces") or []:
            warnings.append(f"body.csg: {pc['label']} prints solid ({pc['cm3']:.0f} cm3 of plastic): its hollow does not join the "
                            f"main hollow, so nothing can mount behind its faces; overlap it with the main body by at least "
                            f"{2 * sp.body.wall + 1:.0f} mm if it should be hollow")
    x0, x1, y0, y1, z0, z1 = env["bbox"]
    warnings += [f"envelope: {n}" for n in env["notes"]]
    size = (x1 - x0, y1 - y0, z1 - z0)
    if getattr(getattr(sp, "mount", None), "kind", "desk") == "wall":  # pack `mount`: room for the keyholes (d-to-e 3)
        from . import envelope as _envelope

        wk_fn = getattr(_envelope, "wall_keyhole_room", None)  # package D's pure-Python check, when it has landed
        if callable(wk_fn):
            try:
                wk = wk_fn(sp) or {}
                if not wk.get("ok", True):
                    _err(errors, "mount", str(wk.get("why") or "no room for the wall keyholes on the back"), wk.get("fix"),
                         "wall_mount_room")
            except Exception as exc:  # never crash normalize
                warnings.append(f"wall keyhole room not checked: {exc}")
    for axis, v in zip("XYZ", size):
        if v < SIZE_MIN - 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; minimum is {SIZE_MIN:g} mm", "make the body bigger", "size")
        if v > SIZE_MAX + 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; maximum is {SIZE_MAX:g} mm", "scale the body down", "size")
    # silhouette bodies reproduce the CAD within ~0.3 mm; the distance-field estimate of blob/revolve bodies ran up to 2.7 mm
    # small (desk-buddy ears vs its CAD preview, 2026-11-02), so those keep 3 mm per axis in hand for the printer fit
    fit_margin = 0.0 if env["source"].startswith("CAD spline") else ENVELOPE_MARGIN
    fit = fit_orientations(tuple(v + fit_margin for v in size), BED)
    body_top = env["body_bbox"][5]
    ear_top = max((e["bounds"][3] for e in env["ears"]), default=None)
    sticks = (f"; the ears/antennae reach z {ear_top:.0f} mm, {ear_top - body_top:.0f} mm above the {body_top:.0f} mm body top"
              if ear_top is not None and ear_top > body_top + 0.5 else "")
    dims = f"{size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm"
    if not fit["any"]:
        k = math.floor(fit["scale_to_fit"] * 0.98 * 100) / 100  # 2 % margin on the CAD estimate
        _err(errors, "body", f"the full envelope (body, ears and antennae, as the CAD builds them) is {dims} and does not fit the "
             f"{BED[0]:.0f} x {BED[1]:.0f} x {BED[2]:.0f} mm printer in any orientation{sticks}",
             f"scale the whole design by {k:.2f} or less (multiply " + ("every length in body.csg (sizes, radii, heights, outline "
             "points and move positions)" if sp.body.family == "csg" else "every body.outline point, body.depth") +
             f" and every decor and feature size and position by {k:.2f}) so the longest side is <= {BED[0]:.0f} mm and the other two <= {BED[1]:.0f} mm"
             + (", or make the ears/antennae shorter (decor size[1])" if sticks else ""), "bed")
    elif not fit["upright"]:
        k = math.floor(fit["scale_to_fit_upright"] * 0.98 * 100) / 100
        top_ok = BED[2] - 2.0  # 2 mm margin on the CAD estimate
        tall_only = size[2] + fit_margin > BED[2] and fit["scale_to_fit_upright"] >= min(1.0, BED[2] / (size[2] + fit_margin)) - 1e-9
        if sticks and tall_only and body_top <= top_ok:  # only the ears/antennae push it over: the cheap repair first
            how = (f"make the ears/antennae at least {math.ceil(ear_top - top_ok)} mm shorter (decor size[1]) so the top stays at or "
                   f"below {top_ok:.0f} mm, or scale the whole design by {k:.2f} (then re-check the feature layout)")
        else:
            how = f"scale the whole design by {k:.2f} (then re-check the feature layout) or keep the top at or below {top_ok:.0f} mm"
        warnings.append(f"bed: the full envelope (incl. ears/antennae) is {dims}{sticks}; it does not fit the {BED[0]:.0f} x "
                        f"{BED[1]:.0f} x {BED[2]:.0f} mm printer upright, so the shell prints on its side with supports. To print it "
                        f"upright, {how}")

    # ---------------------------------------------------------------- cartridge placement
    cart = bp["cartridge"]
    W, H = bp["board"]["size"]
    kx0, kx1, ky0, ky1 = cart["keepout_plan_board"]
    kz = cart["keepout_height_above_floor"]
    floor = geom.floor_z
    rear_gap = cart.get("rear_gap_mm", 0.2)
    cart_box = None
    y_off = None
    if not any(e.get("code") in ("size",) for e in errors):
        kb = cart.get("keepout_bottom_above_floor", 0.0)
        bosses = [(h["at"][0] - W / 2, h["at"][1], cart["boss"]["od"] / 2) for h in cart["holes"]]
        y_seam = seam_y(geom, W, floor) if sp.body.split == "rear_cover" else None
        y_cap = None
        if y_seam is not None:
            holes_y = max(h["at"][1] for h in cart["holes"])
            y_cap = y_seam - SEAM_BOSS_MARGIN - holes_y
        usb_slot = lib.slot("J_PWR")
        usb_bx = (usb_slot["courtyard"][0] + usb_slot["courtyard"][1]) / 2
        ctrace = [] if sp.body.family == "csg" else None  # pack `freeform`: name the binding place, not the rear wall
        y_off, cart_box, why = _place_cartridge(geom, W, (kx0, kx1, ky0, ky1), floor, kz, rear_gap, kb, bosses,
                                                cart["board_bottom_above_floor"], y_cap, usb_bx=usb_bx,
                                                base_plate=sp.body.split == "base_plate", trace=ctrace)
        if y_off is not None:
            warnings += boss_warnings(geom, bosses, y_off, floor, cart["board_bottom_above_floor"])
        if y_off is None:
            need_w = (kx1 - kx0) + 2 * CLEAR + 2 * geom.wall
            need_d = (ky1 - ky0) + CLEAR + rear_gap + 2 * geom.wall
            fix = f"make the lower body at least {need_w:.0f} mm wide and {need_d:.0f} mm deep for the first {kz + floor:.0f} mm of height"
            if isinstance(sp.body, SilhouetteBody) and "base-plate opening" not in why:
                alt = _silhouette_cartridge_fix(sp, W, (kx0, kx1, ky0, ky1), floor, kz, rear_gap, kb, bosses,
                                                cart["board_bottom_above_floor"], cart["holes"], usb_bx)
                if alt:
                    fix = f"{alt} (the front edge round takes room at the floor); or {fix}"
            if "base-plate opening" in why:
                fix = (f"the board goes in through the base-plate opening: make the flat bottom at least {kx1 - kx0 + 2 * geom.wall:.0f} x "
                       f"{ky1 - ky0 + 2 * geom.wall:.0f} mm (smaller bottom fillets), or use split rear_cover")
            if ctrace and "base-plate opening" not in why:
                why, fix = _csg_cartridge_text(ctrace, geom, why, fix)
            _err(errors, "body", f"the core cartridge ({kx1 - kx0:.0f} x {ky1 - ky0:.0f} x {kz:.0f} mm keep-out) does not fit inside the shell: {why}",
                 fix, "cartridge_fit")
    derived_cart = None
    if y_off is not None:
        board_top = floor + cart["board_top_above_floor"]
        usb_center = (0.0 + (cart["usb_c_opening"]["center_board_x"] - W / 2), y_off + ky1, board_top + cart["usb_c_opening"]["center_above_board_top"])
        # gap from the receptacle face to the outer surface behind it
        t = geom.surface_depth("rear", usb_center[0], usb_center[2])
        wall_gap = None
        if t is not None:
            outer_y = 400.0 - t
            wall_gap = outer_y - (y_off + ky1)  # ky1 = receptacle front face (board frame) -> CAD's 13 x 7 plug tunnel length
            if wall_gap > 15.0:
                warnings.append(f"USB-C plug tunnel is {wall_gap:.1f} mm long (outer rear surface to receptacle face): the plug "
                                "overmold passes through the 13 x 7 tunnel, but little of it is left to grip; a flatter rear helps")
        derived_cart = DCartridge(
            backplane="backplane-v1",
            board_origin=(_r(-W / 2, 3), _r(y_off, 3), _r(floor + cart["board_bottom_above_floor"], 3)),
            rotation_deg=0.0,
            floor_z=_r(floor, 3),
            board_top_z=_r(board_top, 3),
            board_size=(W, H),
            keepout_box=tuple(_r(v, 2) for v in cart_box),
            usb_c_center=tuple(_r(v, 2) for v in usb_center),
            usb_c_wall_gap=None if wall_gap is None else _r(wall_gap, 2),
            usb_c_tunnel_mm=None if wall_gap is None else _r(wall_gap, 2),
            usb_c_tunnel_size=(cart["usb_c_opening"]["w"], cart["usb_c_opening"]["h"]),
            split=sp.body.split,
            seam_y=None if y_seam is None else _r(y_seam, 2),
        )

    # ---------------------------------------------------------------- features: placement, depth, overlaps
    fboxes: dict[str, tuple] = {}
    frects: dict[str, tuple] = {}
    positions: dict[str, tuple] = {}
    order = [k for k in range(len(sp.features)) if k not in night] + night
    for k in order:
        f = sp.features[k]
        m = feat_mods.get(f.id)
        if m is None:
            continue
        if f.face == "rear" and sp.body.split == "rear_cover" and _rear_kit_crosses_seam(geom, sp, f, m, W, floor):
            # integration (e2e r1): the rear wall is the cover; a rear kit deeper than the cover is cut by the seam and its inner
            # half is dropped as loose fragments (print_single_solid) with no fit coupon. Until the CAD mounts kits on the
            # cover, such a module goes on another face.
            _err(errors, f"features[{k}].face",
                 f"{f.id}: a module on the rear face of a rear_cover design sits on the removable cover, which the CAD does not "
                 "mount modules on yet (the kit would be cut by the cover seam)",
                 f"put {f.id} on the front, left, right or top face, or use body.split base_plate", "rear_cover_face")
            continue
        if k in night:
            moved = _place_night_light(sp, k, geom, m, lib, fboxes, frects, cart_box, errors, added)
            if moved is False:
                continue
            f = sp.features[k]
        res = _place_feature(geom, f, m, k, errors, warnings)
        if res is None:
            continue
        box, rect, center = res
        fboxes[f.id] = box
        frects[f.id] = rect
        positions[f.id] = center
        if cart_box is not None and _overlap3(box, cart_box, 1.0):
            _err(errors, f"features[{k}].at", f"{f.id}: the {m['id']} module (reaches z {box[4]:.0f}-{box[5]:.0f}, y {box[2]:.0f}-{box[3]:.0f}) "
                 f"collides with the core cartridge (z up to {cart_box[5]:.0f}, y {cart_box[2]:.0f}-{cart_box[3]:.0f})",
                 f"move it up to z >= {cart_box[5] + (box[5] - box[4]) / 2 + 2:.0f} or toward the front", "feature_cartridge")
    ids = list(fboxes)
    clashed: set = set()
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            fa, fb = sp.feature(ids[a]), sp.feature(ids[b])
            if fa.face == fb.face and _overlap2(frects[ids[a]], frects[ids[b]], APERTURE_MARGIN):
                _err(errors, f"features[{sp.features.index(fb)}].at", f"{fb.id} opening overlaps {fa.id} (need {APERTURE_MARGIN:g} mm between openings)",
                     "move one of them apart", "feature_overlap")
                clashed.add(frozenset((fa.id, fb.id)))
            elif _overlap3(fboxes[ids[a]], fboxes[ids[b]], 1.0):
                _err(errors, f"features[{sp.features.index(fb)}].at", f"{fb.id} module collides with the {fa.id} module behind the wall",
                     "move one of them apart or onto another face", "feature_overlap")
                clashed.add(frozenset((fa.id, fb.id)))
    # kit footprints (retainer, bosses, clip strap, speaker clamp ring and tabs), REVIEW-1 MAJ-8
    placed = [sp.features[k] for k in range(len(sp.features)) if sp.features[k].id in fboxes]
    nudges = kit_conflicts(sp, lib, placed, fboxes)
    for c in kit_shell_conflicts(sp, lib, geom, placed):
        _err(errors, f"features[{sp.features.index(sp.feature(c['feature']))}].at", c["msg"], c["fix"], "kit_shell")
    if sp.body.split == "rear_cover":
        for c in kit_seam_conflicts(sp, lib, geom, placed, seam_y(geom, W, floor)):
            _err(errors, f"features[{sp.features.index(sp.feature(c['feature']))}].at", c["msg"], c["fix"], "kit_seam")
    for c in nudges:
        if frozenset((c["a"], c["b"])) in clashed:
            continue
        fb = sp.feature(c["b"])
        _err(errors, f"features[{sp.features.index(fb)}].at",
             f"{c['b']} kit ({c['b_what']}) {'touches' if c['gap_mm'] >= 0 else 'overlaps'} the {c['a']} kit ({c['a_what']})"
             + (f" by {-c['gap_mm']:.1f} mm" if c["gap_mm"] < 0 else f" ({c['gap_mm']:.1f} mm apart, need {KIT_MARGIN:g})"),
             c["nudge"], "kit_overlap")
    # decor cut-outs vs front openings
    for dk, d in enumerate(sp.decor):
        if d.type in ("face_plate", "ear"):
            continue
        rects = []
        w, h = (d.size if hasattr(d, "size") else (len(getattr(d, "text", "")) * d.height * 0.7, d.height))
        rects.append((d.at[0] - w / 2, d.at[0] + w / 2, d.at[1] - h / 2, d.at[1] + h / 2))
        if getattr(d, "side", None) == "both":
            rects.append((-d.at[0] - w / 2, -d.at[0] + w / 2, d.at[1] - h / 2, d.at[1] + h / 2))
        for fid, rr in frects.items():
            if sp.feature(fid).face != "front":
                continue
            if any(_overlap2(rc, rr, APERTURE_MARGIN) for rc in rects):
                _err(errors, f"decor[{dk}].at", f"{d.type} overlaps the {fid} opening (need {APERTURE_MARGIN:g} mm)",
                     "move the decor or the feature", "decor_overlap")
        # decor should sit on the body
        if geom.surface_depth("front", d.at[0], d.at[1]) is None:
            warnings.append(f"decor[{dk}] ({d.type}) at {list(d.at)} is outside the front silhouette")

    # ---------------------------------------------------------------- positions of implied / internal instances
    board_pos = {}
    if y_off is not None:
        bz = floor + cart["board_top_above_floor"]
        for s in bp["slots"]:
            plan = s.get("module_plan") or s.get("devkit_pcb_plan") or s.get("courtyard")
            if plan:
                cx, cy = (plan[0] + plan[1]) / 2 - W / 2, (plan[2] + plan[3]) / 2 + y_off
                board_pos[s["id"]] = (_r(cx), _r(cy), _r(bz + 6.0))
                if s["kind"] == "port":
                    board_pos[s["id"]] = (_r(cx), _r(cy), _r(bz + 3.0))
    for i in inst:
        if i.get("feature") and i["feature"] in positions:
            i["position"] = tuple(_r(v) for v in positions[i["feature"]])
        elif i.get("slot") in board_pos and i["role"] not in ("backplane",):
            i["position"] = board_pos[i["slot"]]
        elif i["role"] == "backplane" and y_off is not None:
            i["position"] = (0.0, _r(y_off + H / 2), _r(floor + cart["board_top_above_floor"]))

    # ---------------------------------------------------------------- harness
    harness: list[DHarness] = []
    hid = 0
    for i in inst:
        slot_id = i.get("slot")
        if not slot_id or slot_id in ("U1", "A1", "S1", "J_PWR"):
            continue
        slot = lib.slot(slot_id)
        if slot["kind"] != "port":
            continue
        hid += 1
        start = board_pos.get(slot_id)
        end = i.get("position")
        if start and end:
            # REVIEW-1 MIN-6: the CAD's routed cables measured up to 0.96 x the Manhattan distance, so the estimate is
            # 1.0 x Manhattan + 40 mm for plug ends and a service loop (was 1.25 x + 40); the class is the shortest stocked one above
            dist = (abs(start[0] - end[0]) + abs(start[1] - end[1]) + abs(start[2] - end[2])) * ROUTE_FACTOR + ROUTE_SLACK
        else:
            dist = 150.0
        if ((lib.get(i["module"]).get("aperture") or {}).get("needs")) == "cable_exit":
            # the part sits outside the shell (soil probe, float switch): its lead continues through the cable exit to the
            # pot or tank, so it always gets the longest class (hand-off c-to-e 2); reach outside = 300 mm - route
            dist = max(dist, float(LENGTH_CLASSES[-1]))
        cls = next((c for c in LENGTH_CLASSES if c >= dist), None)
        if cls is None:
            _err(errors, i.get("path", "features"), f"{i['ref']} is about {dist:.0f} mm of cable from {slot_id}; the longest harness is 300 mm",
                 "move the feature closer to the bottom rear of the body", "harness_length")
            cls = LENGTH_CLASSES[-1]
        from .electrical import select_cable

        mm = lib.get(i["module"])
        sel = select_cable(lib, mm, slot, cls)
        hmod = sel["module"]
        if hmod is None:
            warnings.append(f"{i['ref']}: no library harness module for {slot_id} at {cls} mm")
        elif sel["match"] == "fallback":
            warnings.append(f"{i['ref']} ({i['module']}): {sel['why']}")
        kind = "speaker_lead" if slot_id == "J_SPK" else ("jst_xh" if str(slot.get("connector", "")).startswith("JST-XH") else "jst_ph")
        harness.append(DHarness(id=f"H{hid}", ref=i["ref"], slot=slot_id, kind=kind,
                                cable=f"{slot['connector']} {slot['pins_count']}p {cls} mm", pins=slot["pins_count"], length_mm=cls,
                                distance_mm=_r(dist), module=hmod, module_end=_module_end(mm),
                                module_end_family=sel["module_end_family"], sku=sel["sku"], drawing=sel["drawing"],
                                made_to_drawing=sel["made_to_drawing"], crossed=sel["crossed"], match=sel["match"],
                                module_end_order=sel["module_end_order"]))
        i["harness"] = f"H{hid}"

    # ---------------------------------------------------------------- hardware kit (planned; CAD counts win)
    hw = planned_hardware(sp, lib, feat_mods, inst, harness, has_speaker)

    # ---------------------------------------------------------------- antenna rule (metal near the antenna)
    if y_off is not None:
        u1 = lib.slot("U1")
        ax0, ax1, ay0, ay1 = u1["antenna_plan"]
        ant = ((ax0 + ax1) / 2 - W / 2, (ay0 + ay1) / 2 + y_off, floor + cart["board_top_above_floor"] + 13.0)
        for i in inst:
            if i["role"] in ("speaker", "servo") and i.get("position"):
                d = math.dist(ant, i["position"])
                if d < 30.0:
                    warnings.append(f"{i['ref']} ({i['role']}, has metal/magnet) is ~{d:.0f} mm from the Wi-Fi antenna; keep >= 15 mm from its edge")

    # ---------------------------------------------------------------- derived
    dinst = []
    for i in inst:
        dinst.append(DInstance(ref=i["ref"], module=i["module"], role=i["role"] or "", name=i.get("name", ""), source=i["source"],
                               feature=i.get("feature"), slot=i.get("slot"), face=i.get("face"),
                               position=i.get("position"), qty=1, reason=i.get("reason", ""), harness=i.get("harness"),
                               required_by=i.get("required_by")))
    sp.derived = Derived(
        engine_version=ENGINE_VERSION,
        size_mm=tuple(_r(v) for v in size),
        bbox=tuple(_r(v) for v in (x0, x1, y0, y1, z0, z1)),
        instances=dinst,
        harness=harness,
        hardware=hw,
        cartridge=derived_cart,
        warnings=warnings,
    )
    # MAJ-5 rule (pipeline adapter_policy): ship the adapter when the uncapped draw is over 80% of the +5V limit
    if not sp.electronics.power.adapter_in_box and not errors:
        try:
            from .electrical import power_budget

            pb = power_budget(sp, lib)
            if pb["total_ma_max"] > 0.8 * pb["limit_ma"]:
                psu = next(iter(lib.by_role("adapter")), None)
                if psu:
                    sp.electronics.power.adapter_in_box = True
                    sp.derived.instances.append(DInstance(ref="PSU1", module=psu["id"], role="adapter", name=psu.get("name", ""),
                                                          source="implied", reason="adapter_in_box (uncapped draw over 80% of the +5V limit)"))
                    added.append(f"electronics.power.adapter_in_box: true (uncapped draw {pb['total_ma_max']:.0f} mA is over 80% of "
                                 f"the {pb['limit_ma']} mA +5V limit; REVIEW-1 MAJ-5)")
        except Exception as exc:  # never crash normalize
            warnings.append(f"adapter rule not evaluated: {exc}")
    # REVIEW-1 MIN-2 / MAJ-29 (integration): the +5V drop budget must hold at the capped peak. A 12-pixel night-light ring
    # at the default LED cap pulls the rail under the SD1 minimum, so lower the global LED cap (firmware led_max_percent,
    # emitted as GS_CFG_LED_MAX_PERCENT in config.h) in 5 % steps, never below 10 %, until the budget holds.
    if not errors:
        try:
            cap_note = _cap_leds_for_rail(sp, lib)
            if cap_note:
                added.append(cap_note)
        except Exception as exc:  # never crash normalize
            warnings.append(f"LED cap for the +5V drop budget not evaluated: {exc}")
    report = {"ok": not errors, "errors": errors, "warnings": warnings, "added": added, "nudges": nudges,
              "size_mm": [_r(v) for v in size],
              "envelope": {"bbox": [_r(v) for v in (x0, x1, y0, y1, z0, z1)], "source": env["source"],
                           "fits_upright": fit["upright"], "fits_any_orientation": fit["any"],
                           "ears": [{k: (list(map(_r, v)) if k == "bounds" else v) for k, v in e.items()} for e in env["ears"]]},
              "instances": [i["ref"] for i in inst],
              "slots": {i["ref"]: i.get("slot") for i in inst if i.get("slot")}}
    if raise_on_error and errors:
        raise SpecError(errors)
    return sp, report


def _cap_leds_for_rail(sp: Spec, lib, floor_pct: int = 10, step: int = 5) -> Optional[str]:
    """Lower behaviour.settings.led_max_percent until the pipeline's +5V drop budget (gstudio.order.power.drop_budget,
    the same function electrical.py stores in electrical.json) holds at the capped peak. Returns a note, or None when
    nothing changed (budget already holds, no LED, or no cap value low enough)."""
    from .electrical import effective_caps, power_budget
    from .order.power import drop_budget

    insts = list(sp.derived.instances or [])
    if not any(i.role == "led" for i in insts):
        return None
    adapter_row = next((lib.get(i.module) for i in insts if i.role == "adapter" and lib.has(i.module)), None)
    rows = [{"ref": i.ref, "module": i.module} for i in insts]

    def budget() -> dict:
        return drop_budget(power_budget(sp, lib), lib.backplane, lambda mid: lib.get(mid) if lib.has(mid) else None, rows,
                           adapter_row)

    db = budget()
    if db.get("ok") is not False:
        return None
    start = effective_caps(sp).get("led_max_percent")
    if start is None:
        return None
    st = dict(sp.behaviour.settings or {})
    old = int(start)
    pct = old
    while pct > floor_pct:
        pct = max(floor_pct, pct - step)
        sp.behaviour.settings = {**st, "led_max_percent": pct}
        db = budget()
        if db.get("ok"):
            return (f"behaviour.settings.led_max_percent: {old} -> {pct} (the +5V drop budget failed at {old} %: "
                    f"{db.get('equation')}; REVIEW-1 MIN-2 / MAJ-29)")
    sp.behaviour.settings = st  # no cap low enough: keep the setting; the rail-voltage check reports the failure
    return None


def _rear_kit_crosses_seam(geom: "ShellGeometry", sp: Spec, f: Feature, m: dict, W: float, floor: float) -> bool:
    """A rear-face kit (wall + module depth + 3 mm for its pad and posts, a design estimate) reaching past the rear-cover seam."""
    try:
        t = geom.surface_depth("rear", float(f.at[0]), float(f.at[1]))
        if t is None:
            return False
        outer_y = 400.0 - t
        depth = float(m.get("depth_mm") or (m.get("dims_mm") or {}).get("z") or 10.0)
        return outer_y - (geom.wall + depth + 3.0) < seam_y(geom, W, floor)
    except Exception:  # never crash normalize on this guard
        return False


def _module_end(m: dict) -> str:
    if (m.get("prewire") or {}).get("lead"):
        return "factory_prewired"  # the factory solders the PH-terminated lead to the module before assembly
    mc = m.get("module_connector") or {}
    t, loc = str(mc.get("type", "")), str(mc.get("location", "")).lower()
    if t == "jst_ph_2.0":
        return "jst_ph"
    if t == "jst_xh_2.5":
        return "jst_xh"
    if t == "header_2.54":
        return "dupont_2.54"
    if t == "spade_2.8" or "spade" in loc or "spade" in str((m.get("backplane_harness") or {})).lower():
        return "spade_2.8"
    return "solder"


# =============================================================================================== planned hardware
# Mirrors the CAD stage's fastener rules (gstudio.cad: internals.py cartridge, enclosure.py cover/plate, features.py kits):
#   cartridge 4 x (insert + M2.5 screw); cover/base plate 4 x (insert + M2.5 screw);
#   per feature: speaker ring 3, snap-in button 0, touch pad 0 (adhesive), every other board-clamped module 2 posts.
# All screws are M2.5 into M2.5 heat-set inserts. The S1 support posts take 2 x M2 screws into printed pilot holes
# (CAD models the posts, not those screws yet). These are PLANNED counts: components.json qty from CAD is authoritative.
# REVIEW-1 MAJ-12: one purchased row per screw length (library SCREW_<thread>X<len>_SHCS_A2, each with its own SKU) and the
# insert that is actually bought (INSERT_HEATSET_M2_5X4_MEC8005, pack of 10). `id` is the CAD fastener group name, so the
# parts list joins PURCHASE_<group> by name; `module` is the library row that is bought.
MODULE_POSTS = {"speaker": 3, "touch": 0, "servo": 0, "servo_continuous": 0}  # servo: its own tab screws into pilot holes (B hand-off 1)
# planned lengths: cartridge from backplane-v1 (boss.screw_length_mm); cover and module clamp lengths are what the CAD's
# pick_screw rule chose on the benchmark shells (12 and 8); the CAD picks per design and its components.json size wins
PLANNED_SCREW_LEN = {"CoverScrews": 12, "PlateScrews": 12, "ModuleScrews": 8, "SupportPostScrews": 6}
PURCHASED_INSERT = "INSERT_HEATSET_M2_5X4_MEC8005"
WALL_SCREW_SET = "WALL_SCREW_PLUG_SET_4X25"  # pack `mount` row (package D)
PURCHASED_FEET = "FEET_RUBBER_8X2_5_SUNROM_4436"  # REVIEW-1 MIN-25 (integration): Indian retail feet (Sunrom 4436) replace the IndiaMART-only 3M row


def module_posts(m: dict) -> int:
    role = m.get("role")
    if role in MODULE_POSTS:
        return MODULE_POSTS[role]
    if role == "button" and (m.get("mount") or {}).get("type") == "panel_snap":
        return 0
    return 2


def screw_module(lib, thread: str, length: float) -> Optional[str]:
    """Library screw row for a thread and length under the head (e.g. 'M2.5', 8 -> SCREW_M2_5X8_SHCS_A2), else None."""
    for m in lib.by_role("screws"):
        if str(m.get("thread") or "") == thread and m.get("length_mm") is not None and float(m["length_mm"]) == float(length):
            return m["id"]
    return None


def planned_hardware(sp: Spec, lib, feat_mods: dict, inst: list, harness: list, has_speaker: bool) -> list[DHardware]:
    hw: list[DHardware] = []

    def first(role, pred=None):
        xs = [m for m in lib.by_role(role) if pred is None or pred(m)]
        return xs[0] if xs else None

    set_row = first("screws", lambda m: m["id"] == "SCREW_SET_M2_M2_5_SHCS_A2") or first("screws")
    ins = (lib.get(PURCHASED_INSERT) if lib.has(PURCHASED_INSERT) else None) or first("inserts", lambda m: "M2_5" in m["id"]) or first("inserts")
    n_cart, n_cover = 4, 4
    n_mod = sum(module_posts(m) for m in feat_mods.values())
    cover = "CoverScrews" if sp.body.split == "rear_cover" else "PlateScrews"
    cart_len = float(((lib.backplane.get("cartridge") or {}).get("boss") or {}).get("screw_length_mm") or 5)

    def screw(group, thread, length, qty, use):
        mid = screw_module(lib, thread, length)
        m = lib.get(mid) if mid else set_row
        if m is None:
            return
        hw.append(DHardware(id=group, name=f"{thread} x {length:g} mm socket head screw", qty=qty, size=f"{thread}x{length:g}",
                            group=group, use=use, module=m["id"], catalog_row=m.get("catalog_row"), length_mm=float(length)))

    if set_row:
        screw("CartridgeScrews", "M2.5", cart_len, n_cart, "backplane to the 4 cartridge bosses")
        screw(cover, "M2.5", PLANNED_SCREW_LEN[cover], n_cover, "rear cover" if cover == "CoverScrews" else "base plate")
        if n_mod:
            screw("ModuleScrews", "M2.5", PLANNED_SCREW_LEN["ModuleScrews"], n_mod, "module clamp straps and the speaker ring")
        if any(i.get("slot") == "S1" for i in inst):
            screw("SupportPostScrews", "M2", PLANNED_SCREW_LEN["SupportPostScrews"], 2,
                  "microSD module corners into the 2 printed support posts (1.8 mm pilot holes)")
    if ins:
        hw.append(DHardware(id="HeatSetInserts", name="M2.5 heat-set insert", qty=n_cart + n_cover + n_mod, size="M2.5", group="HeatSetInserts",
                            use=f"{n_cart} cartridge + {n_cover} {'cover' if cover == 'CoverScrews' else 'plate'} + {n_mod} module bosses",
                            module=ins["id"], catalog_row=ins.get("catalog_row"), length_mm=ins.get("length_mm")))
    feet = (lib.get(PURCHASED_FEET) if lib.has(PURCHASED_FEET) else None) or first("feet")
    if feet and sp.body.feet:
        hw.append(DHardware(id="Feet", name="adhesive bumper foot", qty=sp.body.feet, group="Feet", use="underside",
                            module=feet["id"], catalog_row=feet.get("catalog_row")))
    mt = getattr(sp, "mount", None)  # pack `mount` (hand-off d-to-e 2): the wall screws and plugs are bought, not printed
    if mt is not None and getattr(mt, "kind", "desk") == "wall" and lib.has(WALL_SCREW_SET):
        w = lib.get(WALL_SCREW_SET)
        n = int(getattr(mt, "screws", 2) or 2)
        hw.append(DHardware(id="WallScrews", name="wall screw + wall plug set", qty=n, size="about 4 x 25 mm screw + plug",
                            group="WallScrews", use=f"one screw in a wall plug per keyhole ({n} keyholes in the rear cover)",
                            module=w["id"], catalog_row=w.get("catalog_row")))
    ties = first("ties")
    if ties and harness:
        hw.append(DHardware(id="CableTies", name="cable tie 100 x 2.5 mm", qty=max(3, len(harness) + 1) + 1, group="CableTies",
                            use="harness retention at printed anchors + 1 for the USB-C cord tie bridge (REVIEW-1 MIN-3)",
                            module=ties["id"], catalog_row=ties.get("catalog_row")))
    if has_speaker:
        g = first("gasket")
        if g:
            hw.append(DHardware(id="SpeakerGasket", name="speaker foam gasket strip", qty=1, group="SpeakerGasket",
                                use="seal the speaker to the baffle", module=g["id"], catalog_row=g.get("catalog_row")))
    lab = first("label")
    if lab:
        hw.append(DHardware(id="Label", name="device ID label + packing kit", qty=1, group="Label",
                            use="serial/QR label and packing", module=lab["id"], catalog_row=lab.get("catalog_row")))
    return hw


# =============================================================================================== cartridge search
def seam_y(geom: ShellGeometry, W: float, floor: float) -> float:
    """Rear-cover seam y, same rule as gstudio.cad.enclosure.seam_y: silhouette = depth/2 - rear fillet - 1.5 (rear fillet
    = min(edge_round, 6, depth/2 - 2)); other families = the largest y (2 mm steps) whose cavity section contains the
    cartridge front envelope [-W/2-3, W/2+3] x [floor+0.5, floor+26]."""
    b = geom.body
    if isinstance(b, SilhouetteBody):
        return b.depth / 2 - min(b.edge_round, 6.0, b.depth / 2 - 2.0) - 1.5
    _, _, y0, y1, _, _ = geom.bbox
    y = y1 - geom.wall - 2.0
    xs = _lin(-W / 2 - 3.0, W / 2 + 3.0, 6.0)
    zs = _lin(floor + 0.5, floor + 26.0, 6.0)
    while y > y0 + 10.0:
        ok = all(geom.outer_sdf(x, y, z) <= -geom.wall for x in xs for z in (zs[0], zs[-1])) and \
            all(geom.outer_sdf(x, y, z) <= -geom.wall for z in zs for x in (xs[0], xs[-1]))
        if ok:
            return y
        y -= 2.0
    return (y0 + y1) / 2


def _place_cartridge(geom: ShellGeometry, W, plan, floor, kz, rear_gap, kb=0.0, bosses=(), boss_top=6.4, y_cap=None,
                     usb_bx=None, base_plate=False, step=1.0, trace: Optional[list] = None):
    """Board y offset (board y=0 -> world y_off), mirroring gstudio.cad.enclosure.place_cartridge so normalize and CAD agree:
    start with the USB-C face `rear_gap` inside the inner rear wall (ray at the receptacle x, board top + 1.6), cap by
    the rear-cover seam rule (`y_cap`), then step forward 1 mm until the keep-out (CLEAR mm on every side, rear minus
    0.3; z = floor+1, floor+kz/2, floor+kz) lies inside the cavity, and for a base plate until its plan also lies
    inside the bottom opening (cavity at floor+0.8 and outer bottom shrunk by the wall)."""
    kx0, kx1, ky0, ky1 = plan
    wx0, wx1 = kx0 - W / 2, kx1 - W / 2
    zt = floor + 8.0
    ux = (usb_bx if usb_bx is not None else W / 2) - W / 2
    # inner rear wall behind the receptacle
    y = 0.0
    y_rear = None
    for _ in range(600):
        if geom.outer_sdf(ux, y, zt + 1.6) > -geom.wall:
            y_rear = y
            break
        y += 0.5
    if y_rear is None:
        return None, None, "could not find the inner rear wall behind the USB-C position"
    # refine to 0.05 mm
    lo_y, hi_y = y_rear - 0.5, y_rear
    for _ in range(4):
        mid = (lo_y + hi_y) / 2
        if geom.outer_sdf(ux, mid, zt + 1.6) > -geom.wall:
            hi_y = mid
        else:
            lo_y = mid
    y0 = hi_y - rear_gap - ky1
    if y_cap is not None and y0 > y_cap:
        y0 = y_cap

    def fits(yo, z_bottom=1.0):
        xs = _lin(wx0 - CLEAR, wx1 + CLEAR, (wx1 - wx0 + 2 * CLEAR) / 8)
        ys = _lin(yo + ky0 - CLEAR, yo + ky1 + CLEAR - 0.3, (ky1 - ky0 + 2 * CLEAR - 0.3) / 8)
        # checked against the cavity with the silhouette's edge rounds (fit_sdf), like gstudio.cad.enclosure
        zs = (floor + z_bottom, floor + kz * 0.5, floor + kz)
        if trace is not None:  # pack `freeform` (csg bodies only): every failing sample of this try, for the error text
            bad = [(x, yy, z) for z in zs for x in xs for yy in ys
                   if (x in (xs[0], xs[-1]) or yy in (ys[0], ys[-1])) and geom.fit_sdf(x, yy, z) > -geom.wall]
            trace.append({"y0": yo, "fails": bad, "n": 3 * (2 * len(xs) + 2 * len(ys) - 4),
                          "box": (xs[0], xs[-1], ys[0], ys[-1], zs[0], zs[-1])})
        for z in zs:
            for x in xs:
                for yy in (ys[0], ys[-1]):
                    if geom.fit_sdf(x, yy, z) > -geom.wall:
                        return False, f"wall reached at x={x:.0f}, y={yy:.0f}, z={z:.0f}"
            for yy in ys:
                for x in (xs[0], xs[-1]):
                    if geom.fit_sdf(x, yy, z) > -geom.wall:
                        return False, f"wall reached at x={x:.0f}, y={yy:.0f}, z={z:.0f}"
        if base_plate:
            pxs = _lin(wx0, wx1, (wx1 - wx0) / 8)
            pys = _lin(yo + ky0, yo + ky1 - 0.3, (ky1 - ky0 - 0.3) / 8)
            pts = [(x, yy) for x in pxs for yy in (pys[0], pys[-1])] + [(x, yy) for yy in pys for x in (pxs[0], pxs[-1])]
            w = geom.wall
            ring = [(w * math.cos(k * math.pi / 4), w * math.sin(k * math.pi / 4)) for k in range(8)]
            for x, yy in pts:
                if geom.outer_sdf(x, yy, floor + 0.8) > -geom.wall:
                    return False, f"plan leaves the cavity above the base plate at x={x:.0f}, y={yy:.0f}"
                # outer bottom section at z=0.05 shrunk by the wall (2D buffer, as the CAD's base_plate_opening)
                if any(geom.outer_sdf(x + dx, yy + dy, 0.05) > 0.0 for dx, dy in [(0.0, 0.0)] + ring):
                    return False, f"plan leaves the base-plate opening at x={x:.0f}, y={yy:.0f}"
        return True, ""

    first, opening = "", False
    y_start = y0
    # pass 1 from 1 mm above the floor (every design that fitted before keeps its place); when no position fits, pass 2
    # from the keep-out's own bottom (backplane-v1 keepout_bottom_above_floor: below it only the 4 printed bosses reach
    # the floor, and they blend into the wall). Same two passes as gstudio.cad.enclosure.place_cartridge.
    for z_bottom in ((1.0, float(kb)) if kb and kb > 1.0 else (1.0,)):
        y0 = y_start
        for _ in range(80):
            ok, why = fits(y0, z_bottom)
            if ok:
                box = (wx0, wx1, y0 + ky0, y0 + ky1, floor, floor + kz)
                return y0, box, ""
            first = first or why  # the reason at the rear-most position is the meaningful one
            opening = opening or "base-plate opening" in why
            y0 -= step
    return None, None, first + ("; the plan also never fits the base-plate opening" if opening and "base-plate opening" not in first else "")


def _csg_cartridge_text(trace: list, geom: "ShellGeometry", why: str, fix: str) -> tuple[str, str]:
    """pack `freeform`: the cartridge try that came closest (fewest samples in the wall), where it fails and the block
    the csg body must keep free (keep-out + clearance + wall, spec coordinates). The rear-most try always fails on the
    rear wall by construction, so its reason never told the agent what to change (agent request 4)."""
    tries = [t for t in trace if t["fails"]]
    if not tries:
        return why, fix
    best = min(tries, key=lambda t: (len(t["fails"]), -t["y0"]))
    x0, x1, y0, y1, z0, z1 = best["box"]
    w = geom.wall
    sides = []
    for nm, test in (("top", lambda p: abs(p[2] - z1) < 1e-6), ("front", lambda p: abs(p[1] - y0) < 1e-6),
                     ("rear", lambda p: abs(p[1] - y1) < 1e-6), ("right (-x)", lambda p: abs(p[0] - x0) < 1e-6),
                     ("left (+x)", lambda p: abs(p[0] - x1) < 1e-6), ("bottom", lambda p: abs(p[2] - z0) < 1e-6)):
        pts = [p for p in best["fails"] if test(p)]
        if pts:
            sides.append(f"{nm} at x {min(p[0] for p in pts):.0f}..{max(p[0] for p in pts):.0f}, y {min(p[1] for p in pts):.0f}.."
                         f"{max(p[1] for p in pts):.0f}, z {min(p[2] for p in pts):.0f}..{max(p[2] for p in pts):.0f}")
    why = (f"at its best place ({len(best['fails'])} of {best['n']} checked points in the wall) the keep-out box x {x0:.0f}..{x1:.0f}, "
           f"y {y0:.0f}..{y1:.0f}, z {z0:.0f}..{z1:.0f} still meets the wall on its " + "; ".join(sides[:3]))
    fix = (f"keep the csg body a full block with no slope, step, cut or groove inside x {x0 - w:.0f}..{x1 + w:.0f}, "
           f"y {y0 - w:.0f}..{y1 + w:.0f}, z 0..{z1 + w:.0f} (the cartridge keep-out plus clearance and the {w:g} mm wall): move "
           "or raise what cuts into that block (a slope's low end, a lower step, a tray or groove), or make the lower body bigger; "
           + fix)
    return why, fix


def _silhouette_cartridge_fix(sp: Spec, W, plan, floor, kz, rear_gap, kb, bosses, boss_top, holes, usb_bx) -> str:
    """The smallest listed change that lets the core cartridge fit a silhouette: a smaller edge_round or a deeper body
    (a few candidates only; normalize stays fast). '' when none of them helps."""
    b = sp.body
    holes_y = max(h["at"][1] for h in holes)

    def fits_with(**upd) -> bool:
        g2 = ShellGeometry(sp.model_copy(update={"body": b.model_copy(update=upd)}))
        cap = seam_y(g2, W, floor) - SEAM_BOSS_MARGIN - holes_y if b.split == "rear_cover" else None
        y2, _, _ = _place_cartridge(g2, W, plan, floor, kz, rear_gap, kb, bosses, boss_top, cap, usb_bx=usb_bx)
        return y2 is not None

    out = []
    er = float(b.edge_round)
    for r in sorted({round(er * k) for k in (0.75, 0.6, 0.45)} | {6, 4}, reverse=True):
        if 2 <= r < er and fits_with(edge_round=float(r)):
            out.append(f"set body.edge_round to {r} or less")
            break
    for dd in (6, 10, 16, 24):
        if fits_with(depth=float(b.depth) + dd):
            out.append(f"make body.depth at least {b.depth + dd:g} mm")
            break
    return " or ".join(out)


def boss_warnings(geom: ShellGeometry, bosses, y_off, floor, boss_top) -> list[str]:
    out = []
    for bx, by, br in bosses:
        for z in (floor + 0.5, floor + boss_top):
            if any(not geom.inside(bx + br * math.cos(k * math.pi / 4), y_off + by + br * math.sin(k * math.pi / 4), z, margin=0.0)
                   for k in range(8)):
                out.append(f"cartridge boss at x={bx:.0f}, y={y_off + by:.0f} is not fully on a flat floor; CAD must blend it into the wall")
                break
    return out


def _lin(a, b, step):
    n = max(2, int(math.ceil((b - a) / step)) + 1)
    return [a + (b - a) * i / (n - 1) for i in range(n)]


# =============================================================================================== feature placement
def _place_feature(geom: ShellGeometry, f: Feature, m: dict, k: int, errors, warnings):
    face = f.face
    u, v = f.at
    fw, fh = (m.get("face_mm") or [20.0, 20.0])
    fw, fh = float(fw or 20), float(fh or 20)
    if f.rotate in (90, 270):
        fw, fh = fh, fw
    depth = float(m.get("depth_mm") or 5.0) + float(m.get("harness_clearance_mm") or 0.0)
    ap = (m.get("aperture") or {}).get("size_mm") or {}
    aw, ah = float(ap.get("w") or fw), float(ap.get("h") or fh)
    if f.rotate in (90, 270):
        aw, ah = ah, aw
    if f.grille is not None:
        aw, ah = max(aw, f.grille.w), max(ah, f.grille.h)
    t0 = geom.surface_depth(face, u, v)
    if t0 is None:
        _err(errors, f"features[{k}].at", f"{f.id} at {list(f.at)} misses the body on the {face} face",
             "move it inside the body outline as seen from that face", "feature_miss")
        return None
    # deepest surface point under the module footprint -> flat mounting plane behind the wall
    ts = [t0]
    for du in (-fw / 2, fw / 2):
        for dv in (-fh / 2, fh / 2):
            t = geom.surface_depth(face, u + du, v + dv)
            if t is None:
                _err(errors, f"features[{k}].at", f"{f.id}: the {fw:.0f} x {fh:.0f} mm module does not fit on the {face} face at {list(f.at)}",
                     "move it toward the middle of that face or enlarge the body", "feature_edge")
                return None
            ts.append(t)
    # the opening must avoid the rounded perimeter: aperture corners must hit the face too
    for du in (-aw / 2, aw / 2):
        for dv in (-ah / 2, ah / 2):
            if geom.surface_depth(face, u + du, v + dv) is None:
                _err(errors, f"features[{k}].at", f"{f.id}: the {aw:.0f} x {ah:.0f} mm opening runs off the {face} face",
                     "move it inward", "feature_edge")
                return None
    plane = max(ts) + geom.wall
    tunnel = max(ts) - min(ts)
    tilted = None
    if tunnel > 8.0 and geom.family == "csg":  # pack `freeform`: a flat face at a slope (wedge consoles) is no curve
        tmm, tmp_, tpm, tpp = ts[1], ts[2], ts[3], ts[4]  # (-u,-v), (-u,+v), (+u,-v), (+u,+v)
        if abs(t0 - (tmm + tmp_ + tpm + tpp) / 4) < 0.6 and abs((tpp - tpm) - (tmp_ - tmm)) < 0.6:
            du_, dv_ = ((tpm + tpp) - (tmm + tmp_)) / 2 / max(fw, 1e-6), ((tmp_ + tpp) - (tmm + tpm)) / 2 / max(fh, 1e-6)
            tilted = math.degrees(math.atan(math.hypot(du_, dv_)))
    if tilted is not None:
        warnings.append(f"features[{k}] {f.id}: the {face} face is a flat slope here (about {tilted:.0f} deg); the CAD mounts the "
                        "module square to the slope")
    elif tunnel > 8.0:
        warnings.append(f"features[{k}] {f.id}: the face curves {tunnel:.0f} mm across the module; CAD adds a mounting boss and a deep window")
    # available depth along the centre ray
    avail = 0.0
    s = plane + 0.5  # first probe just inside the inner wall surface
    while avail < depth + 1.0:
        p = geom.point(face, u, v, s + avail)
        if not geom.inside(*p, margin=0.0):
            break
        avail += 1.0
    avail += 0.5
    back_ok = avail >= depth
    bad_corners = []
    if back_ok:
        for du in (-fw / 2, fw / 2):
            for dv in (-fh / 2, fh / 2):
                p = geom.point(face, u + du * 0.9, v + dv * 0.9, plane + max(depth, 0.6))
                if not geom.inside(*p, margin=0.0):
                    back_ok = False
                    bad_corners.append(("lower" if dv < 0 else "upper") + "-" + ("left" if du < 0 else "right"))
    if not back_ok:
        fix = "move it where the body is deeper (toward the centre) or make the body deeper"
        pcs = (getattr(geom, "csg_info", None) or {}).get("solid_pieces") if geom.family == "csg" else None
        if pcs:  # pack `freeform`: say why a csg piece has no room inside
            fix += (f"; note {', '.join(p['label'] for p in pcs[:2])} print solid because their hollow does not join the main "
                    f"hollow: overlap such a piece with the main body by at least {2 * geom.wall + 1:.0f} mm")
        msg = f"{f.id}: {m['id']} needs {depth:.0f} mm behind the wall (module + cable); about {avail:.0f} mm is available at {list(f.at)}"
        if geom.family == "csg" and bad_corners:  # pack `freeform` (agent request 2): the centre has room, corners do not
            msg = (f"{f.id}: {m['id']} needs {depth:.0f} mm behind the wall (module + cable): the centre has about {avail:.0f} mm, but "
                   f"its {', '.join(bad_corners)} corner(s) (in {face}-face coordinates) reach the wall or leave the main hollow "
                   "at that depth")
            fix = "move it toward the middle of the body, away from those corners, or make the body deeper there; " + fix
        _err(errors, f"features[{k}].at", msg, fix, "feature_depth")
    a = geom.point(face, u - fw / 2, v - fh / 2, plane)
    b = geom.point(face, u + fw / 2, v + fh / 2, plane + depth)
    box = (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), min(a[2], b[2]), max(a[2], b[2]))
    rect = (u - aw / 2, u + aw / 2, v - ah / 2, v + ah / 2)
    c = geom.point(face, u, v, plane + depth / 2)
    return box, rect, c


# =============================================================================================== kit footprints (MAJ-8)
def _footprint_module():
    """gstudio.cad.footprint (pure Python, cad-owned) or None when it cannot be imported."""
    try:
        from .cad import footprint as FP  # noqa: N812

        return FP
    except Exception:  # pragma: no cover - cad package missing or broken
        return None


def kit_conflicts(sp: Spec, lib, features: list, fboxes: dict) -> list[dict]:
    """Same-face kit-vs-kit conflicts with a nudge: cad's footprint.layout_conflicts (retainer, bosses, clip strap, speaker
    clamp ring and tabs at the rotation CAD will choose); fallback = the bare module envelopes plus 6 mm around speakers."""
    FP = _footprint_module()
    if FP is not None:
        try:
            out = FP.layout_conflicts(features, margin=KIT_MARGIN, skin=sp.body.wall, lib_get=lib.get)
            for c in out:
                c["source"] = "gstudio.cad.footprint"
            return out
        except Exception as exc:  # the footprint module must never crash normalize
            sp_warn = f"kit footprint check fell back to envelopes: {exc}"
            if sp.derived is not None:
                sp.derived.warnings.append(sp_warn)
    out = []
    grown = {}
    for f in features:
        box = fboxes.get(f.id)
        if box is None:
            continue
        g = SPEAKER_KIT_FALLBACK if (lib.get(f.module).get("role") == "speaker") else 0.0
        grown[f.id] = tuple(box[i] - g if i % 2 == 0 else box[i] + g for i in range(6))
    ids = list(grown)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            fa, fb = sp.feature(ids[i]), sp.feature(ids[j])
            if fa.face != fb.face or not _overlap3(grown[ids[i]], grown[ids[j]], 0.0):
                continue
            a, b = grown[ids[i]], grown[ids[j]]
            up = fb.at[1] >= fa.at[1]
            dv = (a[5] - b[4]) if up else (b[5] - a[4])
            out.append({"a": fa.id, "b": fb.id, "a_part": None, "a_what": "envelope", "b_part": None, "b_what": "envelope",
                        "gap_mm": -round(dv, 2), "overlap_v_mm": round(dv, 2), "source": "fallback envelope",
                        "nudge": f"{fb.id} touches the {fa.id} envelope: move {fb.id} {'up' if up else 'down'} {math.ceil(dv + 1):.0f} mm or more"})
    return out


def kit_shell_conflicts(sp: Spec, lib, geom: "ShellGeometry", features: list, tol: float = 1.0) -> list[dict]:
    """Separate kit parts (module, clamp ring, tabs, screws, strap; not the shell's own bosses/pads/cup walls) whose box
    reaches into the shell wall or floor where the face curves (REVIEW-1 MAJ-8: the 20-layout CAD sweep found speaker
    rings cutting the rounded bottom edge). Probes each box's centre and edge midpoints at both depths: the probe must lie
    in the cavity (outer SDF <= -(wall - tol)) and above the inner floor. Needs gstudio.cad.footprint; [] without it."""
    FP = _footprint_module()
    if FP is None:
        return []
    out = []
    for f in features:
        flat = isinstance(sp.body, SilhouetteBody) and f.face in ("front", "rear")
        if not flat and sp.body.family == "csg":  # pack `freeform`: a flat face square to the face axis seats the kit
            flat = _flat_square(geom, f)  # exactly like a silhouette front (CAD frame = face axis), so the probe holds
        try:
            boxes = FP.kit_footprint(f, None, others=features, skin=sp.body.wall, lib_get=lib.get)
        except Exception:
            continue
        t = geom.surface_depth(f.face, f.at[0], f.at[1])
        if t is None:
            continue
        o, d = geom._ray(f.face, f.at[0], f.at[1], 400.0)
        s_surf = (o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t)[{"front": 1, "rear": 1, "left": 0, "right": 0, "top": 2}[f.face]]
        worst = None
        for b in boxes:
            if b.get("part") == "PRINT_Shell" or b.get("kind") in ("pad", "boss", "clearance"):
                continue
            if float(b["depth"][0]) < sp.body.wall + 0.1:
                continue  # sits in the opening by design (window glass, bezel, gasket): only parts behind the wall are probed
            x0, x1, y0, y1, z0, z1 = FP.to_world(b, s_surf)
            xm, ym, zm = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
            probes = [(xm, ym, zm), (x0, ym, zm), (x1, ym, zm), (xm, y0, zm), (xm, y1, zm), (xm, ym, z0), (xm, ym, z1)]
            for px, py, pz in probes:
                # the wall probe is exact only where the kit's depth is measured from a flat face (silhouette front/rear);
                # on curved faces CAD seats the kit on a flattened mounting plane this probe does not model
                depth_in = (geom.outer_sdf(px, py, pz) + geom.wall) if flat else -1.0  # > 0: in the wall or outside
                below = geom.floor_z - pz  # > 0: under the inner floor
                bad = max(depth_in - tol, below - tol)
                if bad > 0 and (worst is None or bad > worst[0]):
                    worst = (bad, b, (px, py, pz))
        if worst:
            bad, b, (px, py, pz) = worst
            fix = (f"move {f.id} toward the middle of the {f.face} face, away from the rounded edge "
                   f"(by {math.ceil(bad + 2):.0f} mm or more)")
            if sp.body.family == "csg":  # the wall it meets may be a cut's pocket, a step or the outside
                fix = (f"move {f.id} (or the cut, step or slope whose wall is there) by {math.ceil(bad + 2):.0f} mm or more so "
                       "the kit behind the face clears every inner wall")
            out.append({"feature": f.id, "what": b.get("what"), "mm": round(bad, 1),
                        "msg": f"{f.id}: its kit ({b.get('what')}) reaches {bad:.1f} mm into the shell wall or floor near "
                               f"x {px:.0f}, y {py:.0f}, z {pz:.0f}",
                        "fix": fix})
    return out


def _flat_square(geom: "ShellGeometry", f) -> bool:
    """csg bodies: the face under the feature is a plane square to the face axis (same ray depth over +-12 mm)."""
    u, v = float(f.at[0]), float(f.at[1])
    ts = [geom.surface_depth(f.face, u + du, v + dv) for du, dv in ((0, 0), (-12, 0), (12, 0), (0, -12), (0, 12))]
    return all(t is not None for t in ts) and max(ts) - min(ts) < 0.3


SEAM_LIP_REACH = 3.0 + 0.5  # the rear cover's locating lip (and its screw tubes) reach 3 mm in front of the seam, + 0.5 gap


def kit_seam_conflicts(sp: Spec, lib, geom: "ShellGeometry", features: list, y_seam: float) -> list[dict]:
    """Top, left and right kits on a rear_cover design whose parts or shell pads reach the rear cover: the cover's lip
    sits SEAM_LIP_REACH mm in front of the seam all round the inner wall, so anything of a kit further back collides
    with the cover or is cut by the seam. Live 2026-11-20 (focus-lamp p_r4yikwxy497q): a top speaker at y 20 whose clamp
    ring reached y 48.8 with the seam at 42.6 (PRINT_RearCover / PRINT_SpeakerRing and shell / cover collisions).
    Rear-face kits are refused separately (rear_cover_face). Needs gstudio.cad.footprint; [] without it."""
    FP = _footprint_module()
    if FP is None:
        return []
    limit = y_seam - SEAM_LIP_REACH
    out = []
    for f in features:
        if f.face not in ("top", "left", "right"):
            continue
        try:
            boxes = FP.kit_footprint(f, None, others=features, skin=sp.body.wall, lib_get=lib.get)
        except Exception:
            continue
        t = geom.surface_depth(f.face, f.at[0], f.at[1])
        if t is None:
            continue
        o, d = geom._ray(f.face, f.at[0], f.at[1], 400.0)
        s_surf = (o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t)[{"left": 0, "right": 0, "top": 2}[f.face]]
        ys = [FP.to_world(b, s_surf)[3] for b in boxes if b.get("kind") != "clearance"]
        if not ys or max(ys) <= limit:
            continue
        over = max(ys) - limit
        axis = 1 if f.face == "top" else 0  # the face coordinate that runs along y (top: at = [x, y]; sides: at = [y, z])
        out.append({"feature": f.id, "mm": round(over, 1),
                    "msg": f"{f.id}: its kit reaches y {max(ys):.0f}, into the removable rear cover (seam at y {y_seam:.0f}; the "
                           f"cover's lip reaches 3 mm in front of the seam)",
                    "fix": f"move {f.id} toward the front: at[{axis}] <= {f.at[axis] - math.ceil(over + 1):.0f} on the {f.face} face, "
                           "or use body.split base_plate"})
    return out


def _place_night_light(sp: Spec, k: int, geom: "ShellGeometry", m: dict, lib, fboxes: dict, frects: dict, cart_box, errors: list,
                       added: list):
    """Keep the upgraded night-light ring where the spec put it if it fits, else move it to the nearest spot on the same
    face whose kit clears every other kit (cad's footprint.find_clear_at with normalize's surface/depth/opening tests).
    Returns True (placed / moved) or False (no place: an error was added)."""
    f = sp.features[k]

    def fits(at) -> bool:
        f2 = f.model_copy(update={"at": (float(at[0]), float(at[1]))})
        tmp_e, tmp_w = [], []
        res = _place_feature(geom, f2, m, k, tmp_e, tmp_w)
        if res is None or tmp_e:
            return False
        box, rect, _ = res
        if cart_box is not None and _overlap3(box, cart_box, 1.0):
            return False
        for fid, rr in frects.items():
            o = sp.feature(fid)
            if o.face == f2.face and _overlap2(rr, rect, APERTURE_MARGIN):
                return False
            if _overlap3(fboxes[fid], box, 1.0):
                return False
        if f2.face == "front":
            for d in sp.decor:
                if d.type in ("face_plate", "ear"):
                    continue
                w, h = (d.size if hasattr(d, "size") else (len(getattr(d, "text", "")) * d.height * 0.7, d.height))
                rcs = [(d.at[0] - w / 2, d.at[0] + w / 2, d.at[1] - h / 2, d.at[1] + h / 2)]
                if getattr(d, "side", None) == "both":
                    rcs.append((-d.at[0] - w / 2, -d.at[0] + w / 2, d.at[1] - h / 2, d.at[1] + h / 2))
                if any(_overlap2(rc, rect, APERTURE_MARGIN) for rc in rcs):
                    return False
        return True

    others = [o for o in sp.features if o.id != f.id and o.id in fboxes]
    FP = _footprint_module()
    clear_here = fits(f.at) and not [c for c in kit_conflicts_for(sp, lib, f, others, FP) if c]
    if clear_here:
        return True
    found = None
    if FP is not None:
        try:
            found = FP.find_clear_at(f, others, fits=fits, margin=3.0, skin=sp.body.wall, lib_get=lib.get, step=2.0, search_radius=90.0)
        except Exception:
            found = None
    else:  # fallback: ring search on the face, envelopes only
        u0, v0 = f.at
        for r in [2.0 * i for i in range(1, 46)]:
            n = max(8, int(2 * math.pi * r / 2.0))
            ring = sorted(((u0 + r * math.cos(2 * math.pi * i / n), v0 + r * math.sin(2 * math.pi * i / n)) for i in range(n)),
                          key=lambda p: (abs(p[0] - u0), -p[1]))
            hit = next((p for p in ring if fits(p)), None)
            if hit:
                found = {"at": [round(hit[0], 1), round(hit[1], 1)], "moved_mm": round(r, 1)}
                break
    if not found:
        _err(errors, f"features[{k}].at", f"{f.id}: the {NIGHT_LIGHT_RING} night light ({NIGHT_LIGHT_MIN_MM:g} mm+ diffused ring, "
             f"NIGHT_USE) has no clear place on the {f.face} face near {list(f.at)}",
             f"the 50 mm ring and its kit need a clear band about 60 mm tall across the {f.face} face: move the speaker lower "
             "and the eyes higher (examples/bedtime-robot: speaker z 36, ring z 88, eyes z 148) or enlarge the body",
             "night_light_fit")
        return False
    old = list(f.at)
    sp.features[k].at = (float(found["at"][0]), float(found["at"][1]))
    added.append(f"features[{k}].at: {old} -> {list(sp.features[k].at)} ({found.get('moved_mm')} mm) so the {NIGHT_LIGHT_RING} "
                 "night light clears every other kit")
    return True


def kit_conflicts_for(sp: Spec, lib, f, others: list, FP) -> list[dict]:
    """Conflicts between one feature's kit and the others' (same face), via cad's footprint module when present."""
    if FP is None:
        return []
    try:
        return [c for c in FP.layout_conflicts([f] + list(others), margin=3.0, skin=sp.body.wall, lib_get=lib.get)
                if f.id in (c["a"], c["b"])]
    except Exception:
        return []


def nudges_from_cad(spec: Spec, components: dict, digital_checks: dict) -> list[dict]:
    """Nudges for a built version (REVIEW-1 MAJ-8): every failing CAD collision or feature_module_clearance pair between
    two different features, with the move along the face's v axis that would clear the two components' bboxes by 1 mm.
    `components` = cad/components.json, `digital_checks` = cad/digital-checks.json (or checks/digital-checks.json)."""
    checks = digital_checks.get("checks", digital_checks) if isinstance(digital_checks, dict) else digital_checks
    pairs = []
    for c in checks or []:
        if c.get("id") == "collisions" and c.get("status") == "failed":
            pairs += [tuple(x["parts"]) for x in (c.get("data") or {}).get("intersections", [])]
        if c.get("id") == "feature_module_clearance" and c.get("status") == "failed":
            pairs += [tuple(x["parts"]) for x in (c.get("data") or {}).get("pairs", []) if float(x.get("gap_mm", 1)) < 0.5]
    out, seen = [], set()
    for a, b in pairs:
        ca, cb = components.get(a) or {}, components.get(b) or {}
        fa, fb = ca.get("feature_id"), cb.get("feature_id")
        if not fa or not fb or fa == fb or frozenset((fa, fb)) in seen:
            continue
        seen.add(frozenset((fa, fb)))
        sa, sb = spec.feature(fa), spec.feature(fb)
        if sa is None or sb is None:
            continue
        mover, fixed, cm, cf = (sb, sa, cb, ca) if sb.at[1] >= sa.at[1] else (sa, sb, ca, cb)
        vaxis = 5 if mover.face in ("front", "rear", "left", "right") else 3
        lo = 4 if vaxis == 5 else 2
        dv = cf["bbox"][vaxis] - cm["bbox"][lo] + 1.0
        label_m, label_f = (mover.label or mover.id), (fixed.label or fixed.id)
        out.append({"a": fixed.id, "b": mover.id, "a_part": a if cf is ca else b, "b_part": b if cm is cb else a,
                    "move": {"feature": mover.id, "axis": "v", "by_mm": math.ceil(max(dv, 1.0))},
                    "to_at": [mover.at[0], round(mover.at[1] + math.ceil(max(dv, 1.0)), 1)],
                    "nudge": f"{label_m} touches the {label_f} ({cf.get('label') or (a if cf is ca else b)}): move {mover.id} "
                             f"{'up' if vaxis == 5 else 'back'} {math.ceil(max(dv, 1.0)):.0f} mm or more"})
    return out


__all__ = ["normalize", "ShellGeometry", "TEMPLATE_RULES", "kit_conflicts", "kit_shell_conflicts", "nudges_from_cad", "screw_module", "NIGHT_LIGHT_RING"]
