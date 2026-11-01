"""Classify CAD components into assembly roles and derive directions from geometry.

Nothing here is hardcoded to one gadget: groups come from component names, refs, electrical instances
and the library role, and every direction (insertion axis, which way a connector faces, where the
cover comes off) is measured from saved bounding boxes and anchors.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .data import Comp, ManualData, camel_words, kebab

Vec = tuple[float, float, float]

# ref prefix (normalize assigns DISP1, SPK1, MIC1, BTN1, LED1, AMP1, SD1, U1, BP1) -> role
REF_ROLES = [
    ("BP", "backplane"), ("DISP", "display"), ("SPK", "speaker"), ("MIC", "mic"), ("BTN", "button"),
    ("LED", "led"), ("AMP", "amp"), ("SD", "sd"), ("U", "core"), ("PWR", "power"), ("J_PWR", "power"),
]
ROLE_WORDS = {
    "backplane": ["backplane"],
    "core": ["esp32", "devkit", "core"],
    "amp": ["amp", "max98357"],
    "sd": ["microsd", "micro sd", "sd card", "sdcard", "sd module"],
    "speaker": ["speaker"],
    "display": ["display", "oled", "lcd", "tft", "screen", "ssd1306", "st7789", "gc9a01", "eyes"],
    "led": ["ws2812", "neopixel", "led", "light", "ring"],
    "button": ["button", "switch", "tactile", "push"],
    "mic": ["mic", "inmp441", "microphone"],
    "sensor": ["sensor", "bme", "sht", "aht", "bh1750", "imu", "rtc", "ldr", "temperature", "humidity"],
    "buzzer": ["buzzer", "piezo"],
    "touch": ["touch"],
}
ROLE_NOUN = {
    "backplane": "backplane", "core": "core board", "amp": "amplifier", "sd": "microSD module",
    "speaker": "speaker", "display": "display", "led": "light", "button": "button", "mic": "microphone",
    "sensor": "sensor", "buzzer": "buzzer", "touch": "touch pad", "power": "USB-C power input",
}
HARDWARE_ROLES = {"screws", "inserts", "feet", "ties", "gasket", "harness", "label", "adapter", "usb_cable", "cartridge",
                  "fasteners", "standoffs", "packing"}
FEATURE_ORDER = ["speaker", "display", "button", "led", "mic", "touch", "buzzer", "sensor"]

AXES: dict[Vec, str] = {
    (1.0, 0.0, 0.0): "+X (the gadget's left)",
    (-1.0, 0.0, 0.0): "-X (the gadget's right)",
    (0.0, -1.0, 0.0): "-Y (front)",
    (0.0, 1.0, 0.0): "+Y (rear)",
    (0.0, 0.0, 1.0): "+Z (up)",
    (0.0, 0.0, -1.0): "-Z (down)",
}
FACE_NORMALS: dict[str, Vec] = {
    "front": (0.0, -1.0, 0.0), "rear": (0.0, 1.0, 0.0), "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0),
    # spec faces left/right are the gadget's own sides; +X is the gadget's left (ARCHITECTURE §0)
    "left": (1.0, 0.0, 0.0), "right": (-1.0, 0.0, 0.0),
}


# --------------------------------------------------------------------------------------------- vectors
def vsub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vadd(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vscale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def vnorm(a: Vec) -> Vec:
    n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def dominant_axis(v: Vec) -> Vec:
    i = max(range(3), key=lambda k: abs(v[k]))
    out = [0.0, 0.0, 0.0]
    out[i] = 1.0 if v[i] >= 0 else -1.0
    return (out[0], out[1], out[2])


def axis_phrase(v: Vec) -> str:
    return AXES[dominant_axis(v)]


def axis_short(v: Vec) -> str:
    return axis_phrase(v).split(" ", 1)[0]


def bbox_union(boxes: Iterable[Optional[tuple]]) -> Optional[tuple]:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return (min(b[0] for b in bs), max(b[1] for b in bs), min(b[2] for b in bs),
            max(b[3] for b in bs), min(b[4] for b in bs), max(b[5] for b in bs))


def bbox_offset(b: tuple, d: Vec) -> tuple:
    return (b[0] + d[0], b[1] + d[0], b[2] + d[1], b[3] + d[1], b[4] + d[2], b[5] + d[2])


def bbox_center(b: tuple) -> Vec:
    return ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)


def bbox_size(b: tuple) -> Vec:
    return (b[1] - b[0], b[3] - b[2], b[5] - b[4])


def bbox_gap(a: tuple, b: tuple) -> float:
    """Euclidean gap between two boxes (0 if they touch or overlap)."""
    dx = max(0.0, max(a[0], b[0]) - min(a[1], b[1]))
    dy = max(0.0, max(a[2], b[2]) - min(a[3], b[3]))
    dz = max(0.0, max(a[4], b[4]) - min(a[5], b[5]))
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# --------------------------------------------------------------------------------------------- groups
@dataclass
class Group:
    """One installable unit: a module plus the printed parts and fasteners that go with it."""

    key: str  # stable slug (feature id, or role)
    role: str
    module: Optional[Comp]
    ref: Optional[str] = None
    feature_id: Optional[str] = None
    prints: list[Comp] = field(default_factory=list)
    extras: list[Comp] = field(default_factory=list)  # purchased accessories with the same ref (gaskets, pads)
    fasteners: list[Comp] = field(default_factory=list)
    normal: Optional[Vec] = None  # outward normal of the face it sits behind
    face: Optional[str] = None
    label: Optional[str] = None  # spec feature label (user-facing words)

    @property
    def comps(self) -> list[Comp]:
        return [c for c in [self.module, *self.extras, *self.prints, *self.fasteners] if c is not None]


@dataclass
class Assembly:
    data: ManualData
    shell: Optional[Comp] = None
    covers: list[Comp] = field(default_factory=list)
    shell_extras: list[Comp] = field(default_factory=list)  # other prints fixed to the shell (none usually)
    split: str = "rear_cover"
    open_dir: Vec = (0.0, 1.0, 0.0)
    backplane: Optional[Comp] = None
    core: Optional[Comp] = None
    cartridge_mods: list[Comp] = field(default_factory=list)
    cartridge_prints: list[Comp] = field(default_factory=list)
    cartridge_fasteners: list[Comp] = field(default_factory=list)
    features: list[Group] = field(default_factory=list)
    internals: list[Group] = field(default_factory=list)
    inserts: list[Comp] = field(default_factory=list)
    cover_fasteners: list[Comp] = field(default_factory=list)
    feet: list[Comp] = field(default_factory=list)
    ties: list[Comp] = field(default_factory=list)
    cables: list[Comp] = field(default_factory=list)  # REFERENCE_* cable envelopes
    harness_parts: list[Comp] = field(default_factory=list)  # PURCHASE_* harness cables, if modelled
    other: list[Comp] = field(default_factory=list)
    keepouts: list[Comp] = field(default_factory=list)

    # ------------------------------------------------------------------ convenience
    @property
    def cartridge(self) -> list[Comp]:
        return [c for c in [self.backplane, self.core, *self.cartridge_mods, *self.cartridge_prints] if c]

    @property
    def prints(self) -> list[Comp]:
        return [c for c in self.data.comps.values() if c.kind == "PRINT"]

    @property
    def purchases(self) -> list[Comp]:
        return [c for c in self.data.comps.values() if c.kind == "PURCHASE"]

    @property
    def body(self) -> list[Comp]:
        return [c for c in [self.shell, *self.shell_extras] if c]

    def bbox(self, comps: Iterable[Comp]) -> Optional[tuple]:
        return bbox_union(c.bbox for c in comps)

    @property
    def overall_bbox(self) -> Optional[tuple]:
        vis = [c for c in self.data.comps.values() if c.kind in ("PRINT", "PURCHASE")]
        return self.bbox(vis)

    def group_of(self, comp_name: str) -> Optional[Group]:
        for g in [*self.features, *self.internals]:
            if any(c.name == comp_name for c in g.comps):
                return g
        return None


def _words(c: Comp) -> str:
    return f"{camel_words(c.name)} {c.label} {c.module_id or ''}".lower()


def role_of_ref(ref: Optional[str]) -> Optional[str]:
    if not ref:
        return None
    up = ref.upper()
    for pre, role in REF_ROLES:
        if re.fullmatch(rf"{pre}\d*", up):
            return role
    return None


def role_of(c: Comp, data: ManualData) -> Optional[str]:
    r = role_of_ref(c.ref)
    if r:
        return r
    inst = data.instance(c.ref) if c.ref else {}
    for cand in (inst.get("role"), (data.lib.get(c.module_id) or {}).get("role")):
        if cand:
            cand = str(cand).lower()
            for role, words in ROLE_WORDS.items():
                if cand == role or any(w in cand for w in words):
                    return role
            return cand
    w = _words(c)
    for role, words in ROLE_WORDS.items():
        if any(x in w for x in words):
            return role
    return None


def _has(c: Comp, *words: str) -> bool:
    w = _words(c)
    return any(x in w for x in words)


def classify(data: ManualData) -> Assembly:
    A = Assembly(data=data, split=data.split)
    comps = list(data.comps.values())
    prints = [c for c in comps if c.kind == "PRINT"]
    purchases = [c for c in comps if c.kind == "PURCHASE"]
    refs = [c for c in comps if c.kind == "REFERENCE"]

    # ---- shell and covers
    def vol(c: Comp) -> float:
        s = c.size
        return s[0] * s[1] * s[2]

    shells = [c for c in prints if _has(c, "shell", "body", "housing", "enclosure")]
    A.shell = max(shells or prints, key=vol, default=None)
    covers = [c for c in prints if c is not A.shell and _has(c, "cover", "lid", "base plate", "baseplate", "back plate", "backplate", "bottom")]
    if not covers and A.shell is not None:
        # a print nearly as wide as the shell sitting on one face is the closure part
        sx, sy, sz = A.shell.size
        for c in prints:
            if c is A.shell or not c.bbox:
                continue
            cx, cy, cz = c.size
            if (cx > 0.7 * sx and cz > 0.7 * sz) or (cx > 0.7 * sx and cy > 0.7 * sy):
                covers.append(c)
    A.covers = covers
    if A.shell is not None and covers and A.shell.bbox and covers[0].bbox:
        A.open_dir = dominant_axis(vsub(covers[0].center, A.shell.center))
        A.split = "base_plate" if A.open_dir == (0.0, 0.0, -1.0) else "rear_cover"
    else:
        A.open_dir = (0.0, 0.0, -1.0) if A.split == "base_plate" else (0.0, 1.0, 0.0)

    # ---- electronics by role
    groups: dict[str, Group] = {}
    for c in purchases:
        role = role_of(c, data)
        if role == "backplane":
            A.backplane = A.backplane or c
            continue
        if role == "core":
            A.core = A.core or c
            continue
        if role in ("amp", "sd"):
            A.cartridge_mods.append(c)
            continue
        if not c.ref and (role in HARDWARE_ROLES or _has(c, "screw", "insert", "feet", "foot", "bumper", "tie", "nut", "bolt",
                                                          "standoff", "washer", "label", "cable", "harness")):
            continue  # kit hardware (it may carry a library module_id): handled below, never a feature step
        inst = data.instance(c.ref) if c.ref else {}
        fid = c.feature_id or inst.get("feature")
        same = next((g for g in groups.values() if c.ref and g.ref == c.ref), None)
        if same is not None and not c.module_id:
            same.extras.append(c)  # e.g. PURCHASE_MicGasket next to PURCHASE_Microphone
            continue
        if same is not None and same.module is not None and not same.module.module_id and c.module_id:
            # the earlier one was the accessory: swap roles and re-key the group under the real module
            old_key = next(k for k, g in groups.items() if g is same)
            same.extras.append(same.module)
            same.module = c
            groups[c.name] = groups.pop(old_key)
            continue
        is_module = bool(c.ref or c.module_id or fid)
        if not is_module:
            continue
        if role is None and not fid:
            continue
        feat = data.feature(fid) if fid else {}
        key = kebab(fid) if fid else kebab(c.ref or role or c.name)
        g = Group(key=key, role=role or "module", module=c, ref=c.ref, feature_id=fid,
                  face=feat.get("face") or inst.get("face"), label=feat.get("label"))
        groups[c.name] = g
    taken = {c.name for c in [A.backplane, A.core, *A.cartridge_mods] if c} | {
        x.name for g in groups.values() for x in [g.module, *g.extras] if x}

    # ---- fasteners, inserts, feet, ties, harness
    rest = [c for c in purchases if c.name not in taken]
    for c in rest:
        if _has(c, "insert"):
            A.inserts.append(c)
        elif _has(c, "foot", "feet", "bumper"):
            A.feet.append(c)
        elif _has(c, "tie", "strap", "velcro"):
            A.ties.append(c)
        elif _has(c, "screw", "bolt", "nut", "fastener", "standoff"):
            pass  # assigned below
        elif _has(c, "cable", "harness", "lead", "wire", "jst"):
            A.harness_parts.append(c)
        else:
            A.other.append(c)
    feats = list(groups.values())
    for c in rest:
        if not _has(c, "screw", "bolt", "nut", "fastener", "standoff") or _has(c, "insert"):
            continue
        if _has(c, "cover", "lid", "base", "back"):
            A.cover_fasteners.append(c)
            continue
        if _has(c, "cartridge", "backplane", "board", "pcb"):
            A.cartridge_fasteners.append(c)
            continue
        hit = next((g for g in feats if g.role in _words(c) or (g.feature_id and g.feature_id.lower() in _words(c))), None)
        if hit:
            hit.fasteners.append(c)
            continue
        boxes = data.solid_boxes.get(c.name) or []
        if len(boxes) > 1 and feats:
            owners: dict[str, list[int]] = {}
            for k, sb in enumerate(boxes):
                cands = [(bbox_gap(sb, bbox_union([g.module.bbox] + [p.bbox for p in g.prints])), g.key) for g in feats
                         if g.module and g.module.bbox]
                if A.covers and A.covers[0].bbox:
                    cands.append((bbox_gap(sb, A.covers[0].bbox) + 0.5, "#cover"))
                if A.backplane and A.backplane.bbox:
                    cands.append((bbox_gap(sb, A.backplane.bbox) + 0.5, "#cart"))
                owners.setdefault(min(cands)[1] if cands else "#other", []).append(k)
            if len(owners) > 1:
                for key, idx in owners.items():
                    sub = subcomp(data, c, idx)
                    if key == "#cover":
                        A.cover_fasteners.append(sub)
                    elif key == "#cart":
                        A.cartridge_fasteners.append(sub)
                    elif key == "#other":
                        A.other.append(sub)
                    else:
                        next(g for g in feats if g.key == key).fasteners.append(sub)
                continue
        # nearest owner by geometry
        cands: list[tuple[float, str, object]] = []
        if A.covers and c.bbox and A.covers[0].bbox:
            cands.append((bbox_gap(c.bbox, A.covers[0].bbox), "cover", None))
        if A.backplane and c.bbox and A.backplane.bbox:
            cands.append((bbox_gap(c.bbox, A.backplane.bbox), "cart", None))
        for g in feats:
            if g.module and g.module.bbox and c.bbox:
                cands.append((bbox_gap(c.bbox, g.module.bbox), "feat", g))
        if not cands:
            A.other.append(c)
            continue
        _, kind, g = min(cands, key=lambda t: t[0])
        if kind == "cover":
            A.cover_fasteners.append(c)
        elif kind == "cart":
            A.cartridge_fasteners.append(c)
        else:
            g.fasteners.append(c)  # type: ignore[union-attr]

    # ---- other prints: cartridge tray, speaker ring, diffuser, bezels...
    for c in prints:
        if c is A.shell or c in A.covers:
            continue
        if _has(c, "cartridge", "tray", "sled", "carrier"):
            A.cartridge_prints.append(c)
            continue
        owner = None
        for g in feats:
            if (c.feature_id and c.feature_id == g.feature_id) or (c.ref and c.ref == g.ref):
                owner = g
                break
        if owner is None:
            for g in feats:
                words = ROLE_WORDS.get(g.role, []) + [g.role]
                if g.feature_id:
                    words.append(g.feature_id.replace("_", " ").lower())
                if any(w in _words(c) for w in words):
                    owner = g
                    break
        if owner is None and c.bbox:
            near = [(bbox_gap(c.bbox, g.module.bbox), g) for g in feats if g.module and g.module.bbox]
            near = [t for t in near if t[0] < 3.0]
            if near:
                owner = min(near, key=lambda t: t[0])[1]
        if owner is not None:
            owner.prints.append(c)
        else:
            A.shell_extras.append(c)

    # ---- references
    for c in refs:
        if _has(c, "keep out", "keepout", "zone", "envelope"):
            A.keepouts.append(c)
        else:
            A.cables.append(c)

    # ---- normals and order
    for g in feats:
        g.normal = feature_normal(A, g)
    aperture = [g for g in feats if g.feature_id]
    internal = [g for g in feats if not g.feature_id]

    def order_key(g: Group) -> tuple:
        r = FEATURE_ORDER.index(g.role) if g.role in FEATURE_ORDER else len(FEATURE_ORDER)
        z = -(g.module.center[2] if g.module and g.module.bbox else 0)
        return (r, z, g.key)

    A.features = sorted(aperture, key=order_key)
    A.internals = sorted(internal, key=order_key)
    return A


def subcomp(data: ManualData, c: Comp, idx: list[int]) -> Comp:
    """A subset of the solids of a multi-solid component (e.g. the 2 screws of one clip out of 9)."""
    from dataclasses import replace

    boxes = data.solid_boxes.get(c.name) or []
    name = f"{c.name}#{','.join(str(i) for i in idx)}"
    sub = replace(c, name=name, qty=len(idx), bbox=bbox_union(boxes[i] for i in idx if i < len(boxes)) or c.bbox,
                  solids=len(idx), raw={**c.raw, "base": c.name, "solid_index": idx})
    data.subcomps[name] = sub
    return sub


def feature_normal(A: Assembly, g: Group) -> Vec:
    """Outward normal of the face this module sits behind: measured from geometry when possible (the
    nearest shell bbox face), with the spec face as a tie-break and as the fallback without geometry."""
    if g.module and g.module.bbox and A.shell and A.shell.bbox:
        sb, mb = A.shell.bbox, g.module.bbox
        gaps = {
            (1.0, 0.0, 0.0): sb[1] - mb[1], (-1.0, 0.0, 0.0): mb[0] - sb[0],
            (0.0, 1.0, 0.0): sb[3] - mb[3], (0.0, -1.0, 0.0): mb[2] - sb[2],
            (0.0, 0.0, 1.0): sb[5] - mb[5], (0.0, 0.0, -1.0): mb[4] - sb[4],
        }
        best = min(gaps, key=lambda k: gaps[k])
        spec_n = FACE_NORMALS.get(g.face or "")
        if spec_n in gaps and gaps[spec_n] <= gaps[best] + 2.0:
            return spec_n  # type: ignore[return-value]
        return best
    return FACE_NORMALS.get(g.face or "front", (0.0, -1.0, 0.0))


def comp_qty(data: ManualData, c: Comp) -> tuple[int, str]:
    """Quantity of a component and its basis: explicit > purchased[] > hardware list > CAD solids."""
    if c.qty:
        return int(c.qty), "cad"
    m = re.search(r"(?:^|\s)[x×]\s?(\d+)\s*$", c.label or "")
    if m:
        return int(m.group(1)), "cad label"
    if c.kind in ("PRINT", "TEST", "REFERENCE"):
        return 1, "default"
    for p in data.purchased():
        if p.get("modelPart") == c.name and p.get("quantity"):
            return int(p["quantity"]), "purchased"
    hw_like = re.search(r"screw|nut|insert|feet|foot|tie|bolt|standoff|gasket|pad|tape|washer", c.name, re.I)
    toks = set(kebab(camel_words(c.name)).split("-")) if hw_like and not (c.ref or c.module_id) else set()
    best, score = None, 0
    for h in data.hardware():
        ht = set(kebab(f"{h.get('id', '')} {h.get('name', '')} {h.get('use', '')}").split("-"))
        s = len(toks & ht)
        if s > score and h.get("qty"):
            best, score = h, s
    if best and score >= 1 and (c.solids <= 1 or int(best["qty"]) <= c.solids):
        return int(best["qty"]), "hardware"
    if c.solids > 1 and re.search(r"screw|nut|insert|feet|foot|tie|bolt|standoff", c.name, re.I):
        return int(c.solids), "solids"
    return 1, "default"


def anchor_owner(A: Assembly, key: str) -> Optional[Comp]:
    """'DISP1.CONN' -> component with ref DISP1; 'BP1.J_I2C1' -> backplane; also accepts comp names."""
    head = key.split(".", 1)[0]
    if head in A.data.comps:
        return A.data.comps[head]
    c = A.data.comp_for_ref(head)
    if c:
        return c
    if head.upper().startswith("BP") and A.backplane:
        return A.backplane
    return None


def anchors_for(A: Assembly, ref_or_name: str) -> dict[str, Vec]:
    pre = ref_or_name + "."
    return {k: v for k, v in A.data.anchors.items() if k.startswith(pre)}


def connector_dir(A: Assembly, c: Comp, words: tuple[str, ...] = ("usb",)) -> Optional[Vec]:
    """Direction from a component's centre to its first anchor whose name contains one of `words`."""
    if not c.bbox:
        return None
    for key, p in anchors_for(A, c.ref or c.name).items():
        tail = key.split(".", 1)[1].lower()
        if any(w in tail for w in words):
            return dominant_axis(vsub(p, c.center))
    return None


def overhang_dir(inner: Comp, outer: Comp, min_mm: float = 0.5) -> Optional[Vec]:
    """Axis along which `inner` sticks out past `outer` in XY (e.g. devkit USB past the backplane edge)."""
    if not inner.bbox or not outer.bbox:
        return None
    a, b = inner.bbox, outer.bbox
    cands = {(1.0, 0.0, 0.0): a[1] - b[1], (-1.0, 0.0, 0.0): b[0] - a[0], (0.0, 1.0, 0.0): a[3] - b[3], (0.0, -1.0, 0.0): b[2] - a[2]}
    k = max(cands, key=lambda d: cands[d])
    return k if cands[k] >= min_mm else None
