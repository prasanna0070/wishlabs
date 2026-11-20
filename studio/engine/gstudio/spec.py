"""gadget-spec/1 pydantic models (ARCHITECTURE §3). Owned by foundation.

The LLM writes this JSON; the engine validates it here and completes it in gstudio.normalize.
Coordinates: millimetres, +Z up, the front faces -Y, the gadget stands on Z=0, +X is the viewer's right
when looking at the front. Origin = centre of the footprint at the table plane.
Front-view positions ("at" on the front face, decor, outline) are (x, z).

Public API (stable, extend only):
  Spec, load_spec(dict | path | json str) -> Spec      raises SpecError with short actionable items
  validate_spec(data) -> (Spec | None, errors)
  spec_json_schema(compact=True) -> dict                schema for the LLM prompt (no engine-only fields)
  Spec.model_json_schema()                              full pydantic schema (also hides `derived`)
  Geometry helpers used by other stages: polygon_area, polygon_is_simple, segments_intersect,
  catmull_rom_closed, outline_points(spec_body)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from .errors import SpecError
from .version import SPEC_SCHEMA

# --------------------------------------------------------------------------------------------- bounds
MAX_XY = 130.0  # |x|, |y| of any body coordinate (overall size <= 260 mm)
MAX_Z = 260.0
MIN_BOTTOM_WIDTH = 30.0  # flat contact segment length on the table
EPS = 1e-6
# kind "printed" (CATALOG-PLAN E2, pack `printed`): objects with no electronics may be small. The body validators read
# the kind from the validation context that load_spec passes; without it they keep the gadget floors.
GADGET_MIN_AREA = 1500.0  # mm2 enclosed by a silhouette outline (15 cm2)
PRINTED_MIN_AREA = 400.0  # 4 cm2
GADGET_MIN_DEPTH = 40.0  # silhouette depth / revolve height
PRINTED_MIN_SIZE = 20.0  # mm per axis for printed objects (normalize's size floor for printed is the same)
PRINTED_MIN_BOTTOM_WIDTH = 10.0


def _printed_ctx(info: Optional[ValidationInfo]) -> bool:
    ctx = getattr(info, "context", None) if info is not None else None
    return isinstance(ctx, dict) and ctx.get("kind") == "printed"

Num = float
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", description="hex colour #RRGGBB")]
LibId = Annotated[
    str,
    Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$", description="library module id"),
]
Slug = Annotated[str, Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9-]*$")]
FeatureId = Annotated[str, Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")]
XZ = Annotated[
    tuple[Annotated[float, Field(ge=-MAX_XY, le=MAX_XY)], Annotated[float, Field(ge=0, le=MAX_Z)]],
    Field(description="front-view point [x, z] mm"),
]
FacePoint = Annotated[
    tuple[Annotated[float, Field(ge=-MAX_Z, le=MAX_Z)], Annotated[float, Field(ge=-MAX_Z, le=MAX_Z)]],
    Field(description="2D point on the face: front/rear [x,z], left/right [y,z], top [x,y]"),
]
Size2 = Annotated[
    tuple[Annotated[float, Field(ge=1, le=200)], Annotated[float, Field(ge=1, le=200)]],
    Field(description="[width, height] mm"),
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False, str_strip_whitespace=True)


# --------------------------------------------------------------------------------------------- basics
class Audience(_M):
    child_directed: bool = False
    age_years: Optional[int] = Field(None, ge=0, le=120)


class Colors(_M):
    body: HexColor = "#8CC9C0"
    accent: HexColor = "#F4F1EA"
    detail: HexColor = "#2B3A42"
    body_name: str = Field("pale teal", max_length=32)
    accent_name: Optional[str] = Field(None, max_length=32)
    detail_name: Optional[str] = Field(None, max_length=32)


# --------------------------------------------------------------------------------------------- body
class _BodyCommon(_M):
    wall: float = Field(2.4, ge=2.0, le=3.5, description="shell wall mm")
    edge_round: float = Field(
        8.0, ge=0, le=25,
        description="perimeter fillet mm (silhouette: front/rear edges; revolve/blob: bottom edge); reduced if it fails",
    )
    feet: int = Field(4, ge=0, le=6, description="adhesive anti-slip feet count (0, 3, 4 or 6)")

    @field_validator("feet")
    @classmethod
    def _feet(cls, v: int) -> int:
        if v not in (0, 3, 4, 6):
            raise ValueError("feet must be 0, 3, 4 or 6")
        return v


class SilhouetteBody(_BodyCommon):
    """Closed front-view outline (x,z), counter-clockwise, smooth spline through the points, with a flat
    bottom segment on z=0 (two consecutive points with z=0). Extruded along Y by `depth`, centred on y=0."""

    family: Literal["silhouette"]
    outline: list[XZ] = Field(..., min_length=4, max_length=80)
    depth: float = Field(..., ge=40, le=210, description="extrusion along Y mm")
    split: Literal["rear_cover", "base_plate", "none"] = "rear_cover"

    @field_validator("depth", mode="wrap")
    @classmethod
    def _depth(cls, v: Any, handler: Any, info: ValidationInfo) -> float:
        # printed objects (no electronics) may be as thin as PRINTED_MIN_SIZE; gadgets keep ge=40 (schema unchanged)
        if (_printed_ctx(info) and isinstance(v, (int, float)) and not isinstance(v, bool)
                and PRINTED_MIN_SIZE <= float(v) < GADGET_MIN_DEPTH):
            return float(v)
        return handler(v)

    @field_validator("outline")
    @classmethod
    def _outline(cls, pts: list[tuple[float, float]], info: ValidationInfo) -> list[tuple[float, float]]:
        pts = [(float(x), float(z)) for x, z in pts]
        _check_no_duplicates(pts)
        if polygon_area(pts) < 0:  # make it counter-clockwise in (x right, z up)
            pts = list(reversed(pts))
        bad = polygon_self_intersections(pts)
        if bad:
            i, j = bad[0]
            raise ValueError(
                f"outline crosses itself (segment {i}->{(i + 1) % len(pts)} meets segment {j}->{(j + 1) % len(pts)}); "
                "list the points in order around the shape"
            )
        printed = _printed_ctx(info)
        if abs(polygon_area(pts)) < (PRINTED_MIN_AREA if printed else GADGET_MIN_AREA):
            raise ValueError("outline encloses less than 4 cm^2; make the object bigger" if printed else
                             "outline encloses less than 15 cm^2; make the body bigger")
        _check_flat_bottom(pts, "outline", PRINTED_MIN_BOTTOM_WIDTH if printed else MIN_BOTTOM_WIDTH)
        return pts


class RevolveBody(_BodyCommon):
    """Half profile (r,z) from (0,0) bottom centre to (0,h) top centre, revolved about Z.
    profile[1] must also have z=0 (flat bottom ring). squash_y scales the depth (Y) only."""

    family: Literal["revolve"]
    profile: list[Annotated[tuple[Annotated[float, Field(ge=0, le=MAX_XY)], Annotated[float, Field(ge=0, le=MAX_Z)]], Field()]] = Field(
        ..., min_length=3, max_length=80
    )
    squash_y: float = Field(1.0, ge=0.5, le=1.5)
    split: Literal["base_plate", "rear_cover", "none"] = "base_plate"

    @field_validator("profile")
    @classmethod
    def _profile(cls, pts: list[tuple[float, float]], info: ValidationInfo) -> list[tuple[float, float]]:
        pts = [(float(r), float(z)) for r, z in pts]
        printed = _printed_ctx(info)
        h_min = PRINTED_MIN_SIZE if printed else GADGET_MIN_DEPTH
        w_min = PRINTED_MIN_BOTTOM_WIDTH if printed else MIN_BOTTOM_WIDTH
        if abs(pts[0][0]) > EPS or abs(pts[0][1]) > EPS:
            raise ValueError("profile must start at [0, 0] (bottom centre)")
        if abs(pts[-1][0]) > EPS or pts[-1][1] < h_min:
            raise ValueError(f"profile must end at [0, h] (top centre) with h >= {h_min:g}")
        if abs(pts[1][1]) > EPS or pts[1][0] < w_min / 2:
            raise ValueError(f"profile[1] must be [r, 0] with r >= {w_min / 2:g} (flat bottom)")
        for k, (r, _z) in enumerate(pts[1:-1], start=1):
            if r <= EPS:
                raise ValueError(f"profile point {k} has r=0; only the first and last points may touch the axis")
        _check_no_duplicates(pts)
        bad = polygon_self_intersections(pts)
        if bad:
            i, j = bad[0]
            raise ValueError(f"profile crosses itself or the axis (segments {i} and {j}); list points bottom to top")
        return pts


SCULPT_KINDS = ("cone", "torus", "tube")  # pack `sculpt` (CATALOG-PLAN E4); geometry in gstudio.sdf_ext and cad/body.py (D)
BLOB_MAX = 12  # primitives per blob while pack `sculpt` is off (24 with it)
BLOB_MAX_SCULPT = 24


def _rot_matrix(rot) -> list[list[float]]:
    """R = Rz @ Ry @ Rx for rot = [rx, ry, rz] degrees: the primitive is turned about x, then y, then z, about its centre."""
    ax, ay, az = (math.radians(float(v)) for v in rot)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


class Primitive(_M):
    kind: Literal["rounded_box", "sphere", "ellipsoid", "cylinder", "capsule", "cone", "torus", "tube"]
    center: tuple[
        Annotated[float, Field(ge=-MAX_XY, le=MAX_XY)],
        Annotated[float, Field(ge=-MAX_XY, le=MAX_XY)],
        Annotated[float, Field(ge=-50, le=MAX_Z)],
    ]
    size: tuple[
        Annotated[float, Field(ge=4, le=260)],
        Annotated[float, Field(ge=4, le=260)],
        Annotated[float, Field(ge=4, le=260)],
    ] = Field(..., description="bounding box [sx,sy,sz] mm (sphere: [d,d,d])")
    radius: float = Field(4.0, ge=0, le=60, description="corner/blend fillet mm")
    axis: Literal["x", "y", "z"] = Field("z", description="cylinder/capsule axis")
    rot: tuple[
        Annotated[float, Field(ge=-180, le=180)],
        Annotated[float, Field(ge=-180, le=180)],
        Annotated[float, Field(ge=-180, le=180)],
    ] = Field((0.0, 0.0, 0.0), description="[rx, ry, rz] degrees: turn the finished primitive about its centre, first "
              "about world x, then y, then z; size and axis are before turning")
    tip: Optional[float] = Field(None, ge=0, le=260, description="cone only: diameter of the flat top at the +axis end, mm "
                                 "(0 or left out = pointed); the base (at the -axis end) spans the other two sizes")
    sweep: Optional[float] = Field(None, ge=30, le=330, description="tube only: an arc of the torus ring, in degrees (180 "
                                   "when left out), from the first other axis turning about +axis; round ends")

    @model_validator(mode="after")
    def _sculpt_shape(self) -> "Primitive":
        # CATALOG-PLAN E4 + d-to-e 6: cone, torus and tube (an arc of a torus) use `axis`; other axes in x, y, z order
        other = [v for a, v in zip("xyz", self.size) if a != self.axis]
        along = self.size["xyz".index(self.axis)]
        if self.tip is not None and self.kind != "cone":
            raise ValueError("tip is only for kind cone")
        if self.sweep is not None and self.kind != "tube":
            raise ValueError("sweep is only for kind tube")
        if self.kind == "cone" and (self.tip or 0.0) > min(other):
            raise ValueError(f"cone tip {self.tip:g} mm is wider than its base ({min(other):g} mm)")
        if self.kind in ("torus", "tube") and along >= min(other) / 2:
            raise ValueError(f"{self.kind} ring thickness (size along {self.axis}, {along:g} mm) must be below half the "
                             f"smaller outer diameter ({min(other) / 2:g} mm) so the ring has a hole")
        return self

    def aabb(self) -> tuple[float, float, float, float, float, float]:
        """World bounding box; a turned primitive gets the box around its turned bounding box (conservative)."""
        (cx, cy, cz), (sx, sy, sz) = self.center, self.size
        if not any(self.rot):
            return (cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2, cz - sz / 2, cz + sz / 2)
        R = _rot_matrix(self.rot)
        h = (sx / 2, sy / 2, sz / 2)
        e = [sum(abs(R[i][j]) * h[j] for j in range(3)) for i in range(3)]
        return (cx - e[0], cx + e[0], cy - e[1], cy + e[1], cz - e[2], cz + e[2])


class BlobBody(_BodyCommon):
    """Union of primitives, each filleted, cut flat at z=0. Good for head-on-body bots and ears."""

    family: Literal["blob"]
    primitives: list[Primitive] = Field(..., min_length=1, max_length=BLOB_MAX_SCULPT)
    split: Literal["base_plate", "rear_cover", "none"] = "base_plate"

    @field_validator("primitives")
    @classmethod
    def _prims(cls, prims: list[Primitive]) -> list[Primitive]:
        boxes = [p.aabb() for p in prims]
        for k, b in enumerate(boxes):
            if b[5] <= 5:
                raise ValueError(f"primitive {k} is (almost) entirely below the table z=0; raise its center z")
        if min(b[4] for b in boxes) > 0.5:
            raise ValueError("no primitive reaches the table: the lowest primitive bottom (center z - size z/2) must be <= 0")
        # every primitive must touch the group (approximate: AABB overlap graph is connected)
        n = len(boxes)
        seen = {0}
        stack = [0]
        while stack:
            a = stack.pop()
            for b in range(n):
                if b not in seen and _aabb_overlap(boxes[a], boxes[b], 1.0):
                    seen.add(b)
                    stack.append(b)
        loose = sorted(set(range(n)) - seen)
        if loose:
            raise ValueError(f"primitive(s) {loose} do not touch the others; move them so the union is one piece")
        return prims


Body = Annotated[Union[SilhouetteBody, RevolveBody, BlobBody], Field(discriminator="family")]

_FAMILY_KEYS = {
    "silhouette": {"outline", "depth"},
    "revolve": {"profile", "squash_y"},
    "blob": {"primitives"},
}


# --------------------------------------------------------------------------------------------- decor
Relief = Literal["engrave", "emboss"]
DecorColor = Literal["body", "accent", "detail"]


class _DecorBase(_M):
    at: XZ
    color: Optional[DecorColor] = Field(None, description="colour token for renders; default per type")


class FacePlate(_DecorBase):
    type: Literal["face_plate"]
    shape: Literal["circle", "ellipse", "rounded_rect"] = "circle"
    size: Size2
    depth: float = Field(1.2, ge=0.4, le=3.0)
    relief: Relief = "engrave"


class Eye(_DecorBase):
    type: Literal["eye"]
    style: Literal["dot", "oval", "arc_closed", "arc_happy", "star", "heart"] = "dot"
    size: Size2
    relief: Relief = "engrave"
    depth: float = Field(0.8, ge=0.3, le=2.0)


class Mouth(_DecorBase):
    type: Literal["mouth"]
    style: Literal["smile", "open", "small_o", "cat"] = "smile"
    size: Size2
    relief: Relief = "engrave"
    depth: float = Field(0.8, ge=0.3, le=2.0)


class Blush(_DecorBase):
    type: Literal["blush"]
    size: Size2
    relief: Relief = "emboss"
    depth: float = Field(0.6, ge=0.3, le=1.5)
    side: Literal["single", "both"] = Field("single", description="both = also mirrored at -x")


class Ear(_DecorBase):
    type: Literal["ear"]
    style: Literal["cat", "bear", "bunny", "owl_tuft", "antenna"] = "cat"
    side: Literal["both", "left", "right"] = Field("both", description="both = at [x,z] and mirrored at [-x,z]")
    size: Size2
    thickness: Optional[float] = Field(None, ge=4, le=80, description="ear depth along Y; default 40% of body depth")
    tilt: float = Field(0, ge=-45, le=45, description="degrees, positive leans outward")
    roundness: Optional[float] = Field(None, ge=0, le=1, description="0 = pointy .. 1 = round tip/outline; None = the style default "
                                       "(cat 0.25, bear 0.6, bunny 0.5, owl_tuft 0.2); read by the CAD ear outline")


class Text(_DecorBase):
    type: Literal["text"]
    text: str = Field(..., min_length=1, max_length=16, pattern=r"^[A-Za-z0-9 .,!?'&+\-]+$")
    height: float = Field(8, ge=5, le=30, description="cap height mm (>= 5 to print cleanly)")
    relief: Relief = "engrave"
    depth: float = Field(0.6, ge=0.4, le=1.5)


Decor = Annotated[Union[FacePlate, Eye, Mouth, Blush, Ear, Text], Field(discriminator="type")]


# --------------------------------------------------------------------------------------------- features
class Grille(_M):
    pattern: Literal["hex_holes", "slots", "concentric", "dots_ellipse"] = "hex_holes"
    w: float = Field(..., ge=8, le=150)
    h: float = Field(..., ge=8, le=150)
    hole: float = Field(2.6, ge=1.5, le=6.0, description="hole or slot width mm (>= 1.5 prints clean)")
    pitch: float = Field(4.4, ge=2.5, le=12.0)

    @model_validator(mode="after")
    def _rib(self) -> "Grille":
        if self.pitch < self.hole + 1.0:
            raise ValueError(f"grille pitch {self.pitch:g} must be >= hole + 1.0 ({self.hole + 1.0:g}) to leave a 1 mm rib")
        return self


class Feature(_M):
    """A functional aperture bound to ONE library module instance."""

    id: FeatureId
    module: LibId
    face: Literal["front", "rear", "left", "right", "top"] = Field(
        "front", description="left = the gadget's own left (+X side), right = -X side"
    )
    at: FacePoint
    label: Optional[str] = Field(None, max_length=48)
    grille: Optional[Grille] = None
    rotate: Literal[0, 90, 180, 270] = 0


# --------------------------------------------------------------------------------------------- electronics
class Power(_M):
    input: Literal["usb_c_5v"] = "usb_c_5v"
    adapter_in_box: bool = False


DEFAULT_CORE = "CORE_ESP32S3_DEVKITC1_N8R8"


class Electronics(_M):
    core: LibId = Field(DEFAULT_CORE, description="core board library id (Espressif ESP32-S3-DevKitC-1 N8R8)")
    extra_modules: list[LibId] = Field(default_factory=list, max_length=6, description="internal modules without an aperture")
    power: Power = Field(default_factory=Power)


TemplateName = Literal["story_player", "desk_companion", "notifier_clock", "ambient_light", "sensor_display", "latch"]
PACK_TEMPLATES = {"latch": "motion2"}  # template -> pack (CATALOG-PLAN E4); the schema leaves it out while the pack is off


class Behaviour(_M):
    template: TemplateName
    summary: str = Field("", max_length=600)
    settings: dict[str, Any] = Field(default_factory=dict, description="template-specific; validated by the firmware package")


class Safety(_M):
    flags: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")]] = Field(
        default_factory=list,
        max_length=16,
        description="e.g. CHILD_DIRECTED, CHILD_DATA, LOCAL_VOICE_CAPTURE, CHILD_VOICE_STORED (the parent consented to storing the child's voice clips; required with capture_mode child_voice_clip), CLOUD_AI, NIGHT_USE, SMALL_PARTS",
    )
    notes: str = Field("", max_length=1200)


# --------------------------------------------------------------------------------------------- packs (CATALOG-PLAN E2)
# Each of these is accepted only while its pack is on (gstudio.packs); spec_json_schema() leaves them out otherwise.
class PrintedOpts(_M):
    """kind "printed" only: an object with no electronics (pack `printed`)."""

    opening: Literal["none", "top"] = Field("none", description="top = an open cavity from the top (cup, planter, holder)")
    drain_hole: bool = Field(False, description="a drain hole in the floor of an open-top planter")


class Cut(_M):
    """kind "printed" only (pack `printed_cuts`): a pocket, slot or hole cut into the body."""

    kind: Literal["pocket", "slot", "hole"]
    face: Literal["front", "rear", "left", "right", "top"] = "front"
    at: FacePoint
    size: Size2
    depth: float = Field(..., ge=0.6, le=200, description="mm into the body from the face")


class Mount(_M):
    """Where the object lives (pack `mount`; ceiling also needs pack `mount_ceiling`)."""

    kind: Literal["desk", "wall", "ceiling"] = "desk"
    screws: Literal[2, 3] = Field(2, description="wall: 2 keyhole slots; ceiling: 2 or 3 countersunk holes")


PACK_FIELDS = {"kind": "printed", "printed": "printed", "cuts": "printed_cuts", "mount": "mount"}  # spec field -> pack


# --------------------------------------------------------------------------------------------- derived (engine-only)
class _D(BaseModel):
    model_config = ConfigDict(extra="allow")


class DInstance(_D):
    ref: str
    module: str
    role: str
    name: str = ""
    source: Literal["core", "feature", "extra", "implied"]
    feature: Optional[str] = None
    slot: Optional[str] = None
    face: Optional[str] = None
    position: Optional[tuple[float, float, float]] = None  # approx world centre of the module (mm)
    qty: int = 1
    reason: str = ""
    required_by: Optional[str] = None  # ref of the instance whose library row `requires` this part (e.g. CARD1 <- SD1)


class DHarness(_D):
    id: str
    ref: str  # instance at the far end
    slot: Optional[str] = None
    kind: str = "jst_ph"  # jst_ph | speaker_lead | socket
    cable: str = ""  # e.g. "JST-PH 4p 150 mm"
    pins: int = 0
    length_mm: int = 0  # length class 100/150/200/300
    distance_mm: float = 0.0  # estimated routed distance
    module: Optional[str] = None  # library harness row, e.g. CABLE_JSTPH_4P_DUPONT1X4_150_X2143 (gstudio.electrical.select_cable)
    module_end: Optional[str] = None  # factory_prewired | jst_ph | jst_xh | dupont_2.54 | spade_2.8 | solder (the module's connector)
    module_end_family: Optional[str] = None  # the chosen row's module-end family (factory_prewired for pre-wired modules)
    sku: Optional[str] = None  # the chosen row's sku (a GS-H-... drawing number for factory-made harnesses)
    drawing: Optional[str] = None  # harness drawing id
    made_to_drawing: bool = False  # not a straight-through lead: the factory makes it to the drawing's position table
    crossed: bool = False  # the module's pin order differs from the port order (REVIEW-1 MAJ-4)
    match: str = "exact"  # exact | fallback (no library row with this module end: made to drawing from the base lead)
    module_end_order: list[str] = Field(default_factory=list)  # module_connector.pin_order


class DHardware(_D):
    id: str
    name: str
    qty: int
    use: str = ""
    module: Optional[str] = None
    catalog_row: Optional[str] = None
    group: Optional[str] = None  # CAD fastener group name (HeatSetInserts, CartridgeScrews, CoverScrews, ...)
    size: Optional[str] = None  # e.g. "M2.5"
    qty_basis: str = "planned"  # "planned" = pre-CAD estimate; the CAD stage's components.json qty is the source of truth
    length_mm: Optional[float] = None  # screw length under the head / insert length (planned; the CAD's components.json size wins)


class DCartridge(_D):
    backplane: str = "backplane-v1"
    board_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)  # world position of board lower-left (bottom face)
    rotation_deg: float = 0.0  # about Z; 0 = board +X is world +X, board +Y is world +Y (rear)
    floor_z: float = 0.0  # top of the inner floor (= wall for rear_cover, base plate top for base_plate)
    board_top_z: float = 0.0
    board_size: tuple[float, float] = (0.0, 0.0)
    keepout_box: tuple[float, float, float, float, float, float] = (0, 0, 0, 0, 0, 0)  # world xmin,xmax,ymin,ymax,zmin,zmax


class Derived(_D):
    engine_version: str = ""
    size_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)  # overall body bbox [x, y, z]
    bbox: tuple[float, float, float, float, float, float] = (0, 0, 0, 0, 0, 0)
    instances: list[DInstance] = Field(default_factory=list)
    harness: list[DHarness] = Field(default_factory=list)
    hardware: list[DHardware] = Field(default_factory=list)
    cartridge: Optional[DCartridge] = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------- spec
class Spec(_M):
    """gadget-spec/1: one buildable gadget. Only library modules may appear. Unknown keys are rejected."""

    schema_: Literal["gadget-spec/1"] = Field(SPEC_SCHEMA, alias="schema")
    name: str = Field(..., min_length=1, max_length=40)
    slug: Slug
    tagline: str = Field("", max_length=140)
    kind: Literal["gadget", "printed"] = Field(
        "gadget", description="gadget = electronics in a printed shell; printed = a printed object with no electronics "
        "(no features, extra_modules or behaviour; sizes from 20 mm per axis)")
    audience: Audience = Field(default_factory=Audience)
    region: Literal["IN"] = "IN"
    colors: Colors = Field(default_factory=Colors)
    body: Body
    decor: list[Decor] = Field(default_factory=list, max_length=24)
    features: list[Feature] = Field(default_factory=list, max_length=12)
    electronics: Electronics = Field(default_factory=Electronics)
    behaviour: Optional[Behaviour] = Field(None, description="required for kind gadget; leave out for kind printed")
    safety: Safety = Field(default_factory=Safety)
    printed: Optional[PrintedOpts] = Field(None, description="kind printed only")
    cuts: list[Cut] = Field(default_factory=list, max_length=8, description="kind printed only")
    mount: Optional[Mount] = None
    derived: SkipJsonSchema[Optional[Derived]] = Field(None, description="engine-filled; leave out")

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    @field_validator("body", mode="before")
    @classmethod
    def _drop_foreign_nulls(cls, v: Any) -> Any:
        """The §3 example lists every family's keys with nulls; drop null keys that belong to other families."""
        if isinstance(v, dict) and v.get("family") in _FAMILY_KEYS:
            fam = v["family"]
            foreign = set().union(*(k for f, k in _FAMILY_KEYS.items() if f != fam))
            v = {k: val for k, val in v.items() if not (k in foreign and val is None)}
            if fam != "revolve" and v.get("squash_y") in (1, 1.0):
                v.pop("squash_y", None)
        return v

    # convenience -----------------------------------------------------------------------------
    @property
    def schema_id(self) -> str:
        return self.schema_

    def to_json_dict(self, include_derived: bool = True) -> dict:
        d = self.model_dump(mode="json", by_alias=True, exclude_none=False)
        if not include_derived or d.get("derived") is None:
            d.pop("derived", None)
        # pack fields at their defaults are left out, so a gadget dumps exactly as before the packs existed
        if d.get("kind") == "gadget":
            d.pop("kind", None)
        elif d.get("kind") == "printed" and d.get("electronics") == Electronics().model_dump(mode="json"):
            d.pop("electronics", None)  # a printed object has no core: never list the default board as one of its parts
        for k, empty in (("printed", None), ("cuts", []), ("mount", None)):
            if d.get(k) == empty:
                d.pop(k, None)
        for pr in (d.get("body") or {}).get("primitives") or []:
            if isinstance(pr, dict):
                if pr.get("rot") in ([0, 0, 0], [0.0, 0.0, 0.0]):
                    pr.pop("rot", None)
                for k in ("tip", "sweep"):
                    if pr.get(k) is None:
                        pr.pop(k, None)
        return d

    def dumps(self, include_derived: bool = True) -> str:
        return json.dumps(self.to_json_dict(include_derived), indent=2, ensure_ascii=False)

    def feature(self, fid: str) -> Optional[Feature]:
        return next((f for f in self.features if f.id == fid), None)

    def module_ids(self) -> list[str]:
        if self.kind == "printed":
            return []  # no electronics at all
        ids = [self.electronics.core] + [f.module for f in self.features] + list(self.electronics.extra_modules)
        return ids


# --------------------------------------------------------------------------------------------- cross-field checks
def cross_check(spec: Spec) -> list[dict]:
    """Checks that span several fields (library-free). load_spec raises on these; normalize re-runs them."""
    errs: list[dict] = []
    ids = [f.id for f in spec.features]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append({"path": "features", "msg": f"duplicate feature id '{i}'", "fix": "give every feature a unique id"})
    wall = spec.body.wall
    for k, d in enumerate(spec.decor):
        if getattr(d, "relief", None) == "engrave" and d.depth > wall - 1.0 + 1e-9:
            errs.append({"path": f"decor[{k}].depth", "msg": f"{d.type} engrave depth {d.depth:g} leaves < 1.0 mm of the {wall:g} mm wall",
                         "fix": f"use depth <= {wall - 1.0:.1f} or a thicker wall"})
    for k, f in enumerate(spec.features):
        if f.face in ("front", "rear") and not (0 <= f.at[1] <= MAX_Z):
            errs.append({"path": f"features[{k}].at", "msg": f"z={f.at[1]:g} is outside 0..{MAX_Z:g}"})
    mods = list(spec.electronics.extra_modules)
    for m in sorted({m for m in mods if mods.count(m) > 1}):
        errs.append({"path": "electronics.extra_modules", "msg": f"'{m}' listed twice", "fix": "list each extra module once"})
    errs += pack_checks(spec)
    return errs


def _na(path: str, what: str, fix: str) -> dict:
    return {"path": path, "msg": f"{what} is not available yet", "fix": fix, "code": "not_available"}


def pack_checks(spec: Spec) -> list[dict]:
    """CATALOG-PLAN E2: kind, printed, cuts, split none and mount, each gated by its pack (gstudio.packs)."""
    from . import packs

    errs: list[dict] = []
    on = packs.enabled_set()
    if spec.kind == "printed":
        if "printed" not in on:
            errs.append(_na("kind", "a printed object with no electronics (kind 'printed')",
                            "design a gadget (kind 'gadget') with electronics, or say that printed-only objects are not available yet"))
        if spec.features:
            errs.append({"path": "features", "msg": "a printed object has no electronics, so no features",
                         "fix": "remove features (shape the object with the body, decor and printed options)"})
        if spec.electronics.extra_modules:
            errs.append({"path": "electronics.extra_modules", "msg": "a printed object has no electronics",
                         "fix": "remove extra_modules"})
        if spec.behaviour is not None:
            errs.append({"path": "behaviour", "msg": "a printed object has no behaviour (no electronics)", "fix": "remove behaviour"})
        if spec.electronics.power.adapter_in_box:
            errs.append({"path": "electronics.power.adapter_in_box", "msg": "a printed object has no power input",
                         "fix": "set adapter_in_box false"})
        if spec.printed is not None and spec.printed.drain_hole and spec.printed.opening != "top":
            errs.append({"path": "printed.drain_hole", "msg": "a drain hole needs an open top (printed.opening 'top')",
                         "fix": "set printed.opening 'top' or drain_hole false"})
    else:
        if spec.behaviour is None:
            errs.append({"path": "behaviour", "msg": "required field is missing"})
        if spec.printed is not None:
            errs.append({"path": "printed", "msg": "printed options are only for kind 'printed'", "fix": "remove printed"})
        if spec.body.split == "none":
            errs.append({"path": "body.split", "msg": "split 'none' (a one-piece print) is only for kind 'printed'; a gadget "
                         "opens with a rear_cover or base_plate to fit its electronics", "fix": "use split rear_cover or base_plate"})
    if spec.body.family == "blob" and "sculpt" not in on:
        prims = spec.body.primitives
        if len(prims) > BLOB_MAX:
            errs.append({"path": "body.primitives", "msg": f"list should have at most {BLOB_MAX} items after validation, not {len(prims)}"})
        for k, pr in enumerate(prims):
            if pr.kind in SCULPT_KINDS:
                errs.append(_na(f"body.primitives[{k}].kind", f"the {pr.kind} primitive",
                                "use rounded_box, sphere, ellipsoid, cylinder or capsule"))
            if any(pr.rot):
                errs.append(_na(f"body.primitives[{k}].rot", "turning a primitive (rot)", "leave rot out (no rotation)"))
    tmpl = spec.behaviour.template if spec.behaviour is not None else None
    if tmpl in PACK_TEMPLATES and PACK_TEMPLATES[tmpl] not in on:
        errs.append(_na("behaviour.template", f"the {tmpl} app", "use another template"))
    if spec.cuts:
        if "printed_cuts" not in on:
            errs.append(_na("cuts", "cutting pockets, slots or holes (cuts)", "remove cuts"))
        elif spec.kind != "printed":
            errs.append({"path": "cuts", "msg": "cuts are only for kind 'printed'", "fix": "remove cuts"})
    m = spec.mount
    if m is not None and m.kind != "desk":
        if "mount" not in on:
            errs.append(_na("mount.kind", f"{m.kind} mounting", "use mount kind 'desk' (or leave mount out)"))
        elif m.kind == "ceiling" and "mount_ceiling" not in on:
            errs.append(_na("mount.kind", "ceiling mounting", "use mount kind 'wall' or 'desk'"))
        else:
            if m.kind == "wall" and not (spec.body.family == "silhouette" and spec.body.split == "rear_cover"):
                errs.append({"path": "mount.kind", "msg": "a wall mount hangs by its back cover: it needs a silhouette body with "
                             "split rear_cover", "fix": "use body family silhouette with split rear_cover, or mount kind 'desk'"})
            if m.kind == "ceiling":
                if spec.body.split != "base_plate":
                    errs.append({"path": "body.split", "msg": "a ceiling mount fixes the base plate to the ceiling: it needs split "
                                 "base_plate", "fix": "use split base_plate"})
                if spec.audience.child_directed:
                    errs.append({"path": "mount.kind", "msg": "no ceiling-mounted child-directed designs",
                                 "fix": "use mount kind 'desk' or 'wall'", "code": "child_safety"})
            if spec.body.feet:
                errs.append({"path": "body.feet", "msg": f"a {m.kind} mount has no feet", "fix": "set body.feet 0"})
    return errs


# --------------------------------------------------------------------------------------------- loading
def load_spec(data: Any) -> Spec:
    """Validate a spec from a dict, a JSON string, a Path or a path string. Raises SpecError."""
    if isinstance(data, Spec):
        return data
    if isinstance(data, Path) or (isinstance(data, str) and not data.lstrip().startswith("{")):
        p = Path(data)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SpecError([{"path": "", "msg": f"spec file not found: {p}"}])
        except json.JSONDecodeError as e:
            raise SpecError([{"path": "", "msg": f"spec is not valid JSON: {e.msg} at line {e.lineno} col {e.colno}"}])
    elif isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise SpecError([{"path": "", "msg": f"spec is not valid JSON: {e.msg} at line {e.lineno} col {e.colno}"}])
    if not isinstance(data, dict):
        raise SpecError([{"path": "", "msg": "spec must be a JSON object"}])
    if data.get("schema") not in (None, SPEC_SCHEMA):
        raise SpecError([{"path": "schema", "msg": f"unsupported schema {data.get('schema')!r}", "fix": f'use "{SPEC_SCHEMA}"'}])
    kind = data.get("kind") if data.get("kind") in ("gadget", "printed") else "gadget"
    try:
        spec = Spec.model_validate(data, context={"kind": kind})
    except ValidationError as e:
        errs = pydantic_errors(e)
        if kind == "gadget" and data.get("behaviour") is None and not any(x["path"] == "behaviour" for x in errs):
            errs.append({"path": "behaviour", "msg": "required field is missing"})  # behaviour is Optional for kind printed only
        raise SpecError(errs) from None
    errs = cross_check(spec)
    if errs:
        raise SpecError(errs)
    return spec


def validate_spec(data: Any) -> tuple[Optional[Spec], list[dict]]:
    try:
        return load_spec(data), []
    except SpecError as e:
        return None, e.errors


# --------------------------------------------------------------------------------------------- error formatting
_TAGS = {"silhouette", "revolve", "blob", "face_plate", "eye", "mouth", "blush", "ear", "text"}


def _is_tag(loc: tuple, k: int) -> bool:
    """pydantic injects the discriminator tag right after a union field (body) or a list index (decor[i])."""
    part = loc[k]
    if part not in _TAGS or k == 0:
        return False
    prev = loc[k - 1]
    return isinstance(prev, int) or prev == "body"


def _loc_str(loc: tuple) -> str:
    out = ""
    for k, part in enumerate(loc):
        if isinstance(part, int):
            out += f"[{part}]"
        elif _is_tag(loc, k):
            continue
        elif part.startswith("function-") or part in ("tuple", "list"):
            continue
        else:
            out += ("." if out else "") + str(part)
    return out or "spec"


def _model_at(loc: tuple) -> Optional[type[BaseModel]]:
    """Walk the Spec model along an error loc and return the model class at that location."""
    import typing

    cur: Any = Spec
    for k, part in enumerate(loc):
        if isinstance(part, int):
            continue
        if _is_tag(loc, k):
            cur = _UNION_MEMBERS.get(part, cur)
            continue
        if not (isinstance(cur, type) and issubclass(cur, BaseModel)):
            return None
        fields = cur.model_fields
        name = "schema_" if part == "schema" else part
        if name not in fields:
            return cur
        ann = fields[name].annotation
        # unwrap Optional / list / Annotated to a model class if any
        for _ in range(6):
            origin = typing.get_origin(ann)
            if origin is None:
                break
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if not args:
                break
            ann = args[0]
        cur = ann
    return cur if isinstance(cur, type) and issubclass(cur, BaseModel) else None


_UNION_MEMBERS: dict[str, type[BaseModel]] = {
    "silhouette": SilhouetteBody,
    "revolve": RevolveBody,
    "blob": BlobBody,
    "face_plate": FacePlate,
    "eye": Eye,
    "mouth": Mouth,
    "blush": Blush,
    "ear": Ear,
    "text": Text,
}


def _allowed_keys(model: type[BaseModel]) -> list[str]:
    keys = [(f.alias or n) for n, f in model.model_fields.items() if n != "derived"]
    if model is Spec:  # never name a field whose pack is off (the agent would try it)
        from . import packs

        on = packs.enabled_set()
        keys = [k for k in keys if k not in PACK_FIELDS or PACK_FIELDS[k] in on]
    return keys


def pydantic_errors(e: ValidationError) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for err in e.errors(include_url=False):
        loc = tuple(err.get("loc", ()))
        typ = err.get("type", "")
        path = _loc_str(loc)
        inp = err.get("input")
        msg = err.get("msg", "")
        fix = None
        if typ == "extra_forbidden":
            parent = _model_at(loc[:-1])
            key = loc[-1]
            path = _loc_str(loc[:-1])
            msg = f"unknown key '{key}'"
            if parent is not None:
                fix = "allowed keys: " + ", ".join(_allowed_keys(parent))
        elif typ == "missing":
            msg = "required field is missing"
        elif typ == "union_tag_invalid":
            tag = err.get("ctx", {}).get("discriminator", "").strip("'")
            msg = f"unknown {tag or 'type'} {inp if not isinstance(inp, dict) else inp.get(tag)!r}"
            fix = "use one of: " + err.get("ctx", {}).get("expected_tags", "")
        elif typ == "union_tag_not_found":
            tag = err.get("ctx", {}).get("discriminator", "").strip("'")
            msg = f"missing '{tag}'"
        elif typ == "value_error":
            msg = msg.removeprefix("Value error, ")
        elif typ in ("greater_than_equal", "less_than_equal", "greater_than", "less_than", "too_short", "too_long",
                     "string_too_long", "string_too_short", "literal_error", "string_pattern_mismatch"):
            shown = json.dumps(inp)[:40] if not isinstance(inp, (dict, list)) else ""
            msg = (f"{shown}: " if shown else "") + msg[0].lower() + msg[1:]
        key = f"{path}|{msg}"
        if key in seen:
            continue
        seen.add(key)
        item = {"path": path, "msg": msg}
        if fix:
            item["fix"] = fix
        items.append(item)
    return items


# --------------------------------------------------------------------------------------------- schema for the LLM
def spec_json_schema(compact: bool = True, packs_on: Optional[Any] = None) -> dict:
    """JSON schema for the LLM prompt. compact=True strips titles and empty keys to save tokens. Fields of packs that
    are off (PACK_FIELDS, split 'none') are left out, so the agent never sees what it cannot use (CATALOG-PLAN E2)."""
    sch = _pack_schema(Spec.model_json_schema(by_alias=True), packs_on)
    if not compact:
        return sch

    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("title",)}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    return strip(sch)


def _pack_schema(sch: dict, packs_on: Optional[Any] = None) -> dict:
    """Remove the schema parts of packs that are off. With every pack off the result equals the pre-pack schema."""
    from . import packs

    on = set(packs.enabled_set() if packs_on is None else packs_on)
    props = sch.get("properties") or {}
    defs = sch.get("$defs") or {}
    for field, pack in PACK_FIELDS.items():
        if pack not in on:
            props.pop(field, None)
    if "printed" not in on:
        # behaviour stays required (and not nullable) while there is no printed kind
        props["behaviour"] = {"$ref": "#/$defs/Behaviour"}
        req = set(sch.get("required") or []) | {"behaviour"}
        sch["required"] = [k for k in props if k in req]
        for name in ("SilhouetteBody", "RevolveBody", "BlobBody"):
            sp = ((defs.get(name) or {}).get("properties") or {}).get("split") or {}
            if isinstance(sp.get("enum"), list):
                sp["enum"] = [v for v in sp["enum"] if v != "none"]
    if "sculpt" not in on:
        prim = (defs.get("Primitive") or {}).get("properties") or {}
        for k in ("rot", "tip", "sweep"):
            prim.pop(k, None)
        kind = prim.get("kind") or {}
        if isinstance(kind.get("enum"), list):
            kind["enum"] = [k for k in kind["enum"] if k not in SCULPT_KINDS]
        blob = ((defs.get("BlobBody") or {}).get("properties") or {}).get("primitives") or {}
        if "maxItems" in blob:
            blob["maxItems"] = BLOB_MAX
    beh = ((defs.get("Behaviour") or {}).get("properties") or {}).get("template") or {}
    if isinstance(beh.get("enum"), list):
        beh["enum"] = [t for t in beh["enum"] if t not in PACK_TEMPLATES or PACK_TEMPLATES[t] in on]
    used = json.dumps(props)
    for name in ("PrintedOpts", "Cut", "Mount"):
        if f'"#/$defs/{name}"' not in used:
            defs.pop(name, None)
    return sch


# --------------------------------------------------------------------------------------------- geometry helpers
def polygon_area(pts: list[tuple[float, float]]) -> float:
    """Signed area (shoelace). Positive = counter-clockwise."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, c) -> bool:
    return min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS


def segments_intersect(p1, p2, p3, p4) -> bool:
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    if abs(d1) <= EPS and _on_seg(p3, p4, p1):
        return True
    if abs(d2) <= EPS and _on_seg(p3, p4, p2):
        return True
    if abs(d3) <= EPS and _on_seg(p1, p2, p3):
        return True
    if abs(d4) <= EPS and _on_seg(p1, p2, p4):
        return True
    return False


def polygon_self_intersections(pts: list[tuple[float, float]], limit: int = 3) -> list[tuple[int, int]]:
    """Pairs of non-adjacent edges (i, j) of the closed polygon that intersect."""
    n = len(pts)
    hits: list[tuple[int, int]] = []
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            if segments_intersect(a1, a2, pts[j], pts[(j + 1) % n]):
                hits.append((i, j))
                if len(hits) >= limit:
                    return hits
    if not hits:
        try:  # cross-check with shapely when available (catches touching rings)
            from shapely.geometry import Polygon  # type: ignore

            if not Polygon(pts).is_valid:
                hits.append((0, 0))
        except Exception:
            pass
    return hits


def polygon_is_simple(pts: list[tuple[float, float]]) -> bool:
    return len(pts) >= 3 and not polygon_self_intersections(pts, limit=1)


def _check_no_duplicates(pts: list[tuple[float, float]]) -> None:
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if math.dist(a, b) < 1.0:
            raise ValueError(f"points {i} and {(i + 1) % n} are closer than 1 mm; remove one")


def _check_flat_bottom(pts: list[tuple[float, float]], name: str, min_width: float = MIN_BOTTOM_WIDTH) -> None:
    n = len(pts)
    on = [abs(z) <= 1e-3 for _, z in pts]
    if sum(on) < 2:
        raise ValueError(f"{name} needs a flat bottom: two consecutive points with z=0 (e.g. [-50,0],[50,0])")
    # z=0 points must form exactly one contiguous run (cyclic)
    runs = 0
    for i in range(n):
        if on[i] and not on[i - 1]:
            runs += 1
    if all(on):
        raise ValueError(f"{name} is flat; it needs height")
    if runs != 1:
        raise ValueError(f"{name} touches z=0 in more than one place; keep one flat bottom run")
    xs = [pts[i][0] for i in range(n) if on[i]]
    width = max(xs) - min(xs)
    if width < min_width:
        raise ValueError(f"{name} flat bottom is {width:.0f} mm wide; it must be >= {min_width:g} mm to stand")


def _aabb_overlap(a, b, tol: float = 0.0) -> bool:
    return (a[0] - tol <= b[1] and b[0] - tol <= a[1] and a[2] - tol <= b[3] and b[2] - tol <= a[3]
            and a[4] - tol <= b[5] and b[4] - tol <= a[5])


def catmull_rom_closed(pts: list[tuple[float, float]], samples_per_seg: int = 8, straight: Optional[set[int]] = None,
                       alpha: float = 0.5) -> list[tuple[float, float]]:
    """Sample a closed centripetal Catmull-Rom spline through pts. Edges i->i+1 listed in `straight`
    are kept straight (used for the flat bottom). Approximates the CAD spline for size/fit checks."""
    n = len(pts)
    straight = straight or set()
    out: list[tuple[float, float]] = []

    def tj(ti, pi, pj):
        d = math.dist(pi, pj)
        return ti + max(d, 1e-9) ** alpha

    for i in range(n):
        p1, p2 = pts[i], pts[(i + 1) % n]
        if i in straight:
            for s in range(samples_per_seg):
                t = s / samples_per_seg
                out.append((p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t))
            continue
        # neighbours: a straight neighbour edge acts as a tangent constraint (mirror point)
        p0 = pts[i - 1]
        p3 = pts[(i + 2) % n]
        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        for s in range(samples_per_seg):
            t = t1 + (t2 - t1) * s / samples_per_seg
            a1 = _lerp(p0, p1, (t1 - t) / (t1 - t0), (t - t0) / (t1 - t0))
            a2 = _lerp(p1, p2, (t2 - t) / (t2 - t1), (t - t1) / (t2 - t1))
            a3 = _lerp(p2, p3, (t3 - t) / (t3 - t2), (t - t2) / (t3 - t2))
            b1 = _lerp(a1, a2, (t2 - t) / (t2 - t0), (t - t0) / (t2 - t0))
            b2 = _lerp(a2, a3, (t3 - t) / (t3 - t1), (t - t1) / (t3 - t1))
            c = _lerp(b1, b2, (t2 - t) / (t2 - t1), (t - t1) / (t2 - t1))
            out.append(c)
    return out


def _lerp(a, b, wa, wb):
    return (a[0] * wa + b[0] * wb, a[1] * wa + b[1] * wb)


def bottom_edges(pts: list[tuple[float, float]]) -> set[int]:
    """Indices i of edges i->i+1 lying on z=0."""
    n = len(pts)
    return {i for i in range(n) if abs(pts[i][1]) <= 1e-3 and abs(pts[(i + 1) % n][1]) <= 1e-3}


def outline_points(body: SilhouetteBody, samples_per_seg: int = 8) -> list[tuple[float, float]]:
    """Densified front-view outline approximating the smooth spline (flat bottom kept straight)."""
    pts = list(body.outline)
    dense = catmull_rom_closed(pts, samples_per_seg, straight=bottom_edges(pts))
    return [(x, max(0.0, z)) for x, z in dense]


__all__ = [
    "Spec", "Audience", "Colors", "SilhouetteBody", "RevolveBody", "BlobBody", "Primitive", "Body",
    "FacePlate", "Eye", "Mouth", "Blush", "Ear", "Text", "Decor", "Grille", "Feature", "Power", "Electronics",
    "Behaviour", "Safety", "Derived", "DInstance", "DHarness", "DHardware", "DCartridge", "TemplateName",
    "load_spec", "validate_spec", "spec_json_schema", "pydantic_errors", "cross_check",
    "polygon_area", "polygon_is_simple", "polygon_self_intersections", "segments_intersect",
    "catmull_rom_closed", "outline_points", "bottom_edges",
]
