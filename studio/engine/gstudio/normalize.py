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

from .errors import SpecError
from .spec import (
    BlobBody, DCartridge, DHardware, DHarness, DInstance, Derived, Feature, RevolveBody, SilhouetteBody, Spec,
    cross_check, load_spec, outline_points, polygon_area,
)
from .version import ENGINE_VERSION

SIZE_MIN, SIZE_MAX = 60.0, 260.0
BED = (250.0, 210.0, 210.0)
CLEAR = 3.0  # cartridge clearance to the inner wall
APERTURE_MARGIN = 3.0
LENGTH_CLASSES = (100, 150, 200, 300)
FACES = ("front", "rear", "left", "right", "top")


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


class ShellGeometry:
    """Approximate outer shape of the body as a signed distance field (mm, world coordinates)."""

    def __init__(self, spec: Spec):
        self.spec = spec
        b = spec.body
        self.body = b
        self.wall = b.wall
        self.family = b.family
        if isinstance(b, SilhouetteBody):
            self.poly = Poly2D(outline_points(b, 6))
            x0, x1, _, z1 = self.poly.bb
            self.bbox = (x0, x1, -b.depth / 2, b.depth / 2, 0.0, z1)
        elif isinstance(b, RevolveBody):
            pts = list(b.profile)
            n = len(pts)
            self.poly = Poly2D(pts, skip={n - 1})  # the closing edge is the axis, not a surface
            self.poly_pts = pts
            rmax = max(r for r, _ in pts)
            self.bbox = (-rmax, rmax, -rmax * b.squash_y, rmax * b.squash_y, 0.0, max(z for _, z in pts))
        else:
            boxes = [p.aabb() for p in b.primitives]
            self.bbox = (min(q[0] for q in boxes), max(q[1] for q in boxes), min(q[2] for q in boxes),
                         max(q[3] for q in boxes), 0.0, max(q[5] for q in boxes))
        self.floor_z = self.wall

    # ---- distance field
    def outer_sdf(self, x: float, y: float, z: float) -> float:
        b = self.body
        if isinstance(b, SilhouetteBody):
            return max(self.poly.sdf(x, z), abs(y) - b.depth / 2)
        if isinstance(b, RevolveBody):
            rho = math.sqrt(x * x + (y / b.squash_y) ** 2)
            d = self.poly.sdf(rho, z)
            return d * min(1.0, b.squash_y) if d < 0 else d
        d = min(_prim_sdf(p, x, y, z) for p in b.primitives)
        return max(d, -z)

    def inside(self, x: float, y: float, z: float, margin: float = 0.0) -> bool:
        """True when the point lies in the inner cavity with `margin` mm to the inner wall surface."""
        return self.outer_sdf(x, y, z) <= -(self.wall + margin) and z >= self.floor_z + margin - 1e-9

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
    "notifier_clock": {"need": [("display", "a display feature for the time"), ("led", "an LED feature for notifications")],
                       "recommend": [("buzzer", "a buzzer or speaker for alerts")]},
    "ambient_light": {"need": [("led", "an LED (ring or strip) feature")], "need_any": [(("button", "touch"), "a button or touch control")],
                      "recommend": [("light_sensor", "a light sensor for auto-dim")]},
    "sensor_display": {"need": [("display", "a display feature")],
                       "need_any": [(("temp_humidity", "pressure", "light_sensor"), "at least one environment sensor")]},
}


# =============================================================================================== main
def normalize(spec: Any, lib, raise_on_error: bool = False) -> tuple[Spec, dict]:
    sp = load_spec(spec) if not isinstance(spec, Spec) else spec
    sp = sp.model_copy(deep=True)
    errors: list[dict] = list(cross_check(sp))
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

    roles_present = [m.get("role") for m in feat_mods.values()] + [m.get("role") for _, m in extra_mods]

    # ---------------------------------------------------------------- template rules
    tmpl = sp.behaviour.template
    rules = TEMPLATE_RULES.get(tmpl, {})
    for role, why in rules.get("need", []):
        if role not in roles_present:
            cand = ", ".join(x["id"] for x in lib.by_role(role) if (x.get("aperture") or {}).get("feature_allowed")
                             and (x.get("backplane_v1") or {}).get("supported"))
            _err(errors, "features", f"{tmpl} needs {why}", f"add a feature with module {cand}" if cand else None, "template_requirement")
    for roles, why in rules.get("need_any", []):
        if not any(r in roles_present for r in roles):
            _err(errors, "features", f"{tmpl} needs {why}", f"add a feature whose module role is one of {', '.join(roles)}", "template_requirement")
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
    # in the box
    cbl = next(iter(lib.by_role("usb_cable")), None)
    if cbl:
        inst.append({"ref": "CBL1", "module": cbl["id"], "role": "usb_cable", "name": cbl.get("name", ""), "source": "implied",
                     "reason": "USB-C cable in the box"})
    if sp.electronics.power.adapter_in_box:
        psu = next(iter(lib.by_role("adapter")), None)
        if psu:
            inst.append({"ref": "PSU1", "module": psu["id"], "role": "adapter", "name": psu.get("name", ""), "source": "implied",
                         "reason": "adapter_in_box"})

    # ---------------------------------------------------------------- slots
    from .electrical import assign_slots

    cand = [i for i in inst if i["source"] in ("feature", "implied", "extra") and i["role"] not in ("backplane", "usb_cable", "adapter", "power_in")]
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
    x0, x1, y0, y1, z0, z1 = geom.bbox
    # ears / decor that stick out of the body
    for d in sp.decor:
        if d.type == "ear":
            xs = [d.at[0]] + ([-d.at[0]] if d.side == "both" else [])
            for xe in xs:
                x0, x1 = min(x0, xe - d.size[0] / 2), max(x1, xe + d.size[0] / 2)
            z1 = max(z1, d.at[1] + d.size[1] / 2)
    size = (x1 - x0, y1 - y0, z1 - z0)
    for axis, v in zip("XYZ", size):
        if v < SIZE_MIN - 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; minimum is {SIZE_MIN:g} mm", "make the body bigger", "size")
        if v > SIZE_MAX + 1e-6:
            _err(errors, "body", f"overall size along {axis} is {v:.0f} mm; maximum is {SIZE_MAX:g} mm", "scale the body down", "size")
    srt = sorted(size, reverse=True)
    if srt[0] > BED[0] or srt[1] > BED[1] or srt[2] > BED[2]:
        _err(errors, "body", f"body {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm does not fit the 250 x 210 x 210 mm printer bed",
             "scale it down so the longest side is <= 250 mm and the other two <= 210 mm", "bed")

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
        y_off, cart_box, why = _place_cartridge(geom, W, (kx0, kx1, ky0, ky1), floor, kz, rear_gap, kb, bosses,
                                                cart["board_bottom_above_floor"])
        if y_off is None:
            need_w = (kx1 - kx0) + 2 * CLEAR + 2 * geom.wall
            need_d = (ky1 - ky0) + CLEAR + rear_gap + 2 * geom.wall
            _err(errors, "body", f"the core cartridge ({kx1 - kx0:.0f} x {ky1 - ky0:.0f} x {kz:.0f} mm keep-out) does not fit inside the shell: {why}",
                 f"make the lower body at least {need_w:.0f} mm wide and {need_d:.0f} mm deep for the first {kz + floor:.0f} mm of height",
                 "cartridge_fit")
    derived_cart = None
    if y_off is not None:
        board_top = floor + cart["board_top_above_floor"]
        usb_center = (0.0 + (cart["usb_c_opening"]["center_board_x"] - W / 2), y_off + ky1, board_top + cart["usb_c_opening"]["center_above_board_top"])
        # gap from the receptacle face to the outer surface behind it
        t = geom.surface_depth("rear", usb_center[0], usb_center[2])
        wall_gap = None
        if t is not None:
            outer_y = 400.0 - t
            wall_gap = outer_y - (y_off + ky1)  # ky1 = receptacle front face (board frame)
            if wall_gap > geom.wall + 3.0:
                warnings.append(f"USB-C face sits {wall_gap:.1f} mm behind the outer rear surface; the shell needs a recess so the plug seats")
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
        )

    # ---------------------------------------------------------------- features: placement, depth, overlaps
    fboxes: dict[str, tuple] = {}
    frects: dict[str, tuple] = {}
    positions: dict[str, tuple] = {}
    for k, f in enumerate(sp.features):
        m = feat_mods.get(f.id)
        if m is None:
            continue
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
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            fa, fb = sp.feature(ids[a]), sp.feature(ids[b])
            if fa.face == fb.face and _overlap2(frects[ids[a]], frects[ids[b]], APERTURE_MARGIN):
                _err(errors, f"features[{sp.features.index(fb)}].at", f"{fb.id} opening overlaps {fa.id} (need {APERTURE_MARGIN:g} mm between openings)",
                     "move one of them apart", "feature_overlap")
            elif _overlap3(fboxes[ids[a]], fboxes[ids[b]], 1.0):
                _err(errors, f"features[{sp.features.index(fb)}].at", f"{fb.id} module collides with the {fa.id} module behind the wall",
                     "move one of them apart or onto another face", "feature_overlap")
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
            # Manhattan routing over the board edge with 25% slack and 40 mm for plug ends and service loop
            dist = (abs(start[0] - end[0]) + abs(start[1] - end[1]) + abs(start[2] - end[2])) * 1.25 + 40.0
        else:
            dist = 150.0
        cls = next((c for c in LENGTH_CLASSES if c >= dist), None)
        if cls is None:
            _err(errors, i.get("path", "features"), f"{i['ref']} is about {dist:.0f} mm of cable from {slot_id}; the longest harness is 300 mm",
                 "move the feature closer to the bottom rear of the body", "harness_length")
            cls = LENGTH_CLASSES[-1]
        hmod = f"CABLE_JSTPH_{slot['pins_count']}P_{cls}"
        if not lib.has(hmod):
            warnings.append(f"{i['ref']}: no library harness module {hmod}")
            hmod = None
        harness.append(DHarness(id=f"H{hid}", ref=i["ref"], slot=slot_id, kind="speaker_lead" if slot_id == "J_SPK" else "jst_ph",
                                cable=f"{slot['connector']} {slot['pins_count']}p {cls} mm", pins=slot["pins_count"], length_mm=cls,
                                distance_mm=_r(dist), module=hmod, module_end=_module_end(lib.get(i["module"]))))
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
                               position=i.get("position"), qty=1, reason=i.get("reason", ""), harness=i.get("harness")))
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
    report = {"ok": not errors, "errors": errors, "warnings": warnings, "added": added,
              "size_mm": [_r(v) for v in size], "instances": [i["ref"] for i in inst],
              "slots": {i["ref"]: i.get("slot") for i in inst if i.get("slot")}}
    if raise_on_error and errors:
        raise SpecError(errors)
    return sp, report


def _module_end(m: dict) -> str:
    if (m.get("prewire") or {}).get("lead"):
        return "factory_prewired"  # the factory solders the PH-terminated lead to the module before assembly
    mc = m.get("module_connector") or {}
    t, loc = str(mc.get("type", "")), str(mc.get("location", "")).lower()
    if t == "jst_ph_2.0":
        return "jst_ph"
    if t == "header_2.54":
        return "dupont_2.54"
    if "spade" in loc or "spade" in str((m.get("backplane_harness") or {})).lower():
        return "spade_2.8"
    return "solder"


# =============================================================================================== planned hardware
# Mirrors the CAD stage's fastener rules (gstudio.cad: internals.py cartridge, enclosure.py cover/plate, features.py kits):
#   cartridge 4 x (insert + M2.5 screw); cover/base plate 4 x (insert + M2.5 screw);
#   per feature: speaker ring 3, snap-in button 0, touch pad 0 (adhesive), every other board-clamped module 2 posts.
# All screws are M2.5 into M2.5 heat-set inserts. The S1 support posts take 2 x M2 screws into printed pilot holes
# (CAD models the posts, not those screws yet). These are PLANNED counts: components.json qty from CAD is authoritative.
MODULE_POSTS = {"speaker": 3, "touch": 0}


def module_posts(m: dict) -> int:
    role = m.get("role")
    if role in MODULE_POSTS:
        return MODULE_POSTS[role]
    if role == "button" and (m.get("mount") or {}).get("type") == "panel_snap":
        return 0
    return 2


def planned_hardware(sp: Spec, lib, feat_mods: dict, inst: list, harness: list, has_speaker: bool) -> list[DHardware]:
    hw: list[DHardware] = []

    def first(role, pred=None):
        xs = [m for m in lib.by_role(role) if pred is None or pred(m)]
        return xs[0] if xs else None

    screws = first("screws")
    ins = first("inserts", lambda m: "M2_5" in m["id"]) or first("inserts")
    n_cart, n_cover = 4, 4
    n_mod = sum(module_posts(m) for m in feat_mods.values())
    cover = "CoverScrews" if sp.body.split == "rear_cover" else "PlateScrews"
    if screws:
        sid, row = screws["id"], screws.get("catalog_row")
        hw.append(DHardware(id=sid, name="M2.5 socket head screw", qty=n_cart, size="M2.5", group="CartridgeScrews",
                            use="backplane to the 4 cartridge bosses", module=sid, catalog_row=row))
        hw.append(DHardware(id=sid, name="M2.5 socket head screw", qty=n_cover, size="M2.5", group=cover,
                            use="rear cover" if cover == "CoverScrews" else "base plate", module=sid, catalog_row=row))
        if n_mod:
            hw.append(DHardware(id=sid, name="M2.5 socket head screw", qty=n_mod, size="M2.5", group="ModuleScrews",
                                use="module clamp straps and the speaker ring", module=sid, catalog_row=row))
        if any(i.get("slot") == "S1" for i in inst):
            hw.append(DHardware(id=sid, name="M2 socket head screw", qty=2, size="M2", group="SupportPostScrews",
                                use="microSD module corners into the 2 printed support posts (1.8 mm pilot holes)",
                                module=sid, catalog_row=row))
    if ins:
        hw.append(DHardware(id=ins["id"], name="M2.5 heat-set insert", qty=n_cart + n_cover + n_mod, size="M2.5", group="HeatSetInserts",
                            use=f"{n_cart} cartridge + {n_cover} {'cover' if cover == 'CoverScrews' else 'plate'} + {n_mod} module bosses",
                            module=ins["id"], catalog_row=ins.get("catalog_row")))
    feet = first("feet")
    if feet and sp.body.feet:
        hw.append(DHardware(id=feet["id"], name="adhesive bumper foot", qty=sp.body.feet, group="Feet", use="underside",
                            module=feet["id"], catalog_row=feet.get("catalog_row")))
    ties = first("ties")
    if ties and harness:
        hw.append(DHardware(id=ties["id"], name="cable tie 100 x 2.5 mm", qty=max(3, len(harness) + 1), group="CableTies",
                            use="harness retention at printed anchors", module=ties["id"], catalog_row=ties.get("catalog_row")))
    if has_speaker:
        g = first("gasket")
        if g:
            hw.append(DHardware(id=g["id"], name="speaker foam gasket strip", qty=1, group="SpeakerGasket",
                                use="seal the speaker to the baffle", module=g["id"], catalog_row=g.get("catalog_row")))
    lab = first("label")
    if lab:
        hw.append(DHardware(id=lab["id"], name="device ID label + packing kit", qty=1, group="Label",
                            use="serial/QR label and packing", module=lab["id"], catalog_row=lab.get("catalog_row")))
    return hw


# =============================================================================================== cartridge search
def _place_cartridge(geom: ShellGeometry, W, plan, floor, kz, rear_gap, kb=0.0, bosses=(), boss_top=6.4):
    """Largest y offset (board y=0 -> world y_off) for which the keep-out box (from floor+kb to floor+kz) fits with
    CLEAR mm (rear: rear_gap) and every boss column (x, board y, radius) stands on the floor inside the cavity."""
    kx0, kx1, ky0, ky1 = plan
    wx0, wx1 = kx0 - W / 2, kx1 - W / 2
    zb0, zb1 = floor + kb, floor + kz

    def bosses_ok(y_off):
        for bx, by, br in bosses:
            for z in (floor + 0.5, floor + boss_top):
                for k in range(8):  # the boss foot fuses with the floor: test its rim laterally only
                    a = k * math.pi / 4
                    if not geom.inside(bx + br * math.cos(a), y_off + by + br * math.sin(a), z, margin=0.0):
                        return False, f"boss at x={bx:.0f}, y={y_off + by:.0f} has no flat floor under it"
        return True, ""

    def fits(y_off, step):
        ok, why = bosses_ok(y_off)
        if not ok:
            return ok, why
        bx0, bx1 = wx0 - CLEAR, wx1 + CLEAR
        by0, by1 = y_off + ky0 - CLEAR, y_off + ky1 + rear_gap
        bz1 = zb1 + CLEAR
        if isinstance(geom.body, SilhouetteBody):
            half = geom.body.depth / 2 - geom.wall
            if by0 < -half or by1 > half:
                return False, "not deep enough"
            # x-z rectangle boundary inside the inner outline
            xs = _lin(bx0, bx1, step)
            zs = _lin(zb0 + 0.01, bz1, step)
            for x in xs:
                for z in (zs[0], zs[-1]):
                    if geom.poly.sdf(x, z) > -geom.wall:
                        return False, f"too narrow or too low at x={x:.0f}, z={z:.0f}"
            for z in zs:
                for x in (xs[0], xs[-1]):
                    if geom.poly.sdf(x, z) > -geom.wall:
                        return False, f"too narrow at x={x:.0f}, z={z:.0f}"
            return True, ""
        xs = _lin(bx0, bx1, step)
        ys = _lin(by0, by1, step)
        zs = _lin(zb0 + 0.01, bz1, step)
        for x in xs:
            for y in ys:
                for z in (zs[0], zs[-1]):
                    if geom.outer_sdf(x, y, z) > -geom.wall:
                        return False, f"wall reached at x={x:.0f}, y={y:.0f}, z={z:.0f}"
        for z in zs:
            for x in xs:
                for y in (ys[0], ys[-1]):
                    if geom.outer_sdf(x, y, z) > -geom.wall:
                        return False, f"wall reached at x={x:.0f}, y={y:.0f}, z={z:.0f}"
            for y in ys:
                for x in (xs[0], xs[-1]):
                    if geom.outer_sdf(x, y, z) > -geom.wall:
                        return False, f"wall reached at x={x:.0f}, y={y:.0f}, z={z:.0f}"
        return True, ""

    _, _, gy0, gy1, _, _ = geom.bbox
    hi = gy1 - geom.wall - rear_gap - ky1
    lo = gy0 + geom.wall + CLEAR - ky0
    if hi < lo:
        return None, None, f"the body is {gy1 - gy0:.0f} mm deep; the cartridge needs {ky1 - ky0 + CLEAR + rear_gap + 2 * geom.wall:.0f} mm"
    ok, why = fits(hi, 6.0)
    if ok and fits(hi, 3.0)[0]:
        best = hi
    else:
        # scan forward in 2 mm steps for the first fit, then refine
        best = None
        y = hi
        while y >= lo:
            ok, why2 = fits(y, 6.0)
            if ok and fits(y, 3.0)[0]:
                best = y
                break
            why = why2 or why
            y -= 2.0
        if best is None:
            return None, None, why
        top = min(hi, best + 2.0)
        for _ in range(5):
            mid = (best + top) / 2
            if fits(mid, 3.0)[0]:
                best = mid
            else:
                top = mid
    box = (wx0, wx1, best + ky0, best + ky1, zb0, zb1)
    return best, box, ""


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
    if tunnel > 8.0:
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
    if back_ok:
        for du in (-fw / 2, fw / 2):
            for dv in (-fh / 2, fh / 2):
                p = geom.point(face, u + du * 0.9, v + dv * 0.9, plane + max(depth, 0.6))
                if not geom.inside(*p, margin=0.0):
                    back_ok = False
    if not back_ok:
        _err(errors, f"features[{k}].at", f"{f.id}: {m['id']} needs {depth:.0f} mm behind the wall (module + cable); about {avail:.0f} mm is available at {list(f.at)}",
             "move it where the body is deeper (toward the centre) or make the body deeper", "feature_depth")
    a = geom.point(face, u - fw / 2, v - fh / 2, plane)
    b = geom.point(face, u + fw / 2, v + fh / 2, plane + depth)
    box = (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), min(a[2], b[2]), max(a[2], b[2]))
    rect = (u - aw / 2, u + aw / 2, v - ah / 2, v + ah / 2)
    c = geom.point(face, u, v, plane + depth / 2)
    return box, rect, c


__all__ = ["normalize", "ShellGeometry", "TEMPLATE_RULES"]
