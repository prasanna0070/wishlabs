"""Load and normalise every input the manual stage reads.

Inputs (ARCHITECTURE §5, §6, §7): cad/components.json, cad/anchors.json, cad/cable-routes.json,
cad/components/*.brep, cad/assembly.glb, electrical.json, spec.json (with engine-filled `derived`),
library/backplane-v1.json, library/modules.json, an existing build.json (purchased/printed from the
pipeline), print/*.stl and pcb/board-top.png.

The other stages are written in parallel, so every reader here is tolerant: it accepts the documented
field names plus a few obvious variants, and never invents a value that is missing. Missing values stay
None and the text generators leave them out.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

STUDIO_ROOT = Path(__file__).resolve().parents[3]

KINDS = ("PRINT", "PURCHASE", "REFERENCE", "TEST")


# --------------------------------------------------------------------------------------------- helpers
def read_json(path: Path | str | None, default: Any = None) -> Any:
    if path is None:
        return default
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def as_dict(obj: Any) -> Any:
    """pydantic model / dataclass / mapping -> plain JSON-like data."""
    if obj is None:
        return None
    if hasattr(obj, "to_json_dict"):
        try:
            return obj.to_json_dict()
        except Exception:  # pragma: no cover - defensive
            pass
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json", by_alias=True)
        except TypeError:
            return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return obj


def first(d: dict | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def parse_color(c: Any, default: tuple[float, float, float] = (0.7, 0.7, 0.7)) -> tuple[float, float, float]:
    """'#RRGGBB' | [r,g,b] (0..1 or 0..255) | {r,g,b} | [r,g,b,a] -> (r,g,b) in 0..1."""
    try:
        if c is None:
            return default
        if isinstance(c, str):
            s = c.strip().lstrip("#")
            if len(s) == 3:
                s = "".join(ch * 2 for ch in s)
            if len(s) >= 6:
                return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
            return default
        if isinstance(c, dict):
            c = [first(c, "r", "R"), first(c, "g", "G"), first(c, "b", "B")]
        vals = [float(v) for v in list(c)[:3]]
        if len(vals) != 3:
            return default
        if max(vals) > 1.0:
            vals = [v / 255.0 for v in vals]
        return tuple(max(0.0, min(1.0, v)) for v in vals)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return default


def kebab(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", str(s))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def camel_words(name: str) -> str:
    """PRINT_SpeakerRing -> 'speaker ring'."""
    base = name.split("_", 1)[1] if "_" in name and name.split("_", 1)[0] in KINDS else name
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base).replace("_", " ")
    return words.strip().lower()


# --------------------------------------------------------------------------------------------- components
@dataclass
class Comp:
    name: str
    kind: str
    label: str
    ref: Optional[str] = None
    module_id: Optional[str] = None
    feature_id: Optional[str] = None
    color: tuple[float, float, float] = (0.7, 0.7, 0.7)
    bbox: Optional[tuple[float, float, float, float, float, float]] = None
    volume: Optional[float] = None
    solids: int = 1
    qty: Optional[int] = None
    raw: dict = field(default_factory=dict)

    @property
    def center(self) -> tuple[float, float, float]:
        if not self.bbox:
            return (0.0, 0.0, 0.0)
        b = self.bbox
        return ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)

    @property
    def size(self) -> tuple[float, float, float]:
        if not self.bbox:
            return (0.0, 0.0, 0.0)
        b = self.bbox
        return (b[1] - b[0], b[3] - b[2], b[5] - b[4])

    @property
    def diag(self) -> float:
        return math.sqrt(sum(s * s for s in self.size))


def _kind_of(name: str, raw: dict) -> str:
    k = str(first(raw, "kind", "role_prefix", default="")).upper()
    if k in KINDS:
        return k
    pre = name.split("_", 1)[0].upper()
    return pre if pre in KINDS else "REFERENCE"


def _bbox(raw: dict) -> Optional[tuple]:
    b = first(raw, "bbox", "bbox_mm", "bounds")
    try:
        if isinstance(b, dict):
            b = [b["xmin"], b["xmax"], b["ymin"], b["ymax"], b["zmin"], b["zmax"]]
        if isinstance(b, (list, tuple)) and len(b) == 2 and isinstance(b[0], (list, tuple)):
            (x0, y0, z0), (x1, y1, z1) = b
            b = [x0, x1, y0, y1, z0, z1]
        if isinstance(b, (list, tuple)) and len(b) == 6:
            return tuple(float(v) for v in b)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def load_components(path: Path) -> dict[str, Comp]:
    raw = read_json(path, {}) or {}
    if isinstance(raw, dict) and "components" in raw and isinstance(raw["components"], (dict, list)):
        raw = raw["components"]
    if isinstance(raw, list):
        raw = {str(first(r, "name", "id")): r for r in raw if isinstance(r, dict)}
    comps: dict[str, Comp] = {}
    for name, r in raw.items():
        if not isinstance(r, dict):
            continue
        qty = first(r, "qty", "quantity", "count")
        comps[name] = Comp(
            name=name,
            kind=_kind_of(name, r),
            label=str(first(r, "label", "title", default=camel_words(name))),
            ref=first(r, "ref"),
            module_id=first(r, "module_id", "module"),
            feature_id=first(r, "feature_id", "feature"),
            color=parse_color(first(r, "color", "colour")),
            bbox=_bbox(r),
            volume=first(r, "volume_mm3", "volumeMm3", "volume"),
            solids=int(first(r, "solids", default=1) or 1),
            qty=int(qty) if isinstance(qty, (int, float)) else None,
            raw=r,
        )
    return comps


def load_anchors(path: Path) -> dict[str, tuple[float, float, float]]:
    raw = read_json(path, {}) or {}
    if isinstance(raw, dict) and "anchors" in raw and isinstance(raw["anchors"], (dict, list)):
        raw = raw["anchors"]
    out: dict[str, tuple[float, float, float]] = {}
    items: Iterable = raw.items() if isinstance(raw, dict) else ((first(a, "name", "id"), a) for a in raw)
    for k, v in items:
        try:
            if isinstance(v, dict):
                v = first(v, "xyz", "pos", "position", "at", "p")
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                out[str(k)] = (float(v[0]), float(v[1]), float(v[2]))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class Route:
    id: str
    frm: Optional[str]
    to: Optional[str]
    cable: Optional[str]
    diameter: Optional[float]
    length_mm: Optional[float]
    points: list[tuple[float, float, float]]
    component: Optional[str] = None  # REFERENCE_* component that draws this route, if any
    raw: dict = field(default_factory=dict)


def load_routes(path: Path) -> list[Route]:
    raw = read_json(path, []) or []
    if isinstance(raw, dict):
        raw = raw.get("routes") or list(raw.values())
    routes: list[Route] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            continue
        pts = []
        for p in first(r, "points", "path", default=[]) or []:
            try:
                pts.append((float(p[0]), float(p[1]), float(p[2])))
            except (TypeError, ValueError, IndexError):
                pass
        routes.append(
            Route(
                id=str(first(r, "id", "name", default=f"route{i + 1}")),
                frm=first(r, "from"),
                to=first(r, "to"),
                cable=first(r, "cable", "via"),
                diameter=first(r, "diameter", "diameter_mm", "diameterEnvelopeMm"),
                length_mm=first(r, "length_mm", "pathLengthMm", "path_length_mm"),
                points=pts,
                component=first(r, "component", "name_component"),
                raw=r,
            )
        )
    return routes


# --------------------------------------------------------------------------------------------- backplane
@dataclass
class Pin:
    n: Any  # 1, 2, ... or a string id such as "A1" / "J1-3"
    name: str
    net: Optional[str] = None
    gpio: Optional[int] = None
    at: Optional[tuple[float, float]] = None  # pad centre in board coordinates, when the slot map gives it


@dataclass
class Slot:
    id: str
    kind: str
    footprint: Optional[str]
    pos: Optional[tuple[float, float]]
    size: Optional[tuple[float, float]]
    rot: float
    edge: Optional[str]
    pins: list[Pin]
    accepts: list[str]
    rect: Optional[tuple[float, float, float, float]] = None  # x0, x1, y0, y1 (courtyard / module plan)
    label: Optional[str] = None
    notes: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Backplane:
    id: str
    size: Optional[tuple[float, float]]
    outline: Optional[list[tuple[float, float]]]
    holes: list[tuple[float, float, float]]
    slots: dict[str, Slot]
    raw: dict = field(default_factory=dict)
    source: str = ""


def _xy(v: Any) -> Optional[tuple[float, float]]:
    try:
        if isinstance(v, dict):
            v = [first(v, "x"), first(v, "y")]
        if isinstance(v, (list, tuple)) and len(v) >= 2 and v[0] is not None:
            return (float(v[0]), float(v[1]))
    except (TypeError, ValueError):
        return None
    return None


def _slot_kind(sid: str, raw: dict) -> str:
    k = str(first(raw, "kind", "type", default="")).lower()
    if k in ("core", "module_socket"):
        return "socket"
    if k == "port":
        return "jst_ph"
    if k == "power_in":
        return "power"
    fp = str(first(raw, "footprint", default="")).lower()
    if "usb" in k or "usb" in fp or sid.upper() == "J_PWR":
        return "power"
    if "socket" in k or "header" in k or sid.upper() in ("U1", "A1", "S1"):
        return "socket"
    if "jst" in k or "jst" in fp or sid.upper().startswith("J_"):
        return "jst_ph"
    if "pad" in k or sid.upper().startswith("TP"):
        return "testpad"
    return k or "connector"


def parse_backplane(raw: Any, source: str = "") -> Optional[Backplane]:
    raw = as_dict(raw)
    if not isinstance(raw, dict):
        return None
    board = first(raw, "board", default={}) or {}
    size = _xy(first(board, "size_mm", "size", "dims_mm") or first(raw, "size_mm", "board_size_mm", "size"))
    outline = first(board, "outline", "outline_mm") or first(raw, "outline")
    poly = None
    if isinstance(outline, list) and outline and isinstance(outline[0], (list, tuple, dict)):
        poly = [p for p in (_xy(q) for q in outline) if p]
    elif isinstance(outline, list) and len(outline) == 2 and size is None:
        size = _xy(outline)
    if size is None and poly:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        size = (max(xs) - min(xs), max(ys) - min(ys))
    holes = []
    for h in first(board, "holes", "mounting_holes") or first(raw, "holes", "mounting_holes") or []:
        p = _xy(first(h, "at", "pos") if isinstance(h, dict) and first(h, "at", "pos") is not None else h)
        if p:
            d = first(h, "d", "dia", "diameter", "drill", default=2.7) if isinstance(h, dict) else 2.7
            holes.append((p[0], p[1], float(d)))
    slots_raw = first(raw, "slots", "ports", "connectors", default=[])
    if isinstance(slots_raw, dict):
        slots_raw = [dict(v, id=v.get("id", k)) for k, v in slots_raw.items() if isinstance(v, dict)]
    slots: dict[str, Slot] = {}
    for s in slots_raw or []:
        if not isinstance(s, dict):
            continue
        sid = str(first(s, "id", "ref", "name", default=""))
        if not sid:
            continue
        pins = []
        for j, p in enumerate(first(s, "pins", "pinout", default=[]) or []):
            if isinstance(p, dict):
                n = first(p, "n", "pin", "num", "number", default=j + 1)
                n = int(n) if isinstance(n, (int, float)) or (isinstance(n, str) and n.isdigit()) else str(n)
                g = first(p, "gpio", "esp32_gpio")
                try:
                    g = int(g) if g is not None and str(g).lstrip("-").isdigit() else None
                except (TypeError, ValueError):
                    g = None
                pins.append(Pin(n=n, name=str(first(p, "name", "signal", "devkit_label", "net", default=f"P{n}")),
                                net=first(p, "net"), gpio=g, at=_xy(first(p, "at"))))
            elif isinstance(p, str):
                pins.append(Pin(n=j + 1, name=p, net=p))
        pos = _xy(first(s, "pos", "position", "at", "xy", "center", "centre", "origin", "module_frame_origin", "devkit_origin"))
        acc = first(s, "accepts_roles", "accepts", "roles", "module_roles", "allowed_roles", default=[]) or []
        rect = None
        for key in ("courtyard", "module_plan", "devkit_pcb_plan", "rect"):
            r = s.get(key)
            if isinstance(r, (list, tuple)) and len(r) == 4:
                try:
                    rect = tuple(float(v) for v in r)
                    break
                except (TypeError, ValueError):
                    pass
        if rect and not pos:
            pos = ((rect[0] + rect[1]) / 2, (rect[2] + rect[3]) / 2)
        slots[sid] = Slot(
            id=sid,
            kind=_slot_kind(sid, s),
            footprint=first(s, "footprint", "part"),
            pos=pos,
            size=_xy(first(s, "size", "size_mm", "envelope_mm", "body_mm")),
            rot=float(first(s, "rot", "rotation", "rotation_deg", default=0) or 0),
            edge=first(s, "edge", "facing_edge") or (s.get("facing") if s.get("facing") in ("front", "rear", "left", "right") else None),
            pins=pins,
            accepts=[str(a) for a in (acc if isinstance(acc, list) else [acc])],
            rect=rect,
            label=first(s, "label", "title", "description"),
            notes=first(s, "notes", "note"),
            raw=s,
        )
    return Backplane(id=str(first(raw, "id", "name", default="backplane-v1")), size=size, outline=poly,
                     holes=holes, slots=slots, raw=raw, source=source)


def load_backplane(ctx_lib: Any, electrical: dict | None) -> Optional[Backplane]:
    for attr in ("backplane", "get_backplane"):
        obj = getattr(ctx_lib, attr, None) if ctx_lib is not None else None
        try:
            val = obj() if callable(obj) else obj
        except Exception:
            val = None
        if val:
            bp = parse_backplane(val, source="library/backplane-v1.json")
            if bp and bp.slots:
                return bp
    bp_id = (electrical or {}).get("backplane") or "backplane-v1"
    for cand in (STUDIO_ROOT / "library" / f"{bp_id}.json", STUDIO_ROOT / "library" / "backplane-v1.json"):
        raw = read_json(cand)
        if raw:
            return parse_backplane(raw, source=f"library/{cand.name}")
    return None


# --------------------------------------------------------------------------------------------- library
class LibView:
    """Read-only view over the module library: lib.get(id) when available, else library/modules*.json."""

    def __init__(self, lib: Any = None, extra_paths: Iterable[Path] = ()):
        self.lib = lib
        self._file: dict[str, dict] | None = None
        self._extra = list(extra_paths)

    def __getstate__(self) -> dict:
        # the live Library object stays in the parent; worker processes only need resolved data
        st = dict(self.__dict__)
        st["lib"] = None
        return st

    def _load_files(self) -> dict[str, dict]:
        if self._file is None:
            self._file = {}
            paths = list(self._extra) + [STUDIO_ROOT / "library" / n for n in ("modules.json", "modules-a.json", "modules-b.json")]
            for p in paths:
                data = read_json(p, [])
                if isinstance(data, dict):
                    data = data.get("modules") or list(data.values())
                for m in data or []:
                    if isinstance(m, dict) and m.get("id") and m["id"] not in self._file:
                        self._file[m["id"]] = m
        return self._file

    def get(self, mid: Optional[str]) -> dict:
        if not mid:
            return {}
        if self.lib is not None and hasattr(self.lib, "get"):
            try:
                m = self.lib.get(mid)
                d = as_dict(m)
                if isinstance(d, dict) and d:
                    return d
            except Exception:
                pass
        return self._load_files().get(mid, {})

    def name(self, mid: Optional[str], fallback: str = "") -> str:
        m = self.get(mid)
        return str(first(m, "name", "title", "label", default=fallback or (mid or "")))


# --------------------------------------------------------------------------------------------- the bundle
@dataclass
class ManualData:
    out: Path
    spec: dict
    electrical: dict
    comps: dict[str, Comp]
    anchors: dict[str, tuple[float, float, float]]
    routes: list[Route]
    backplane: Optional[Backplane]
    lib: LibView
    build_existing: dict
    brep_dir: Path
    glb: Optional[Path]
    print_files: list[Path]
    pcb_top_png: Optional[Path]
    firmware_dir: Path
    subcomps: dict = field(default_factory=dict)  # "BASE#i,j" -> Comp (a subset of a multi-solid component)
    solid_boxes: dict = field(default_factory=dict)  # comp name -> [bbox per solid]

    def comp(self, name: str) -> Optional["Comp"]:
        return self.comps.get(name) or self.subcomps.get(name)

    @staticmethod
    def base_name(name: str) -> str:
        return name.split("#", 1)[0]

    # ---- derived lookups
    @property
    def derived(self) -> dict:
        return self.spec.get("derived") or {}

    @property
    def instances(self) -> list[dict]:
        """Electrical instances merged with spec.derived.instances (role, name, face, slot)."""
        by_ref: dict[str, dict] = {}
        for inst in self.derived.get("instances") or []:
            if isinstance(inst, dict) and inst.get("ref"):
                by_ref[inst["ref"]] = dict(inst)
        for inst in self.electrical.get("instances") or []:
            if isinstance(inst, dict) and inst.get("ref"):
                merged = by_ref.get(inst["ref"], {})
                merged.update({k: v for k, v in inst.items() if v is not None})
                by_ref[inst["ref"]] = merged
        core = self.electrical.get("core") or {}
        if core.get("ref") and core["ref"] not in by_ref:
            by_ref[core["ref"]] = {"ref": core["ref"], "module": core.get("module"), "role": "core", "slot": "U1"}
        return list(by_ref.values())

    def instance(self, ref: Optional[str]) -> dict:
        for i in self.instances:
            if i.get("ref") == ref:
                return i
        return {}

    def comp_for_ref(self, ref: Optional[str]) -> Optional[Comp]:
        """The module component for an electrical ref (prefers the one with a library module_id)."""
        if not ref:
            return None
        hits = [c for c in self.comps.values() if c.ref == ref and c.kind == "PURCHASE"] or \
            [c for c in self.comps.values() if c.ref == ref]
        hits.sort(key=lambda c: (not c.module_id, c.kind != "PURCHASE"))
        return hits[0] if hits else None

    def module_name(self, ref_or_comp: Any) -> str:
        """Human name of a purchased module: library name > instance name > component label."""
        c = ref_or_comp if isinstance(ref_or_comp, Comp) else self.comp_for_ref(ref_or_comp)
        if c is not None and c.ref and not c.module_id and c.label and isinstance(ref_or_comp, Comp):
            return c.label  # an accessory that shares its module's ref (e.g. a gasket): its own CAD label
        ref = c.ref if c else ref_or_comp
        inst = self.instance(ref) if ref else {}
        mid = (c.module_id if c else None) or inst.get("module")
        nm = self.lib.name(mid) if mid else ""
        if nm and nm != mid:
            return nm
        if inst.get("name"):
            return str(inst["name"])
        if c:
            return c.label
        return str(ref)

    def purchased(self) -> list[dict]:
        return list(self.build_existing.get("purchased") or [])

    def printed(self) -> list[dict]:
        return list(self.build_existing.get("printed") or [])

    def hardware(self) -> list[dict]:
        return [h for h in (self.derived.get("hardware") or []) if isinstance(h, dict)]

    def harness(self) -> list[dict]:
        return [h for h in (self.derived.get("harness") or []) if isinstance(h, dict)]

    @property
    def cartridge(self) -> dict:
        return self.derived.get("cartridge") or {}

    @property
    def split(self) -> str:
        return str((self.spec.get("body") or {}).get("split") or "rear_cover")

    @property
    def product_name(self) -> str:
        return str(self.spec.get("name") or "Gadget")

    def feature(self, fid: Optional[str]) -> dict:
        for f in self.spec.get("features") or []:
            if f.get("id") == fid:
                return f
        return {}


def solid_bboxes(brep: Path) -> list[tuple]:
    """Bounding box of every solid in a BREP file (OCP), used to split fastener groups among their owners."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Builder
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, str(brep), BRep_Builder()):
        return []
    out = []
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        b = Bnd_Box()
        BRepBndLib.Add_s(ex.Current(), b)
        x0, y0, z0, x1, y1, z1 = b.Get()
        out.append((x0, x1, y0, y1, z0, z1))
        ex.Next()
    return out


def load(ctx: Any) -> ManualData:
    out = Path(ctx.out)
    spec = as_dict(getattr(ctx, "spec", None)) or read_json(out / "spec.json", {}) or {}
    electrical = getattr(ctx, "electrical", None)
    if not (isinstance(electrical, dict) and isinstance(electrical.get("instances"), list)):
        electrical = read_json(out / "electrical.json", {}) or {}
    artifacts = getattr(ctx, "artifacts", {}) or {}
    cad = out / "cad"
    comps_path = Path(artifacts.get("cad.components") or cad / "components.json")
    anchors_path = Path(artifacts.get("cad.anchors") or cad / "anchors.json")
    routes_path = Path(artifacts.get("cad.cable_routes") or artifacts.get("cad.routes") or cad / "cable-routes.json")
    comps = load_components(comps_path)
    lib = LibView(getattr(ctx, "lib", None))
    pdir = out / "print"
    prints = sorted(pdir.glob("*.stl")) if pdir.is_dir() else []
    glb = cad / "assembly.glb"
    pcb_png = out / "pcb" / "board-top.png"
    return ManualData(
        out=out,
        spec=spec,
        electrical=electrical,
        comps=comps,
        anchors=load_anchors(anchors_path),
        routes=load_routes(routes_path),
        backplane=load_backplane(getattr(ctx, "lib", None), electrical),
        lib=lib,
        build_existing=read_json(out / "build.json", {}) or {},
        brep_dir=comps_path.parent / "components",
        glb=glb if glb.is_file() else None,
        print_files=prints,
        pcb_top_png=pcb_png if pcb_png.is_file() else None,
        firmware_dir=out / "firmware",
    )
