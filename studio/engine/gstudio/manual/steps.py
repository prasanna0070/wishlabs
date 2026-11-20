"""Assembly steps: generated from the classified components, never from a hardcoded list.

Each Step carries the build.json fields (id, title, instruction, expected, parts, highlight, camera) and
the FigureSpecs for its illustrations. Text follows the house style: imperative, exact names and
quantities, orientation as an axis plus a gadget-relative word, an expected result and a stop rule.
Numbers only appear when they come from saved data (CAD, library, electrical, slicer, firmware source).

Order (MAJ-13): check the parts, prepare the story card, assemble the core cartridge and bench-test every module
on the table, then print the shell, fit inserts and modules, plug, route, re-check, close and do the final checks.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .cables import FAMILY_WORDS, ModuleEnd, lookalike_ports, module_ends
from .data import Comp, camel_words, first, read_json
from .figs import (FRONT_34, REAR_34, TOP_34, BELOW_34, FigureSpec, LabelSpec, MeshSpec, PanelSpec,
                   camera_hint)
from .geom import REAR_NOTE, rear_view, vdot
from .issues import Issues, load_issues
from .model import (ROLE_NOUN, Assembly, Group, Vec, anchors_for, axis_phrase, axis_short, bbox_center,
                    bbox_size, comp_qty, connector_dir, dominant_axis, overhang_dir, vadd, vnorm, vscale)
from .text import axis_words, dedupe, parent_name, tools_line, wrap
from . import words as W

FOLD_MM = 40  # suggested Z-fold length for spare cable (guidance, stated as such in the text)
FOOTER = "Rendered from the saved CAD | +Z up | purchased parts are simplified envelopes | not a physical test"
FACE_PART = W.RoleTable({"speaker": "cone", "display": "screen", "button": "cap", "led": "LEDs", "mic": "sound port",
                         "sensor": "sensor face", "touch": "pad", "buzzer": "sound opening"}, "face_part")
SELFTEST_CHECK = W.RoleTable({
    "speaker": "the speaker plays the test sound clearly, without crackle",
    "display": "the display shows the test image the right way up",
    "button": "each press of the button is reported once",
    "led": "the light shows each test colour",
    "mic": "the microphone level reacts when you speak near it",
    "sd": "the microSD check reports pass with the card inserted",
}, "selftest")


@dataclass
class Step:
    id: str
    title: str
    use: str
    tools: list[str]
    body: str
    expected: str
    stop: str
    parts: list[str]
    highlight: list[str]
    figures: list[FigureSpec] = field(default_factory=list)
    camera: Optional[dict] = None
    adds: list[str] = field(default_factory=list)
    extra_images: list[str] = field(default_factory=list)  # shared images (wiring.png, ports.png...)
    tools_note: str = ""
    rows: list[str] = field(default_factory=list)  # purchased rows (module ids) used here that have no CAD part
    known: list[str] = field(default_factory=list)  # known design issues (BLK-7)
    extra_stops: list[str] = field(default_factory=list)
    module_end: list[str] = field(default_factory=list)  # refs whose module-end cable instruction this step gives

    @property
    def tools_text(self) -> str:
        return tools_line(self.tools, self.tools_note)

    @property
    def stop_text(self) -> str:
        bits = [self.stop] + [s for s in self.extra_stops if s]
        return "; or ".join(b.rstrip(".") for b in bits if b) + "." if any(bits) else ""

    @property
    def known_text(self) -> str:
        return " ".join(f"**Known design issue:** {k}." for k in self.known)

    @property
    def instruction(self) -> str:
        parts = [f"**Use:** {self.use}", f"**Tools:** {self.tools_text}", self.known_text, self.body.strip()]
        if self.stop_text:
            parts.append(f"**Stop if:** {self.stop_text}")
        return "\n\n".join(p for p in parts if p)


# --------------------------------------------------------------------------------------------- naming
SIZE_RE = re.compile(r"M(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", re.I)


def fastener_size(c: Comp) -> Optional[tuple[str, str]]:
    """('M2.5', '8') from the CAD record (`size` "M2.5x8"), else from the label."""
    for src in (c.raw.get("size"), *(i.get("size") for i in (c.raw.get("items") or []) if isinstance(i, dict)), c.label):
        m = SIZE_RE.search(str(src or ""))
        if m:
            return f"M{m.group(1)}", m.group(2).rstrip("0").rstrip(".") if "." in m.group(2) else m.group(2)
    return None


def is_fastener(c: Comp) -> bool:
    return bool(re.search(r"screw|insert|bolt|nut|standoff", c.name, re.I))


class Namer:
    def __init__(self, A: Assembly):
        self.A, self.d = A, A.data
        self.nuts = any(re.search(r"\bnut", c.name, re.I) for c in A.purchases)

    def fastener_name(self, c: Comp) -> Optional[str]:
        sz = fastener_size(c)
        if not sz or not is_fastener(c):
            return None
        d, L = sz
        lab = (c.label or "").lower()
        if "insert" in c.name.lower():
            return f"{d} × {L} mm heat-set inserts"
        use = next((w for w in ("cartridge", "cover", "clamp", "ring", "plate") if w in lab or w in c.name.lower()), None)
        use = {"clamp": "module clamp", "ring": "module clamp"}.get(use, use)
        return f"{d} × {L} mm socket screws" + (f" ({use})" if use else "")

    def name(self, c: Comp) -> str:
        if c.kind == "PRINT" or c.kind == "TEST":
            return f"`{c.name}`"
        fn = self.fastener_name(c)
        if fn:
            return fn
        if self.d.is_accessory(c):
            return re.sub(r"\s*[x×]\s?\d+\s*$", "", c.label or "").strip() or camel_words(c.name)
        if "#" in c.name or (not c.ref and c.module_id and re.search(r"screw|insert|feet|foot|tie|nut|gasket|bolt", c.name, re.I)):
            lab = re.sub(r"\s*[x×]\s?\d+\s*$", "", c.label or "").strip()
            if lab:
                return lab
        for p in self.d.purchased():
            if p.get("modelPart") == c.name and p.get("name") and p.get("kind") != "hardware":
                return parent_name(str(p["name"]), nuts_used=self.nuts)
        if c.ref or c.module_id:
            return parent_name(self.d.module_name(c), nuts_used=self.nuts)
        return c.label if c.label else camel_words(c.name)

    def human(self, c: Comp) -> str:
        """Picture label: a person's words, never an internal PRINT_ id (MIN-31)."""
        if c.kind in ("PRINT", "TEST"):
            lab = c.label or camel_words(c.name)
            return lab[0].upper() + lab[1:]
        return self.name(c)

    def row_name(self, p: dict) -> str:
        return parent_name(str(p.get("name") or p.get("module_id") or ""), nuts_used=self.nuts)

    def qty(self, c: Comp) -> int:
        return comp_qty(self.d, c)[0]

    def use(self, comps: list[Comp], extra: list[str] = (), qty: Optional[dict] = None) -> str:
        items = []
        seen = set()
        for c in comps:
            if c is None or c.name in seen:
                continue
            seen.add(c.name)
            n = self.name(c)
            q = (qty or {}).get(c.name, self.qty(c))
            items.append(f"{q} × {n}" if c.kind in ("PRINT", "TEST") else f"{q} × **{n}**")
        items.extend(extra)
        return join_and(items) + "." if items else "no parts."

    def noun(self, g: Group) -> str:
        return ROLE_NOUN.get(g.role, g.role)

    def short(self, c: Comp) -> str:
        return self.human(c)


def slot_desc(slot) -> Optional[str]:
    """Human description of a backplane slot: its title, else a non-KiCad footprint string."""
    if slot is None:
        return None
    if slot.label:
        return parent_name(axis_words(str(slot.label)))
    fp = str(slot.footprint or "")
    return fp if fp and ":" not in fp else None


def slot_note(slot) -> Optional[str]:
    """Orientation notes the backplane slot data states for a socket, in the guide's axis words (MIN-32/34)."""
    if slot is None:
        return None
    bits = []
    for key in ("facing", "card_slot_side", "orientation"):
        v = slot.raw.get(key) if isinstance(slot.raw, dict) else None
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("up", "down"):
            bits.append(axis_words(v.strip().rstrip(".")))
    out = "; ".join(b for b in bits if b)
    out = re.sub(r",\s*\d+(\.\d+)? mm past the board edge", " over the board edge", out)
    return out or None


def plural(word: str) -> bool:
    return word.endswith("s") and not word.endswith("ss")


def join_and(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


MOUNT_TYPES = {"panel_snap": "snap_out", "panel": "panel_nut", "holes": "screws", "screws": "screws", "adhesive": "adhesive",
               "clip": "holder", "cradle": "holder", "press_fit": "press", "header": "socket", "none": "holder"}


def mount_kind(module: dict) -> str:
    mt = W.block(module).get("mount_type")  # a pack row's own words first (words.py)
    if mt:
        key = mt.lower().replace("-", " ").strip()
        if key in ("cable exit", "cable_exit", "lead exit", "outside"):
            return "cable_exit"
        if key.replace(" ", "_") in MOUNT_TYPES:
            return MOUNT_TYPES[key.replace(" ", "_")]
        if key.replace(" ", "_") in set(MOUNT_TYPES.values()) | {"snap", "clamp"}:
            return key.replace(" ", "_")
        module = {**module, "mount": mt}
    m = module.get("mount") if isinstance(module, dict) else None
    if isinstance(m, dict) and str(m.get("type", "")).lower() in MOUNT_TYPES:
        return MOUNT_TYPES[str(m["type"]).lower()]
    s = (m if isinstance(m, str) else " ".join(str(v) for v in (m or {}).values()) if isinstance(m, dict) else "").lower()
    if not s:
        return "holder"
    if "panel" in s or "nut" in s or "outside" in s or "bezel" in s:
        return "panel_nut"
    if "snap" in s or "clip" in s:
        return "snap"
    if "screw" in s or "boss" in s:
        return "screws"
    if "adhesive" in s or "tape" in s or "glue" in s:
        return "adhesive"
    if "press" in s or "friction" in s:
        return "press"
    return "holder"


def retention_kind(mk: str, g: Group) -> str:
    """The verb comes from the parts actually in the group (MAJ-15): a clip or screws means it is clamped, never
    'peel the backing', whatever the library mount type says."""
    clip = [c for c in g.prints if re.search(r"clip|clamp|strap|ring|retain|bracket|holder", f"{c.name} {c.label}", re.I)]
    if mk in ("snap_out", "panel_nut", "cable_exit"):
        return mk
    if clip and g.fasteners:
        return "clamp_screws"
    if clip:
        return "clamp"
    if g.fasteners:
        return "screws"
    return mk


def lc(s: str) -> str:
    """Lower-case the first letter only (keeps acronyms such as PETG)."""
    return s[:1].lower() + s[1:] if s else s


def ring_text(rd: Optional[dict]) -> str:
    if not rd:
        return ""
    return f"Ø{rd['od_mm']:g} mm outside, Ø{rd['id_mm']:g} mm inside"


# --------------------------------------------------------------------------------------------- planner
class Planner:
    def __init__(self, A: Assembly, issues: Optional[Issues] = None):
        self.A, self.d, self.n = A, A.data, Namer(A)
        self.installed: list[str] = []
        self.steps: list[Step] = []
        self.bboxes = {c.name: c.bbox for c in [*self.d.comps.values(), *self.d.subcomps.values()] if c.bbox}
        self.shell_size = bbox_size(A.shell.bbox) if A.shell and A.shell.bbox else (120.0, 100.0, 140.0)
        self.scale = max(self.shell_size)
        self.ends: dict[str, ModuleEnd] = module_ends(self.d)
        self.issues = issues if issues is not None else load_issues(self.d)
        self.rings = dict(getattr(self.d, "rings", {}) or {})
        self.selftest = self.d.selftest()
        self.ids: dict[str, int] = {}
        # parts that appear in many steps (shell, closing part, shared screw/insert/feet groups): a known issue is shown
        # on the step that fits the other, specific part of the pair (BLK-7)
        self.generic = {c.name for c in [*A.body, *A.covers, *A.inserts, *A.feet, *A.cartridge_fasteners, *A.cover_fasteners]}
        self.generic |= {c.name.split("#", 1)[0] for g in [*A.features, *A.internals] for c in g.fasteners}

    # ------------------------------------------------------------------ helpers
    @property
    def num(self) -> int:
        return len(self.steps) + 1

    def fig_name(self, slug: str) -> str:
        return f"manual/step-{self.num:02d}-{slug}.png"

    def interior_dir(self) -> Vec:
        return REAR_34 if self.A.open_dir[1] > 0.5 else BELOW_34 if self.A.open_dir[2] < -0.5 else vnorm(vadd(self.A.open_dir, (0.4, -0.3, 0.6)))

    def sub(self, text: str, view: Vec) -> str:
        """Panel subtitle with the rear-view note when the camera sits behind the gadget (MIN-32)."""
        if rear_view(view) and REAR_NOTE not in text and "seen from the rear" not in text:
            return f"{text}; {REAR_NOTE}"
        return text

    def body_names(self) -> list[str]:
        return [c.name for c in self.A.body]

    def ctx_show(self, ghost_body: bool = True) -> dict[str, str]:
        show = {n: "solid" for n in self.installed}
        for n in self.body_names():
            show[n] = "ghost" if ghost_body else "solid"
        return show

    def add(self, step: Step) -> Step:
        base, k = step.id, 2
        while any(x.id == step.id for x in self.steps):
            step.id = f"{base}-{k}"
            k += 1
        step.camera = step.camera or self._camera(step)
        step.parts = list(dict.fromkeys(n.split("#", 1)[0] for n in step.parts))
        step.highlight = list(dict.fromkeys(n.split("#", 1)[0] for n in step.highlight))
        if step.id != "check-parts":  # the banner covers the parts check; issues go to the steps that fit the parts
            names = {c.name: self.n.human(c) for c in self.d.comps.values()}
            hl = {n.split("#", 1)[0] for n in (step.adds or step.highlight)}  # the parts this step fits (or prints)
            for iss in self.issues.pairs:
                spec = [m for m in iss.parts if m not in self.generic] or list(iss.parts)
                if any(m in hl for m in spec):
                    step.known.append(iss.line(names))
                    step.extra_stops.append(iss.stop(names))
        for f in step.figures:  # MIN-32: every rear-view panel says so
            for p in f.panels:
                p.subtitle = self.sub(p.subtitle, p.view_dir)
        self.steps.append(step)
        self.ids[step.id] = len(self.steps)
        for n in step.adds:
            if n not in self.installed:
                self.installed.append(n)
        return step

    def _camera(self, step: Step) -> Optional[dict]:
        for f in step.figures:
            if f.kind == "3d" and f.panels:
                cam = camera_hint(f.panels[-1], self.bboxes)
                if cam:
                    return cam
        for names in (step.highlight, step.parts, list(self.bboxes)):
            names = [n for n in names if n in self.bboxes]
            if names:
                p = PanelSpec(subtitle="", show={n: "focus" for n in names}, focus=names, view_dir=FRONT_34)
                return camera_hint(p, self.bboxes)
        return None

    def lbl(self, c: Comp, *lines: str, prio: int = 0) -> LabelSpec:
        out: list[str] = []
        for x in lines:
            if x:
                out += wrap(x, 30, 3)
        return LabelSpec(text="\n".join(out), comp=c.name, priority=prio)

    def rows_like(self, *, kind: Optional[str] = None, role: Optional[str] = None, refs: Optional[list] = None,
                  pattern: Optional[str] = None, no_model: bool = True) -> list[dict]:
        out = []
        for p in self.d.purchased():
            if no_model and p.get("modelPart") and p.get("modelPart") in self.d.comps:
                continue
            lib = self.d.lib.get(p.get("module_id")) or {}
            if kind and p.get("kind") != kind:
                continue
            if role and str(lib.get("role") or "") != role:
                continue
            if refs is not None and not set(refs) & set(p.get("refs") or []):
                continue
            if pattern and not re.search(pattern, f"{p.get('name')} {p.get('module_id')} {lib.get('role') or ''}", re.I):
                continue
            out.append(p)
        return out

    def harness_rows(self) -> list[dict]:
        return [p for p in self.d.purchased() if p.get("kind") == "cable" or str((self.d.lib.get(p.get("module_id")) or {}).get("role")) == "harness"]

    def usb_cable_row(self) -> Optional[dict]:
        return next(iter(self.rows_like(role="usb_cable") or self.rows_like(pattern=r"USB-?C to USB-?C|usb_cable")), None)

    def storage_rows(self) -> list[dict]:
        return self.rows_like(pattern=r"storage|STORAGE_MICROSD|microSD card|SDHC|SDXC", no_model=True)

    def tie_rows(self) -> list[dict]:
        return self.rows_like(role="ties") or self.rows_like(pattern=r"cable tie")

    def label_rows(self) -> list[dict]:
        return self.rows_like(role="label") or self.rows_like(pattern=r"device id label")

    def adapter_rows(self) -> list[dict]:
        return self.rows_like(role="adapter") or self.rows_like(pattern=r"power adapter|charger|wall supply")

    def unmodelled_screw_rows(self, pattern: str) -> list[dict]:
        """Purchased screw rows with no CAD part whose `use` names this place (e.g. the microSD support-post screws)."""
        out = []
        for p in self.d.purchased():
            if p.get("modelPart") and p.get("modelPart") in self.d.comps:
                continue
            if not re.search(r"screw|bolt|standoff|spacer", f"{p.get('name')} {p.get('module_id')}", re.I):
                continue
            if re.search(pattern, str(p.get("use") or ""), re.I):
                out.append(p)
        return out

    def unmodelled_line(self, p: dict) -> str:
        use = str(p.get("use") or "")
        m = re.search(r"\((.*)\)\s*$", use)
        purpose = (m.group(1) if m else use).strip()
        return (f"Fit the {int(p.get('quantity') or 1)} × **{self.n.row_name(p)}** ({purpose}). These screws are not modelled "
                "in the CAD, so the pictures do not show them; tighten them gently until snug.")

    def harnessed(self) -> list[Group]:
        return [g for g in [*self.A.features, *self.A.internals] if g.ref and g.ref in self.ends]

    def loose_ends(self) -> list[str]:
        """Harnessed modules with a module-end record but no CAD part: an extra module the CAD does not mount (an RTC in
        electronics.extra_modules). Their cable is fitted in the bench test like every other (MAJ-10, hand-off b-to-a #3)."""
        grouped = {g.ref for g in [*self.A.features, *self.A.internals] if g.ref}
        return sorted(r for r in self.ends if r not in grouped and self.d.comp_for_ref(r) is None)

    def mod_fasteners(self) -> Counter:
        """Screws per kit ref from the CAD fastener records (cad/digital-checks.json fastener_engagement)."""
        cnt: Counter = Counter()
        for c in self.d.cad_checks():
            if c.get("id") == "fastener_engagement":
                for s in (c.get("data") or {}).get("screws") or []:
                    head = str(s.get("screw") or "").split(" ", 1)[0]
                    cnt[(s.get("group"), head)] += 1
        return cnt

    def group_screw_qty(self, g: Group) -> dict[str, int]:
        """Quantity per fastener component of a group: the CAD fastener records first, the solids second (MAJ-11)."""
        cnt = self.mod_fasteners()
        out = {}
        for c in g.fasteners:
            base = c.name.split("#", 1)[0].replace("PURCHASE_", "")
            q = cnt.get((base, g.ref)) if g.ref else None
            out[c.name] = int(q) if q else self.n.qty(c)
        return out

    # ------------------------------------------------------------------ the plan
    @property
    def printed(self) -> bool:
        """A printed-only object (spec kind "printed", catalog package A): nothing electrical to fit, wire or flash."""
        return self.d.spec.get("kind") == "printed"

    def plan_printed(self) -> list[Step]:
        """Printed-only object: check the parts, print, fit the feet, final checks. There is no cartridge, wiring,
        flashing or self-test step because the design has no electronics."""
        A = self.A
        bed = self.bed_figure() if (self.d.print_files or A.prints) else None
        if A.purchases or self.d.purchased():  # nothing to check when the object is only its print (no feet)
            self.step_check_parts([])
        if A.prints:
            self.step_print(bed)
        self.installed.extend(self.body_names())
        if A.feet:
            self.step_feet()
        self.step_final_printed()
        return self.steps

    def plan(self) -> list[Step]:
        A = self.A
        if self.printed:
            return self.plan_printed()
        coupon = self.coupon_files()
        bed = self.bed_figure() if (self.d.print_files or A.prints) else None
        self.step_check_parts(coupon)
        if A.cartridge_prints:
            self.step_print_cartridge()
        if self.storage_needed():
            self.step_prepare_card()
        if A.backplane or A.core:
            self.step_cartridge()
            self.step_bench_test()
        if coupon:
            self.step_fit_coupon(coupon, bed)
        if A.prints:
            self.step_print(bed)
        self.installed.extend(self.body_names())
        if A.inserts:
            self.step_inserts()
        if A.backplane or A.core:
            self.step_mount_cartridge()
        for g in A.features:
            self.step_feature(g)
        for g in A.internals:
            self.step_feature(g, internal=True)
        if [i for i in self.d.instances if i.get("slot")] or A.cables:
            self.step_plug()
        if A.cables or A.ties or self.tie_rows():
            self.step_route()
        self.step_flash()
        if A.covers:
            self.step_close()
        if self.wall_mount():
            self.step_hang()
        self.step_final()
        gs = sorted({i + 1 for i, st in enumerate(self.steps) if st.id != "check-parts"
                     and any(self.d.comps.get(p) is not None and self.d.is_accessory(self.d.comps[p])
                             and re.search(r"gasket", p, re.I) for p in st.parts)})
        for st in self.steps:
            for f in st.figures:
                for pn in f.panels:
                    for lb in pn.labels:
                        if "{GASKET_STEPS}" in lb.text:
                            lb.text = lb.text.replace("{GASKET_STEPS}", ", ".join(str(x) for x in gs) or "?")
        return self.steps

    # ------------------------------------------------------------------ 1. parts
    def step_check_parts(self, coupon: list[str]) -> None:
        A, n = self.A, self.n
        buys = [c for c in A.purchases]
        kit_fig = self.kit_figure(buys)
        ex_fig = self.exploded_figure()
        n_print = len(A.prints)
        bom = self.d.purchased()
        use = (join_and([f"{int(p.get('quantity') or 1)} × **{n.row_name(p)}**" for p in bom]) + ".") if bom else n.use(buys)
        if self.printed:  # catalog package A: a printed-only object has no modules, cables or bench test
            body = ("Lay every purchased item on a clear table and tick it off against the parts list, with its quantity. Keep "
                    "small parts in a tray" + (", away from children" if self.child() else "") + ".\n\n"
                    + (f"The {n_print} printed part{'s' if n_print != 1 else ''} {'are' if n_print != 1 else 'is'} printed in the next "
                       "step. " if n_print else "") + "The exploded view shows where every part ends up.")
        else:
            body = (
                "Lay every purchased item on a clear table and tick it off against the parts list: exact module, "
                "quantity and connector pin count. Keep screws, inserts and feet in a small tray"
                + (", away from children" if self.child() else "") + ".\n\n"
                "Do not substitute a module. The backplane ports, cable pin counts and firmware pin numbers are fixed "
                "for these exact modules. Compare each module's printed pin labels with the wiring table "
                "(`manual/wiring.png`) now, before anything is printed or plugged.\n\n"
                + (f"The {n_print} printed part{'s' if n_print != 1 else ''} come later, after the bench test. " if n_print else "")
                + "The exploded view shows where every part ends up."
            )
        self.add(Step(
            id="check-parts", title="Check the parts",
            use=use,
            tools=[], tools_note="A clear table, a small parts tray and the parts list.",
            body=body,
            expected=("Every line of the parts list is present in the stated quantity." if self.printed else
                      "Every line of the parts list is present in the stated quantity, and each module's printed pin labels match the wiring table."),
            stop=("a part is missing or differs from the parts list. Photograph it and ask before continuing." if self.printed else
                  "a module, its pin labels or a cable's pin count differ from the parts list or the wiring table. Photograph both sides of the module and ask before continuing; do not swap in a similar part."),
            parts=[c.name for c in buys], highlight=[c.name for c in buys],
            figures=[f for f in (kit_fig, ex_fig) if f],
        ))

    def kit_figure(self, comps: list[Comp]) -> Optional[FigureSpec]:
        """Every purchased row, one of each: CAD envelopes, plus generic glyphs for rows with no CAD, and the gasket
        drawn as the strip it is bought as (MIN-35)."""
        n = self.n
        gaskets = [c for c in comps if c.bbox and self.d.is_accessory(c) and re.search(r"gasket", f"{c.name} {c.label}", re.I)]
        items = [c for c in comps if c.bbox and c not in gaskets]
        if not items:
            return None
        show = {c.name: "solid" for c in items}
        labels = [self.lbl(c, f"{n.qty(c)} × {n.short(c)}", prio=1) for c in items]
        meshes: list[MeshSpec] = []
        covered = {c.name for c in items}
        glyph_rows = []
        for p in self.d.purchased():
            mp = p.get("modelPart")
            if mp and mp in covered:
                continue
            if mp and mp in {g.name for g in gaskets}:
                glyph_rows.append((p, "strip"))
                continue
            role = str((self.d.lib.get(p.get("module_id")) or {}).get("role") or "")
            g = ("coil" if p.get("kind") == "cable" or role in ("harness", "usb_cable") else "tie" if role == "ties"
                 else "label" if role == "label" else "card" if re.search(r"storage|card", f"{role} {p.get('name')}", re.I)
                 else "box")
            glyph_rows.append((p, g))
        for p, g in glyph_rows:
            key = f"glyph:{g}:{p.get('module_id') or p.get('name')}"
            meshes.append(MeshSpec(path=key, color=(0.82, 0.8, 0.74) if g != "strip" else (0.2, 0.2, 0.22), name=key))
            q = int(p.get("quantity") or 1)
            if g == "strip":
                cuts = []
                for c in gaskets:
                    rd = self.rings.get(c.name)
                    cuts.append(f"Ø{rd['od_mm']:.0f}" if rd else "")
                cut_txt = (" and ".join(x for x in cuts if x) + " mm") if any(cuts) else ""
                txt = [f"{q} × foam gasket strip", f"cut into {len(gaskets)} rings {cut_txt} (steps {{GASKET_STEPS}})".replace("  ", " ")]
            else:
                txt = [f"{q} × {n.row_name(p)}"]
            labels.append(LabelSpec(text="\n".join(x for t in txt if t for x in wrap(t, 30, 3)), point=None, comp=key, priority=1))
        return FigureSpec(
            file=self.fig_name("parts"), title=f"{self.num}  Check the purchased parts",
            panels=[PanelSpec(subtitle="Every purchased row, one of each shown; grey glyphs mark items with no CAD model (not to scale)",
                              show=show, view_dir=(0.0, -0.8, 1.0), layout="grid", label_room=0.0, pad=1.12, labels=labels,
                              meshes=meshes)],
            footer="Saved CAD envelopes of the purchased parts, not product photos | modelled parts to one scale | glyphs not to scale",
            alt="Every purchased row laid out on a table, one of each, labelled with the quantity used and its name.",
            caption=("CAD envelopes, not photos: check the real parts against the parts list." if self.printed else
                     "CAD envelopes and placeholder glyphs, not photos: check the real parts against the parts list and the wiring table."),
            size=(2000, 1500),
        )

    def exploded_figure(self) -> Optional[FigureSpec]:
        A = self.A
        if not A.shell:
            return None
        n = self.n
        ax = A.open_dir
        depth = abs(sum(self.shell_size[i] * ax[i] for i in range(3))) or self.scale
        show, offs, labels = {}, {}, []
        show[A.shell.name] = "solid"
        for c in A.inserts:
            show[c.name] = "solid"
        labels.append(self.lbl(A.shell, n.human(A.shell), prio=2))
        feats = [c for g in A.features for c in g.comps]
        ints = [c for g in A.internals for c in g.comps]
        layers = [(feats + ints, 0.95), (A.cartridge + A.cartridge_fasteners, 1.5), (A.covers + A.cover_fasteners, 2.05)]
        for comps, k in layers:
            for c in comps:
                show[c.name] = "solid"
                offs[c.name] = vscale(ax, depth * k)
        for g in [*A.features, *A.internals]:  # parts fitted from outside explode outward along their own face
            if g.module and mount_kind(self.d.lib.get(g.module.module_id)) in ("panel_nut", "snap_out") and g.normal:
                for c in g.comps:
                    offs[c.name] = vscale(g.normal, 0.35 * max(self.shell_size))
        for c in A.feet:
            show[c.name] = "solid"
            offs[c.name] = (0.0, 0.0, -0.25 * self.shell_size[2]) if ax[2] > -0.5 else vscale(ax, depth * 2.8)
        for g in A.features:
            if g.module:
                labels.append(self.lbl(g.module, (g.label or n.noun(g).capitalize()), prio=1))
        if A.backplane:
            labels.append(self.lbl(A.backplane, "Core cartridge", "(backplane + core board)", prio=2))
        for c in A.covers[:1]:
            labels.append(self.lbl(c, n.human(c), f"fits along {axis_short(vscale(ax, -1))}", prio=2))
        if ax[1] > 0.5:
            view = (1.0, 0.18, 0.5)
        elif ax[2] < -0.5:
            view = (0.9, -0.6, 0.25)
        else:
            view = vnorm(vadd(vscale(ax, 0.3), (0.7, -0.6, 0.5)))
        return FigureSpec(
            file="manual/exploded.png", title=f"{self.d.product_name}: exploded view",
            panels=[PanelSpec(subtitle=f"Parts separated along {axis_phrase(ax)}, the direction they are fitted from; seen from the gadget's left",
                              show=show, offsets=offs, view_dir=view, focus=list(show), labels=labels, label_room=0.04)],
            footer=FOOTER,
            alt=(f"Exploded view of the {self.d.product_name}: the printed part and its feet separated along one axis." if self.printed else
                 f"Exploded view of the {self.d.product_name}: shell, front modules, core cartridge and closing part separated along one axis."),
            caption="Separation distances are for clarity only." + ("" if self.printed else " Cables are hidden."),
            size=(2000, 1100),
        )

    # ------------------------------------------------------------------ 2. story card
    def storage_needed(self) -> bool:
        sd = self.d.sdcard()
        has_mod = any(role_is(c, "sd", self.d) for c in self.A.cartridge_mods)
        return bool(self.storage_rows() or (has_mod and sd.get("present")))

    def step_prepare_card(self) -> None:
        n, d = self.n, self.d
        rows = self.storage_rows()
        sd = d.sdcard()
        card = n.row_name(rows[0]) if rows else None
        audio = sd.get("audio")
        body = (
            ("Format the " + (f"**{card}**" if card else "microSD card") + " as FAT32 on a computer (cards of 32 GB or less "
             "usually come formatted FAT32). ")
            + ("Copy everything inside `firmware/sdcard/` to the root of the card, not the folder itself. "
               if sd.get("present") else "This build delivered no `firmware/sdcard/` folder; check `SOFTWARE_SETUP.md` before continuing. ")
            + "Then open `stories.json` on the card and check that it says `\"audio\": true`. "
            + ("**This build's `stories.json` says `\"audio\": false`: the stories have no audio yet.** Generate the story audio "
               "in the app (Code tab) and copy the new card folder before the bench test, or a tap will only chime. "
               if audio is False else "")
            + "\n\nKeep the card out of the slot until the bench test; insert it with the gadget unplugged."
        )
        self.add(Step(
            id="prepare-card", title="Prepare the story card",
            use=(f"1 × **{card}**" if card else "1 × microSD card (see the parts list)") + " and the delivered `firmware/sdcard/` folder.",
            tools=["A computer with a microSD card reader"],
            body=body,
            expected="The card is FAT32, holds the contents of `firmware/sdcard/` at its root, and its `stories.json` says `\"audio\": true`.",
            stop="the computer cannot format or write the card, or `stories.json` says `\"audio\": false`. Generate the story audio first; do not edit the files by hand.",
            parts=[c.name for c in self.A.cartridge_mods if role_is(c, "sd", d)] + [r.get("module_id") for r in rows if r.get("module_id")],
            highlight=[c.name for c in self.A.cartridge_mods if role_is(c, "sd", d)],
            rows=[r.get("module_id") for r in rows if r.get("module_id")],
        ))

    # ------------------------------------------------------------------ 3. cartridge prints (only when the cartridge has prints)
    def step_print_cartridge(self) -> None:
        A, n = self.A, self.n
        lines = [self.print_line(c) for c in A.cartridge_prints]
        self.add(Step(
            id="print-cartridge", title="Print the cartridge parts",
            use=n.use(A.cartridge_prints), tools=["Printer or printing service", "Slicer"],
            body="Print these first, so the cartridge can be bench-tested before the shell is printed:\n\n" + "\n".join(lines),
            expected="Each cartridge part is complete and unscaled.",
            stop="a part is warped or cracked. Reprint it rather than force it.",
            parts=[c.name for c in A.cartridge_prints], highlight=[c.name for c in A.cartridge_prints],
        ))

    # ------------------------------------------------------------------ 4. cartridge
    def step_cartridge(self) -> None:
        A, n, d = self.A, self.n, self.d
        bp, core = A.backplane, A.core
        mods = A.cartridge_mods
        parts = [c for c in [bp, core, *mods, *A.cartridge_prints] if c]
        slots = d.backplane.slots if d.backplane else {}
        lines = []
        usb = None
        if core:
            usb = overhang_dir(core, bp) if bp else None
            usb = connector_dir(A, core) or usb
            fp = slot_desc(slots.get("U1"))
            note = slot_note(slots.get("U1"))
            lines.append(
                f"Press the **{n.name(core)}** into the core-board sockets at **U1**"
                + (f" ({fp})" if fp else "")
                + (f" so its USB connectors point toward {axis_phrase(usb)}, over the board edge" if usb else "")
                + ". Push evenly on both ends until it sits fully down. Every pin must be inside a socket; none may hang outside a row."
                + (f" Orientation: {note}." if note else "")
            )
        for c in mods:
            inst = d.instance(c.ref)
            slot = inst.get("slot")
            fp = slot_desc(slots.get(slot))
            note = slot_note(slots.get(slot))
            pw = d.prewire(c.ref)
            lines.append(
                f"Plug the **{n.name(c)}** ({c.ref}) into socket **{slot or '?'}**" + (f" ({fp})" if fp else "")
                + ". Match its pin labels to the labels next to the socket before pressing it down."
                + (f" Orientation: {note}." if note else "")
                + (" Its header pins arrive soldered from our factory; the maker never solders." if pw else "")
            )
        if A.cartridge_prints:
            lines.append(f"Seat the backplane in {join_and([n.name(c) for c in A.cartridge_prints])}.")
        body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
        body += ("\n\nThe socket ids (U1, A1, S1) are the reference designators of the backplane; check the board's own "
                 "markings and stop if they differ. Handle boards by their edges.")
        figs = []
        if bp and bp.bbox:
            stack = [c for c in [core, *mods] if c]
            lift = max(30.0, 0.5 * max(bp.size[0], bp.size[1]))
            offs = {c.name: (0.0, 0.0, lift * (1.4 if c is core else 1.0)) for c in stack}
            for c in A.cartridge_prints:
                offs[c.name] = (0.0, 0.0, -lift * 0.8)
            show = {c.name: "focus" for c in parts}
            labels_a = []
            if core:
                labels_a.append(self.lbl(core, n.short(core), f"USB toward {axis_short(usb)}" if usb else "", prio=3))
            for c in mods:
                inst = d.instance(c.ref)
                labels_a.append(self.lbl(c, n.short(c), f"into {inst.get('slot') or 'its socket'}", prio=2))
            labels_a.append(self.lbl(bp, n.short(bp), prio=1))
            pwr = next((k for k in anchors_for(A, bp.ref or "BP1") if "PWR" in k.upper() or "USB" in k.upper()), None)
            labels_b = []
            if pwr:
                labels_b.append(LabelSpec(text=f"USB-C power input\nfaces {axis_phrase(dominant_axis((A.data.anchors[pwr][0] - bp.center[0], A.data.anchors[pwr][1] - bp.center[1], 0.0)))}", anchor=pwr, priority=2))
            if core:
                labels_b.append(self.lbl(core, "All pins inside", "the socket rows", prio=1))
            figs.append(FigureSpec(
                file=self.fig_name("cartridge"), title=f"{self.num}  Assemble the core cartridge",
                panels=[PanelSpec(subtitle="A  Modules lifted above their sockets", show=show, offsets=offs, view_dir=(0.45, -1.0, 0.62),
                                  focus=[c.name for c in parts], labels=labels_a, arrow_comps=[c.name for c in stack]),
                        PanelSpec(subtitle="B  Seated on the backplane", show=show, view_dir=TOP_34,
                                  focus=[c.name for c in parts], labels=labels_b)],
                footer=FOOTER + " | header pins and socket contacts simplified",
                alt="The core board and the plug-in modules lifted above the backplane, then seated in their sockets.",
                caption="Shows placement and orientation only; it does not prove every pin is in its socket. Check each row by eye.",
            ))
        self.add(Step(
            id="assemble-cartridge", title="Assemble the core cartridge",
            use=n.use(parts), tools=[], tools_note="Fingers only. Work on a clean, dry, non-metal surface.",
            body=body,
            expected="The core board and every plug-in module sit fully down in their sockets, in the orientation shown, with no visible pin outside a socket row.",
            stop="a pin misses its socket, a module rocks, or a label on a module differs from the socket labels. Unplug it, photograph the header and the board markings, and ask. Do not bend pins straight by force.",
            parts=[c.name for c in parts], highlight=[c.name for c in parts], figures=figs,
        ))

    # ------------------------------------------------------------------ 5. bench test on the table
    def uart_text(self) -> tuple[str, Optional[str]]:
        """Which core-board port to use, and a stop rule when the library records a connector-type doubt (MIN-15)."""
        core_mod = self.d.lib.get(self.A.core.module_id) if self.A.core else {}
        blob = str(core_mod)
        port = "the core board's port labelled **UART** (its on-board USB-to-UART bridge)" if "UART" in blob else "the core board's USB port"
        stop = None
        if re.search(r"Micro-USB", blob) and re.search(r"Type-C|USB-C", blob):
            stop = ("the core board's UART port is Micro-USB rather than USB-C (the parts data records that both versions are "
                    "sold): stop and use a Micro-USB data cable instead of the kit cable")
        return port, stop

    def flash_text(self) -> str:
        st, fw = self.selftest, self.d.firmware_dir
        env = None
        ini = fw / "platformio.ini"
        if ini.is_file():
            envs = [e for e in re.findall(r"^\[env:([\w-]+)\]", ini.read_text(errors="ignore"), re.M) if e != "native"]
            env = envs[0] if envs else None
        lines = []
        if st.get("web_flasher"):
            lines.append("Use the **Flash** button in the app's Build tab (it writes `firmware/firmware-merged.bin` from the browser, Chrome or Edge).")
        if st.get("merged_bin"):
            lines.append("Or, from the delivered project folder, check the image and write it with esptool:\n\n```sh\ncd firmware\n"
                         + ("sha256sum -c SHA256SUMS\n" if st.get("sha256sums") else "")
                         + "esptool --chip esp32s3 write-flash 0x0 firmware-merged.bin\n```\n\n"
                         + ("`sha256sum -c` must print `OK` for both images before you flash. " if st.get("sha256sums") else "")
                         + "`SOFTWARE_SETUP.md` §1 has the full esptool options.")
        lines.append("Advanced: PlatformIO rebuilds from source and flashes (`cd firmware && pio run"
                     + (f" -e {env}" if env else "") + " -t upload`); use it only if you changed the code.")
        return "\n\n".join(lines)

    def selftest_text(self) -> tuple[str, str]:
        st = self.selftest
        baud = st.get("baud")
        cmd = st.get("command") or "selftest"
        keys = st.get("keys") or []
        line = None
        if st.get("summary_prefix"):
            bits = ['"ok":true', '"passed":…', '"failed":0']
            if "skipped" in keys:
                bits.append('"skipped":…')
            if "operator_checks" in keys:
                bits.append('"operator_checks":[…]')
            if "firmware" in keys and st.get("version"):
                bits.append(f'"firmware":"{st["version"]}"')
            line = f'{st["summary_prefix"]} {{{",".join(bits)},…}}'
        how = (f"Open a serial monitor on the same port" + (f" at {baud} baud" if baud else "") + f" (`pio device monitor"
               + (f" -b {baud}" if baud else "") + "` or any serial terminal), type `" + cmd + "` and press Enter.")
        exp = (f"The run ends with one line like `{line}`: `\"ok\":true` and `\"failed\":0`. Write that whole line down "
               "(or save the monitor output); it is the record of this test." if line else
               "The self-test prints its result on the serial monitor; save the output (see `SOFTWARE_SETUP.md` §5).")
        return how, exp

    def step_bench_test(self) -> None:
        A, n, d = self.A, self.n, self.d
        port, uart_stop = self.uart_text()
        usb_row = self.usb_cable_row()
        cable_rows = self.harness_rows()
        groups = self.harnessed()
        loose = self.loose_ends()
        fits = []
        for g in groups:
            me = self.ends[g.ref]
            fits.append(f"**{g.label or n.noun(g).capitalize()}** ({g.ref}): " + me.instruction(n.name(g.module)))
        for r in loose:
            fits.append(f"**{d.module_name(r)}** ({r}): " + self.ends[r].instruction(d.module_name(r)))
        how, exp = self.selftest_text()
        mods = [g for g in [*A.features, *A.internals] if g.module]
        checks = dedupe([SELFTEST_CHECK.get(g.role, f"the {n.noun(g)} responds") for g in mods]
                        + ([SELFTEST_CHECK["sd"]] if any(role_is(c, "sd", d) for c in A.cartridge_mods) else []))
        sd_line = ("Insert the prepared story card into the microSD module's slot (gadget unplugged). " if "prepare-card" in self.ids else "")
        cable = f"**{n.row_name(usb_row)}** from the kit" if usb_row else "a USB-C data cable"
        body = (
            "Do this on the table, before anything is printed: it proves the electronics before you spend hours printing the shell.\n\n"
            "**1. Fit each module's cable at the module end.**\n\n" + "\n".join(f"- {f}" for f in fits) + "\n\n"
            "**2. Plug in.** " + sd_line + "Plug each cable's port-end plug into its port on the cartridge (the port names are in the "
            "wiring table, `manual/wiring.png`). Lay the modules flat on the table, clear of each other.\n\n"
            f"**3. Flash.** Connect a computer to {port} with the {cable}. " + self.flash_text() + "\n\n"
            f"**4. Self-test.** {how} Check each module in turn:\n\n" + "\n".join(f"- {c}" for c in checks) + "\n\n"
            f"{exp}\n\n"
            "**5. Unplug.** Unplug the USB cable, then unplug each cable at the cartridge port (hold the plug body). Leave the "
            "cables fitted on the modules. Nothing here records the test as passed: you do."
        )
        stops = [s for g in groups for s in self.ends[g.ref].stop_rules(n.name(g.module))]
        stops += [s for r in loose for s in self.ends[r].stop_rules(d.module_name(r))]
        if uart_stop:
            stops.append(uart_stop)
        self.add(Step(
            id="bench-test", title="Bench-test the electronics on the table",
            use=join_and(["the assembled core cartridge", f"1 × **{n.row_name(usb_row)}**" if usb_row else "a USB-C data cable",
                          f"every module ({', '.join(n.name(g.module) for g in mods)})" if mods else "",
                          f"{len(cable_rows)} module cable{'s' if len(cable_rows) != 1 else ''}" if cable_rows else ""]) + ".",
            tools=["A computer with Chrome or Edge (or esptool)", "A serial monitor"],
            body=body,
            expected="The flash finishes without errors, the self-test summary line shows `\"ok\":true` and `\"failed\":0`, and every module behaves as listed.",
            stop="the upload fails, the board resets repeatedly, any part gets warm, or a module does not respond. Unplug USB first, save the monitor output and check that module's cable against the wiring table",
            extra_stops=stops,
            parts=[c.name for c in [A.backplane, A.core, *A.cartridge_mods] if c] + [g.module.name for g in mods],
            highlight=[c.name for c in [A.backplane, A.core, *A.cartridge_mods] if c],
            rows=[r.get("module_id") for r in ([usb_row] if usb_row else []) + cable_rows if r.get("module_id")],
            extra_images=["manual/wiring.png"] + [f"manual/cable-end-{r.lower()}.png" for r in [g.ref for g in groups] + loose],
            module_end=[g.ref for g in groups] + loose,
        ))

    # ------------------------------------------------------------------ 6-7. prints
    def coupon_files(self) -> list[str]:
        return [p.name for p in self.d.print_files if p.name.upper().startswith("TEST_")]

    def bed_figure(self) -> Optional[FigureSpec]:
        files = self.d.print_files
        meshes: list[MeshSpec] = []
        labels: list[LabelSpec] = []
        color = self.A.shell.color if self.A.shell else (0.55, 0.78, 0.75)
        tipped = {str(r.get("name")) for r in self.d.printed() if r.get("tipped")}
        tipped_h: list[str] = []
        for p in files:
            nm = p.stem
            c = self.d.comps.get(nm)
            col = c.color if c else (0.86, 0.86, 0.83) if nm.upper().startswith("TEST_") else color
            meshes.append(MeshSpec(path=str(p), color=col, name=nm))
            human = (self.n.human(c) if c else camel_words(nm).capitalize())
            tail = "fit test, not a gadget part" if nm.upper().startswith("TEST_") else self.support_word(nm)
            if nm in tipped:  # the order stage tipped it: this exported pose is not how it prints (live failure 2026-11-02)
                tail = f"prints on its side (too big as shown); {tail}"
                tipped_h.append(human[:1].lower() + human[1:])
            labels.append(LabelSpec(text="\n".join(wrap(f"{human}: {tail}", 30, 3)), comp=nm, priority=1))
        if not meshes:
            return None
        sub = "Each part in the orientation it was exported in (the side resting on the bed is down)"
        if tipped_h:
            sub = ("Each part as exported; the " + join_and(tipped_h) + (" prints" if len(tipped_h) == 1 else " print")
                   + " on its side instead (rotate it as the print table says)")
        return FigureSpec(
            file="manual/print-bed.png", title="Print the supplied STL files without scaling",
            panels=[PanelSpec(subtitle=sub,
                              meshes=meshes, view_dir=(0.3, -0.75, 0.85), focus_meshes=True, labels=labels,
                              label_room=0.05)],
            footer="Saved print meshes (print/*.stl) laid side by side; not a packed print job | supports not shown",
            alt="The print STL files resting on a notional print bed in their exported orientation.",
            caption="Orientation view only. It does not show supports or prove that a printer can print the part.",
        )

    def coupon_rows(self) -> list[tuple[str, str, str]]:
        """(coupon file, feature with its nominal size, real part it tests and how many) from the CAD's coupon data (MIN-14)."""
        n, A = self.n, self.A
        summ = (self.d.cad_summary().get("print") or {})
        out = []
        btn = next((g.module for g in A.features if g.role == "button" and g.module), None)
        ins = A.inserts[0] if A.inserts else None
        cov = A.cover_fasteners[0] if A.cover_fasteners else None
        usb_row = self.usb_cable_row()
        tie_row = (self.tie_rows() or [None])[0]
        for f in self.coupon_files():
            stem = f.rsplit(".", 1)[0]
            for t in (summ.get(stem) or {}).get("tests") or []:
                tl = t.lower()
                m = re.search(r"\(x(\d+)", t)
                k = int(m.group(1)) if m else 1
                part = None
                if "button" in tl and btn:
                    part = f"1 × **{n.name(btn)}**"
                elif "lip" in tl:
                    part = f"1 × `{stem}` on its mating seam coupon"
                elif "insert" in tl and ins:
                    part = f"{k} × **{n.name(ins)}**" + (" and 1 × **" + n.name(cov) + "**" if "screw" in tl and cov else "")
                elif "screw" in tl and cov:
                    part = f"1 × **{n.name(cov)}**"
                elif "tie" in tl and tie_row:
                    part = f"1 × **{n.row_name(tie_row)}**"
                elif "usb" in tl and usb_row:
                    part = f"the plug of the **{n.row_name(usb_row)}**"
                elif "microphone" in tl or "mic" in tl:
                    part = "none: hold it to the light and check the hole is open"
                else:
                    part = "none: check it by eye"
                out.append((stem, t, part))
        return out

    def step_fit_coupon(self, coupon: list[str], bed: Optional[FigureSpec]) -> None:
        A, n = self.A, self.n
        rows = self.coupon_rows()
        names = ", ".join(f"`print/{f}`" for f in coupon)
        table = ""
        if rows:
            table = ("| Coupon | Feature (nominal, from the CAD) | Try it with |\n|---|---|---|\n"
                     + "\n".join(f"| `{a}` | {b} | {c} |" for a, b, c in rows) + "\n\n")
        btn = next((g.module for g in A.features if g.role == "button" and g.module), None)
        tested = [c for c in [btn, *A.inserts[:1], *A.cover_fasteners[:1]] if c]
        body = (
            f"Print {names} first, in the same material and with the same settings you will use for the shell. "
            "They are small, cheap samples of the tight interfaces: try each real part in its matching feature by hand.\n\n"
            + table
            + "To trial an insert, melt it in with the iron exactly as in the insert step. Parts should go in without force and stay put. "
            "If something binds or is loose, stop before printing the shell and record what binds. Do not scale the design to "
            "force a fit. Printer accuracy is not assumed: the designed clearances are trial values that these real coupons check."
        )
        self.add(Step(
            id="fit-coupon", title="Print and try the fit coupons",
            use=join_and([f"1 × `{f.rsplit('.', 1)[0]}`" for f in coupon]) + (" and, for trying only, " + join_and([f"1 × **{n.name(c)}**" for c in tested]) if tested else "") + ".",
            tools=["Printer or printing service", "Ruler or caliper", "Soldering iron with a heat-set insert tip"] if A.inserts else ["Printer or printing service", "Ruler or caliper"],
            body=body,
            expected="The real parts fit the printed coupons by hand without force and are held without wobble. Keep the coupons as a reference; they are not gadget parts.",
            stop="a part binds, a hole is too tight or loose, or an insert will not seat square. Measure the hole and record the material and printer; send that with a photo before printing the shell.",
            parts=[f.rsplit(".", 1)[0] for f in coupon] + [c.name for c in tested],
            highlight=[c.name for c in tested],
            figures=[bed] if bed else [],
        ))

    def support_word(self, name: str) -> str:
        p = next((r for r in self.d.printed() if str(r.get("name")) == name or str(r.get("file", "")).endswith(f"/{name}.stl")), {})
        if p.get("supports") is True:
            return "slicer adds supports"
        if p.get("supports") is False:
            return "no supports"
        return "as exported, on the bed"

    def print_line(self, c: Comp) -> str:
        p = dict(c.raw.get("print") or {})
        p.update(next((r for r in self.d.printed() if str(r.get("name")) == c.name or str(r.get("file", "")).endswith(f"/{c.name}.stl")), {}))
        bits = []
        if p.get("material"):
            bits.append(str(p["material"]))
        if p.get("color_name") or p.get("color"):
            bits.append(str(p.get("color_name") or p.get("color")))
        bits += print_notes(p)
        return f"- `print/{c.name}.stl`" + (": " + "; ".join(bits) if bits else "")

    def step_print(self, bed: Optional[FigureSpec]) -> None:
        A, n = self.A, self.n
        prints = [c for c in A.prints if c not in A.cartridge_prints]
        lines = [self.print_line(c) for c in prints]
        settings = print_settings(self.d)
        colour = (self.d.spec.get("colors") or {}).get("body_name")
        tipped_rows = [r for r in self.d.printed() if r.get("tipped")]
        tipped = sorted(str(r.get("name")) for r in tipped_rows)
        first = "" if self.printed else "Print the shell only after the bench test has passed. "
        how = (first + "Print each file from `print/` as exported: it is already "
               "in its print orientation, resting on the bed. Do not scale or rotate it.")
        if tipped:  # the order stage tipped a part that does not fit the printer as exported (live failure 2026-11-02)
            how = (first + "Do not scale any file. "
                   + join_and([f"`print/{t}.stl`" for t in tipped]) + (" does" if len(tipped) == 1 else " do")
                   + " not fit the printer as exported: rotate it in your slicer as its line below says, so it prints on its side"
                   + (" with the slicer's supports" if all(r.get("supports") for r in tipped_rows) else "")
                   + ". Print every other file as exported: it is already in its print orientation, resting on the bed.")
        body = (
            how + "\n\n" + "\n".join(lines) + "\n\n"
            + (f"{settings}\n\n" if settings else "")
            + (f"The design colour is {colour}. " if colour else "")
            + ("Remove all support material and any strings or flakes, especially inside openings and the drain hole. Check the "
               "surfaces are whole and the part stands flat on the table." if self.printed else
               "Remove all support material. Deburr every opening (grille holes, windows, button and screw holes) "
               "so no strings or flakes are left. Check that every boss and clip is whole. Try the closing part on the "
               "empty shell before adding anything: it should seat evenly by hand.")
        )
        self.add(Step(
            id="print-parts", title="Print and inspect the printed parts",
            use=n.use(prints),
            tools=["Printer or printing service", "Slicer", "Flush cutters or a deburring tool", "Ruler or caliper"],
            body=body,
            expected=("Every printed part is complete, unscaled and clean, with no support left, and it stands flat on the table."
                      if self.printed else
                      "Every printed part is complete, unscaled and clean, with open holes and whole bosses, and the closing part seats on the empty shell by hand."),
            stop=("a part is warped, cracked or has layers that come apart. Reprint rather than sand or glue it, and note which part and where."
                  if self.printed else
                  "a part is warped, a boss or clip is cracked, or the closing part will not seat without force. Reprint rather than sand or force it, and note which part and where."),
            parts=[c.name for c in prints], highlight=[c.name for c in prints],
            figures=[bed] if bed else [],
        ))

    # ------------------------------------------------------------------ 8. inserts
    def insert_groups(self) -> list[dict]:
        """Inserts grouped by where they sit and the direction they are pressed in, from each insert solid (MIN-14)."""
        A = self.A
        out: dict[tuple, dict] = {}
        sb = A.shell.bbox if A.shell else None
        for c in A.inserts:
            boxes = self.d.solid_boxes.get(c.name) or ([c.bbox] if c.bbox else [])
            for si, b in enumerate(boxes):
                sz = bbox_size(b)
                k = min(range(3), key=lambda i: sz[i])  # an insert is shorter along its axis than across it
                ctr = bbox_center(b)
                if k == 2:
                    lowhalf = sb is None or ctr[2] < (sb[4] + sb[5]) / 2
                    d, where = ((0.0, 0.0, -1.0), "floor bosses") if lowhalf else ((0.0, 0.0, 1.0), "ceiling bosses")
                elif k == 1:
                    d = (0.0, -1.0, 0.0) if A.open_dir[1] > 0.5 else (0.0, 1.0, 0.0)
                    near_rear = sb is not None and abs(sb[3] - b[3]) < 12.0
                    front_half = sb is None or ctr[1] < (sb[2] + sb[3]) / 2
                    where = "rear seam bosses" if near_rear else ("front wall bosses" if front_half else "rear wall bosses")
                else:
                    d = (1.0, 0.0, 0.0) if ctr[0] > 0 else (-1.0, 0.0, 0.0)
                    where = "side wall bosses"
                depth = None
                if sb is not None and A.open_dir[1] > 0.5:
                    depth = sb[3] - ctr[1]
                key = (where, d)
                g = out.setdefault(key, {"where": where, "dir": d, "n": 0, "depth": [], "boxes": [], "comp": c.name, "idx": []})
                g["n"] += 1
                g["boxes"].append(b)
                g["idx"].append(si)
                if depth is not None:
                    g["depth"].append(depth)
        return sorted(out.values(), key=lambda g: (-g["n"], g["where"]))

    def step_inserts(self) -> None:
        A, n = self.A, self.n
        ins = A.inserts
        qty = sum(n.qty(c) for c in ins)
        groups = self.insert_groups()
        in_cover = []
        for c in ins:
            for b in self.d.solid_boxes.get(c.name) or ([c.bbox] if c.bbox else []):
                ctr = bbox_center(b)
                for cv in A.covers:
                    cb = cv.bbox
                    if cb and cb[0] <= ctr[0] <= cb[1] and cb[2] <= ctr[1] <= cb[3] and cb[4] <= ctr[2] <= cb[5]:
                        in_cover.append(cv)
        uniq_cover = list({cv.name: cv for cv in in_cover}.values())  # Comp is unhashable: de-duplicate by name, keep order
        holders = list(A.body) + uniq_cover
        from .model import subcomp

        split = len(groups) > 1 and all(self.d.solid_boxes.get(g["comp"]) for g in groups)
        if split:  # one sub-component per group, lifted against its own pressing direction (MIN-14)
            subs = [(g, subcomp(self.d, next(c for c in ins if c.name == g["comp"]), g["idx"])) for g in groups]
            shown = [sc for _, sc in subs]
            a_off = {sc.name: vscale(g["dir"], -22.0) for g, sc in subs}
        else:
            subs, shown = [], list(ins)
            a_off = {c.name: vscale(self.insert_axis(c), 22.0) for c in ins}
        show_a = self.ctx_show()
        show_a.update({c.name: "focus" for c in shown})
        lab = []
        for g in groups:
            txt = "\n".join(wrap(f"{g['n']} × {g['where']}: pressed in along {axis_short(g['dir'])}", 30, 3))
            sc = next((x for gg, x in subs if gg is g), None)
            if sc is not None:
                lab.append(LabelSpec(text=txt, comp=sc.name, priority=2))
            else:
                mid = bbox_center(g["boxes"][len(g["boxes"]) // 2])
                lab.append(LabelSpec(text=txt, point=vadd(mid, a_off.get(g["comp"], (0.0, 0.0, 0.0))), priority=2))
        fig = FigureSpec(
            file=self.fig_name("inserts"), title=f"{self.num}  Install the heat-set inserts",
            panels=[
                PanelSpec(subtitle="A  Inserts lifted off their bosses, by group", show=show_a, offsets=a_off,
                          view_dir=self.interior_dir(), focus=[c.name for c in shown], min_extent=0.55 * self.scale, labels=lab,
                          arrow_comps=[c.name for c in shown]),
                PanelSpec(subtitle="B  Seated flush; shell shown transparent", show=show_a,
                          view_dir=self.interior_dir(), focus=[c.name for c in shown], min_extent=0.55 * self.scale,
                          labels=[self.lbl(c, "Top flush with the boss", prio=1) for c in shown[:1]]),
            ],
            footer=FOOTER + " | insert knurls not modeled",
            alt="Heat-set inserts shown above the printed bosses, grouped by where they sit, then seated flush in them.",
            caption="Placement only. The image does not prove the insert is straight or fully melted in; check each one by eye.",
        )
        table = ""
        if groups:
            table = ("| Where | How many | Pressed in along |\n|---|---:|---|\n"
                     + "\n".join(f"| {g['where'].capitalize()} | {g['n']} | {axis_phrase(g['dir'])} |" for g in groups)
                     + f"\n| **Total** | **{sum(g['n'] for g in groups)}** | |\n\n")
        deep = [g for g in groups if g["depth"] and max(g["depth"]) > 40]
        deep_txt = ""
        if deep:
            deep_txt = (" The " + join_and([f"{g['where']} sit about {max(g['depth']):.0f} mm in from the open back" for g in deep])
                        + ": use a long insert tip, and before heating check that the tip reaches each boss square-on.")
        sz = fastener_size(ins[0]) if ins else None
        hole = self.coupon_insert_hole()
        body = (
            f"Fit all {qty} inserts before any electronics go in" + (f" ({sz[0]} × {sz[1]} mm" + (f", into {hole}" if hole else "") + ")" if sz else "")
            + ". Set the iron to the temperature the insert supplier recommends for your print material. Stand each insert on its "
            "boss, then let the hot tip carry it in with light, straight pressure until its top is flush with the boss. Do not push "
            "it below flush, and do not tilt it. Lift the iron straight out and let the boss cool before the next one."
            + deep_txt + "\n\n" + table
            + "Work in a ventilated place and park the iron in its stand between inserts. The tip and the insert stay "
            "hot after you lift the iron."
        )
        self.add(Step(
            id="install-inserts", title="Install the heat-set inserts",
            use=n.use(ins + holders),
            tools=["Soldering iron with a heat-set insert tip", "Tweezers", "A flat metal tool to level each insert"],
            tools_note="A long, slim insert tip helps for the deep bosses." if deep else "",
            body=body,
            expected=f"All {qty} inserts sit square and flush in their bosses, and no boss is cracked or bulged.",
            stop="an insert goes in crooked or sinks below flush, or a boss cracks. Let it cool, photograph it, and ask before using that boss",
            extra_stops=["the iron cannot reach a boss square-on (it would have to tilt): stop; do not force the insert in at an angle"],
            parts=[c.name for c in ins + holders], highlight=[c.name for c in ins], figures=[fig], adds=[c.name for c in ins],
        ))

    def coupon_insert_hole(self) -> Optional[str]:
        for c in self.d.cad_checks():
            if c.get("id") == "fit_coupon":
                ih = ((c.get("data") or {}).get("TEST_FitCoupon") or {}).get("insert_holes") or {}
                if ih.get("d") and ih.get("depth"):
                    return f"Ø{float(ih['d']):g} × {float(ih['depth']):g} mm holes"
        return None

    def insert_axis(self, c: Comp) -> Vec:
        A = self.A
        if A.shell and A.shell.bbox and c.bbox:
            ax = A.open_dir
            sb = A.shell.bbox
            face = sb[3] if ax[1] > 0.5 else sb[4] if ax[2] < -0.5 else None
            if face is not None:
                pos = c.bbox[3] if ax[1] > 0.5 else c.bbox[4]
                if abs(face - pos) < 12.0:
                    return ax
        return (0.0, 0.0, 1.0)

    # ------------------------------------------------------------------ 9. mount the cartridge
    def step_mount_cartridge(self) -> None:
        A, n, d = self.A, self.n, self.d
        cart = A.cartridge
        if not cart:
            return
        scr = A.cartridge_fasteners
        bp = A.backplane
        ax = A.open_dir
        cb = A.bbox(cart)
        dist = max(30.0, 0.55 * (abs(sum(bbox_size(cb)[i] * ax[i] for i in range(3))) if cb else 40.0) + 0.25 * self.scale)
        up = (0.0, 0.0, 1.0) if ax[2] > -0.5 else (0.0, 0.0, -1.0)
        offs = {c.name: vadd(vscale(ax, dist), vscale(up, 0.25 * dist)) for c in cart}
        offs.update({c.name: vadd(vscale(ax, dist), vscale(up, 0.25 * dist + 22.0)) for c in scr})
        show = self.ctx_show()
        show.update({c.name: "focus" for c in cart + scr})
        usb = (connector_dir(A, A.core) or (overhang_dir(A.core, bp) if bp else None)) if A.core else None
        lab_a = []
        if bp:
            lab_a.append(self.lbl(bp, "Core cartridge", f"goes in along {axis_short(vscale(ax, -1))}", prio=3))
        for c in scr:
            lab_a.append(self.lbl(c, f"{n.qty(c)} × {n.short(c)}", prio=2))
        lab_b = []
        usb_key = next((k for k in (anchors_for(A, A.core.ref or A.core.name) if A.core else {}) if "usb" in k.lower()), None)
        if A.core and usb:
            if usb_key:  # anchored to the connector itself, not the module's deepest pixel (MIN-31)
                lab_b.append(LabelSpec(text=f"Core board USB\ntoward {axis_phrase(usb)}", anchor=usb_key, priority=2))
            else:
                lab_b.append(self.lbl(A.core, "Core board USB", f"toward {axis_phrase(usb)}", prio=2))
        pwr = next((k for k in (anchors_for(A, bp.ref or "BP1") if bp else {}) if "PWR" in k.upper()), None)
        if pwr:
            lab_b.append(LabelSpec(text="USB-C power input\nlines up with its opening", anchor=pwr, priority=3))
        for c in scr:
            lab_b.append(self.lbl(c, "Screws snug, board flat", prio=1))
        figs = [FigureSpec(
            file=self.fig_name("mount-cartridge"), title=f"{self.num}  Mount the core cartridge",
            panels=[PanelSpec(subtitle=f"A  Cartridge lifted out along {axis_short(ax)}", show=show, offsets=offs,
                              view_dir=self.interior_dir(), focus=[c.name for c in cart + scr] + self.body_names()[:1], labels=lab_a,
                              arrow_comps=[c.name for c in ([bp] if bp else cart[:1])]),
                    PanelSpec(subtitle="B  Installed; shell shown transparent", show=show, view_dir=self.interior_dir(),
                              focus=[c.name for c in cart], min_extent=0.6 * self.scale, labels=lab_b)],
            footer=FOOTER + " | screw threads not modeled",
            alt="The assembled core cartridge lifted out of the shell, then screwed onto its bosses.",
            caption="Placement only: the image does not prove screw engagement or that the USB-C port lines up. Check both by hand.",
        )] if A.shell else []
        qty = sum(n.qty(c) for c in scr)
        extra_rows = self.unmodelled_screw_rows(r"micro ?sd|sd module|support post|cartridge|backplane|core board")
        body = (
            f"Lower the cartridge onto its {qty or 'mounting'} bosses, component side up"
            + (f", with the core board's USB connectors toward {axis_phrase(usb)}" if usb else "")
            + ". The USB-C power input must line up with its opening in the shell. "
            + (f"Fit the {qty} × **{n.name(scr[0])}** through the board holes into the inserts. " if scr else "Fix it with the supplied screws. ")
            + "Tighten each one a little at a time, in a cross pattern, and stop as soon as it is snug: the board must not bend. "
            "No torque value has been established."
            + "".join("\n\n" + self.unmodelled_line(p) for p in extra_rows)
        )
        self.add(Step(
            id="mount-cartridge", title="Mount the core cartridge",
            use=n.use(scr, extra=["the bench-tested core cartridge"] + [f"{int(p.get('quantity') or 1)} × **{n.row_name(p)}**" for p in extra_rows]),
            tools=["A screwdriver that fits the supplied screws"] if scr else [],
            tools_note="" if scr else "Fingers only.",
            body=body,
            expected="The cartridge sits flat on all its bosses, every screw is snug, and the USB-C power input lines up with its opening.",
            stop="a screw will not start by hand, the board bends or rocks, or the USB-C input does not line up with its opening. Back the screws out and photograph the boss and the opening.",
            parts=[c.name for c in cart + scr] + [p.get("module_id") for p in extra_rows if p.get("module_id")],
            highlight=[c.name for c in cart + scr], figures=figs,
            adds=[c.name for c in cart + scr], rows=[p.get("module_id") for p in extra_rows if p.get("module_id")],
        ))

    # ------------------------------------------------------------------ 10+. modules
    def step_feature(self, g: Group, internal: bool = False) -> None:
        A, n, d = self.A, self.n, self.d
        if not g.module:
            return
        comps = g.comps
        mod = d.lib.get(g.module.module_id or d.instance(g.ref).get("module"))
        mk = mount_kind(mod)
        rk = retention_kind(mk, g)
        normal = g.normal or (0.0, -1.0, 0.0)
        outside = mk in ("panel_nut", "snap_out")
        noun = n.noun(g)
        ctype = str(((mod or {}).get("module_connector") or {}).get("type") or "").lower()
        has_conn = ctype not in ("none",)
        fp = FACE_PART.get(g.role, "front")
        name = n.name(g.module)
        where = f"behind its opening ({g.label})" if g.label else "behind its opening"
        if internal:
            where = "in its place inside the shell"
        face_dir = axis_phrase(normal)
        conn = connector_dir(A, g.module, ("conn", "jst", "cable", "pin", "header")) if has_conn else None
        port_face = self.port_face(g, mod)
        sq = self.group_screw_qty(g)

        # order sub-actions from the outer wall inward along the face normal (MAJ-15)
        def depth(c: Comp) -> float:
            return vdot(bbox_center(c.bbox), normal) if c.bbox else -1e9

        seq: list[str] = []
        for c in sorted(g.extras, key=depth, reverse=True):  # gaskets and pads sit against the wall: first
            rd = self.rings.get(c.name)
            row = d.purchased_row(model_part=c.name) or d.purchased_row(module_id=c.module_id)
            src = f" from the **{n.row_name(row)}**" if row else ""
            if re.search(r"gasket|foam", f"{c.name} {c.label}", re.I):
                seq.append(f"Cut a foam ring{src}" + (f": {ring_text(rd)}" if rd else "") + ".")
                aim = f"around the {fp}" if g.role == "mic" else f"on the wall around the {fp} opening"
                seq.append(f"Peel the ring's backing and stick it to the inside wall {aim}, centred on the opening.")
            else:
                seq.append(f"Fit the **{n.name(c)}** against the wall {where} first.")
        outer_prints = [c for c in g.prints if re.search(r"diffuser|lens|window|bezel|grille", f"{c.name} {c.label}", re.I)]
        clamps = [c for c in g.prints if c not in outer_prints]
        for c in sorted(outer_prints, key=depth, reverse=True):
            seq.append(f"Push the {lc(n.human(c))} (`{c.name}`) into the {g.label.lower() if g.label else noun} opening from inside, "
                       f"its smooth face toward {face_dir}, until it sits flush with the wall.")
        face = f"{fp} toward {face_dir}" if has_conn or g.role in FACE_PART else f"toward {face_dir}"
        fit_line = W.word(g.role, "fit")  # a pack row's own fitting sentence (words.py) replaces the generic one
        if fit_line:
            how = fit_line.replace("{name}", f"**{name}**")
        elif rk == "cable_exit":
            how = (f"Pass the lead of the **{name}** through its cable exit ({g.label}) from outside" if g.label else
                   f"Pass the lead of the **{name}** through its cable exit from outside") + \
                  f", so the {noun} itself stays outside the shell. Do not pull on the lead to seat it."
        elif rk == "snap_out":
            how = (f"From the outside, push the **{name}** straight into its opening, {face}, until its "
                   "clips catch. Check from inside that every clip has caught; do not lever against the cap.")
        elif rk == "panel_nut":
            how = (f"From the outside, pass the **{name}** through its opening so the {fp} {'face' if plural(fp) else 'faces'} {face_dir}. "
                   "From inside, fit its retaining nut or clip and tighten by hand; do not over-tighten the plastic.")
        elif rk in ("clamp", "clamp_screws", "screws"):
            how = f"Seat the **{name}** {where}, {face}" + (f"; {port_face}" if port_face else "") + ". Hold it there."
        elif rk == "adhesive":
            how = f"Peel the backing and press the **{name}** in place {where}, {face}. Hold it firmly; it cannot be repositioned easily."
        elif rk == "press":
            how = f"Press the **{name}** straight into its pocket {where} until it sits flat against the stop, {face}."
        else:
            how = f"Seat the **{name}** {where} so the {fp} {'face' if plural(fp) else 'faces'} {face_dir}. It must sit flat and square to the opening."
        seq.append(how)
        for c in sorted(clamps, key=depth, reverse=True):
            if rk == "cable_exit":
                seq.append(f"Fit the {lc(n.human(c))} (`{c.name}`) on the lead from inside, so a gentle pull on the lead "
                           "outside does not reach its connector.")
                continue
            seq.append(f"Fit the {lc(n.human(c))} (`{c.name}`) over the {noun} from inside, so it presses the {noun} toward {face_dir}"
                       + (" against its gasket" if g.extras else "") + ".")
        for c in g.fasteners:
            q = sq.get(c.name, n.qty(c))
            seq.append(f"Fix it with the {q} × **{n.name(c)}** into the inserts. Tighten gently, a little at a time, and stop as soon as it is snug.")
        lines = [f"{i + 1}. {t}" for i, t in enumerate(seq)]
        me = self.ends.get(g.ref or "")
        if me:
            lines.append("\n**Cable at the module end:** " + me.instruction(name)
                         + (" It was fitted for the bench test; check it is still fully seated." if "bench-test" in self.ids and me.family != "factory_prewired" else ""))
        if conn:
            lines.append(f"Its cable connector points toward {axis_phrase(conn)}; keep that side clear for the cable.")
        if g.role == "speaker":
            lines.append("Do not press on the speaker cone. Keep the speaker leads free toward the cartridge.")
        if g.role == "display":
            lines.append("Hold the display by its edges and do not press on the glass.")
        if g.role == "mic":
            lines.append("Keep the sound port clear: no foam, tape or glue over the hole itself.")
        if g.role == "led":
            lines.append("Do not look directly into the LEDs from close up when they are tested later.")
        care = W.word(g.role, "care")
        if care:
            lines.append(care)
        body = "\n".join(lines[: len(seq)]) + ("\n\n" + "\n\n".join(x.strip() for x in lines[len(seq):]) if lines[len(seq):] else "")

        thick = abs(sum(g.module.size[i] * normal[i] for i in range(3)))
        dist = max(34.0, 1.2 * thick + 26.0)
        ax_in = normal if outside else vscale(normal, -1.0)
        order = sorted(comps, key=depth, reverse=True)
        offs = {c.name: vscale(ax_in, dist * (1.0 + 0.35 * i)) for i, c in enumerate(order)}
        # a retainer the size of its module hides behind it in the exploded view (no dot, dropped label): slide each
        # printed retainer sideways, across the insertion axis, by about its module's width so it shows beside it
        up = (0.0, 0.0, 1.0) if abs(ax_in[2]) < 0.9 else (1.0, 0.0, 0.0)
        side = vnorm((ax_in[1] * up[2] - ax_in[2] * up[1], ax_in[2] * up[0] - ax_in[0] * up[2], ax_in[0] * up[1] - ax_in[1] * up[0]))
        mw = 0.6 * max(float(x) for x in (g.module.size or (20.0, 20.0, 20.0))) + 6.0
        for j, c in enumerate(x for x in order if x in g.prints):
            offs[c.name] = vadd(offs[c.name], vscale(side, mw if j % 2 == 0 else -mw))
        show = self.ctx_show()
        show.update({c.name: "focus" for c in comps})
        # panel A looks from inside: parts fitted earlier (the core cartridge) sit between the camera and this kit and hid
        # its retainer (dropped labels); show them ghosted there, solid in panel B
        show_a = {k: ("ghost" if m == "solid" else m) for k, m in self.ctx_show().items()}
        show_a.update({c.name: "focus" for c in comps})
        lab_a = [self.lbl(g.module, n.short(g.module), f"{fp} toward {axis_short(normal)}", prio=3)]
        # inside one module's step the retainer needs no "<kind>: <feature label>" suffix (that is for the parts list):
        # "Clamp strap" fits where "Clamp strap: Tap: next scene. Hold: off" was dropped for lack of room
        for c in g.extras:
            lab_a.append(self.lbl(c, n.human(c).split(": ", 1)[0], prio=2))
        for c in g.prints:
            lab_a.append(self.lbl(c, n.human(c).split(": ", 1)[0], prio=2))
        for c in g.fasteners:
            lab_a.append(self.lbl(c, f"{sq.get(c.name, n.qty(c))} × {n.short(c)}", prio=1))
        lab_b = [self.lbl(g.module, (g.label or noun.capitalize()), f"{fp} {'face' if plural(fp) else 'faces'} {face_dir}", prio=3)]
        conn_key = next((k for k in anchors_for(A, g.module.ref or g.module.name)
                         if any(w in k.lower() for w in ("conn", "jst", "cable", "pin", "header"))), None)
        if conn and conn_key and not outside:
            lab_a.append(LabelSpec(text=f"Cable connector\ntoward {axis_short(conn)}", anchor=conn_key, priority=1))
        out_view = vnorm(vadd(normal, (0.35, 0.0, 0.4) if abs(normal[2]) < 0.5 else (0.3, -0.5, 0.0)))
        in_view = self.interior_dir()
        focus = [c.name for c in comps]
        figs = []
        if A.shell:
            figs.append(FigureSpec(
                file=self.fig_name(g.key), title=f"{self.num}  Fit the {(g.label or noun).lower() if g.label else noun}",
                panels=[PanelSpec(subtitle=("A  Inserted from outside" if outside else f"A  From inside along {axis_short(normal)}, outer part first"),
                                  show=show if outside else show_a, offsets=offs, view_dir=out_view if outside else in_view, focus=focus,
                                  min_extent=0.42 * self.scale, labels=lab_a, arrow_comps=[c.name for c in order]),
                        PanelSpec(subtitle="B  In place, seen from outside; shell transparent" if not internal else "B  In place; shell transparent",
                                  show=show, view_dir=out_view if not internal else in_view, focus=focus, min_extent=0.42 * self.scale,
                                  labels=lab_b)],
                footer=FOOTER + f" | {noun} details simplified",
                alt=f"The {noun} and its parts shown moving into position in fitting order, then installed {('behind ' + g.label) if g.label else 'in the shell'}.",
                caption=f"Shows where the {noun} sits and which way it faces. It does not prove fit or retention; check it by hand.",
            ))
        title = f"Fit the {noun}" + (f" ({g.label})" if g.label and g.label.lower() != noun else "")
        stops = list(me.stop_rules(name)) if me else []
        cable_rows = [p.get("module_id") for p in self.harness_rows() if g.ref in (p.get("refs") or []) and p.get("module_id")]
        self.add(Step(
            id=f"mount-{g.key}", title=title,
            use=n.use(order, qty=sq) + (" Its cable stays fitted from the bench test." if me and "bench-test" in self.ids else ""),
            tools=(["A screwdriver that fits the supplied screws"] if g.fasteners else [])
                  + (["Sharp scissors or a craft knife and a ruler"] if any(re.search(r"gasket|foam", f"{c.name} {c.label}", re.I) for c in g.extras) else []),
            tools_note="" if (g.fasteners or g.extras) else "Fingers only.",
            body=body,
            expected=(f"The {noun} stays outside the shell, its lead passes the cable exit and is held from inside, and a gentle pull "
                      "on the lead outside does not move anything inside." if rk == "cable_exit" else
                      f"The {noun} sits square {('behind its opening' if not internal else 'in its place')} with the {fp} toward {face_dir}, held without force, and its cable is free."),
            stop=(f"the lead does not pass the cable exit without force, or it slips when pulled gently. Photograph the exit from inside and outside before changing anything; do not drill, cut or glue"
                  if rk == "cable_exit" else
                  f"the {noun} does not sit flat, the {fp} {'are' if plural(fp) else 'is'} not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue"),
            extra_stops=stops,
            parts=[c.name for c in order] + cable_rows, highlight=[c.name for c in comps], figures=figs,
            adds=[c.name for c in comps], rows=cable_rows, module_end=[g.ref] if me else [],
            extra_images=[f"manual/cable-end-{g.ref.lower()}.png"] if me and g.ref else [],
        ))

    def port_face(self, g: Group, mod: dict) -> str:
        """Which face of the board carries the sound port, when the library says so (MIN-32: INMP441 is bottom-ported)."""
        notes = str(((mod or {}).get("mount") or {}).get("notes") or "") if isinstance((mod or {}).get("mount"), dict) else ""
        if g.role == "mic" and re.search(r"bottom-ported|bottom ported", notes, re.I):
            return ("the board is bottom-ported: its sound hole goes through the board, so the plain side of the board (without "
                    "the microphone can) faces the wall and the hole sits over the opening")
        return ""

    # ------------------------------------------------------------------ plug
    def step_plug(self) -> None:
        A, n, d = self.A, self.n, self.d
        conns = []
        for inst in d.instances:
            slot = inst.get("slot")
            ref = inst.get("ref")
            if not slot or not ref or slot in ("U1",) or ref == (d.electrical.get("core") or {}).get("ref"):
                continue
            h = inst.get("harness") or {}
            kind = (d.backplane.slots[slot].kind if d.backplane and slot in d.backplane.slots else "")
            if kind in ("socket", "power") or not h:
                continue
            conns.append((inst, h))
        lines = []
        hl = [A.backplane.name] if A.backplane else []
        parts = list(hl)
        used = []
        for inst, h in conns:
            c = d.comp_for_ref(inst["ref"])
            nm = n.name(c) if c else d.module_name(inst["ref"])
            row = next((p for p in self.harness_rows() if inst["ref"] in (p.get("refs") or [])), None)
            desc = n.row_name(row) if row else (h.get("cable") or "its cable")
            length = h.get("length_mm")
            if length and str(int(length)) not in desc:
                desc += f", {int(length)} mm"
            wires = h.get("wires") or []
            order = ", ".join(f"{w.get('slot_pin')} {w.get('color')} {w.get('module_pin')}" for w in sorted(wires, key=lambda w: w.get("slot_pin") or 0) if w.get("color"))
            lines.append(f"**{inst['slot']}** ← {nm} ({inst['ref']}): {desc}." + (f" Pin order at the port: {order}." if order else "")
                         + ("" if c else f" The {nm} has no printed mount in this design, so nothing holds it: lay it flat inside "
                                         "the shell, clear of the cartridge, the openings and every other module (not modelled; "
                                         "physical check)."))
            used.append(inst["slot"])
            if c:
                parts.append(c.name)
                hl.append(c.name)
        rows = [p.get("module_id") for p in self.harness_rows() if p.get("module_id")]
        parts += [c.name for c in A.harness_parts] + rows
        hl += [c.name for c in A.cables]
        look = lookalike_ports(d, used)
        if look:
            keyed = ("Every port plug is keyed, so it only goes in one way round, but "
                     + join_and([f"ports {join_and(gp)} have the same connector and pin count, so their leads fit either port"
                                 for gp in look])
                     + ". Tell those leads apart by the module at the other end and by their length and wire colours in the "
                     "list below, and read the port name printed next to each header before plugging in.")
        else:
            keyed = "Every port plug is keyed, and no two ports used here share a connector family and pin count, so each lead fits only its own port."
        body = (
            "Disconnect USB-C power first. " + keyed + " Line up the ridge on the plug with the slot in the header, then push "
            "straight in until it seats fully. Never pull a cable by its wires; hold the plug body.\n\n"
            + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
            + "\n\nThe pin order is listed from pin 1. Wire colours are a cross-check, not the identifier: "
            "read the port name on the board. The full table is in the wiring diagram."
        )
        stops = []
        if look:
            stops.append("a lead is in a look-alike port (" + "; ".join(", ".join(gp) for gp in look) + "): check the printed port "
                         "name against this list before any power is connected")
        self.add(Step(
            id="plug-harnesses", title="Plug the cables into their ports",
            use=f"{len(conns)} module cable{'s' if len(conns) != 1 else ''}, already fitted on the modules"
                + (" at the bench test" if "bench-test" in self.ids else "") + ".",
            tools=[], tools_note="Fingers only.",
            body=body,
            expected="Every cable is fully seated in the port named in the wiring table, and no port has the wrong cable.",
            stop="a plug will not go in without force, a cable's colours differ from the table, or a port named here is missing on your board. Photograph both ends; do not re-pin a housing or swap wires",
            extra_stops=stops,
            parts=parts, highlight=hl, extra_images=["manual/ports.png", "manual/wiring.png"],
            adds=[c.name for c in A.cables + A.harness_parts], rows=rows,
        ))

    # ------------------------------------------------------------------ route and tie
    def tie_anchor_keys(self) -> list[str]:
        return [k for k in self.d.anchors if re.search(r"(^|\.)TIE\d*$|tie_?anchor", k, re.I)]

    def step_route(self) -> None:
        A, n, d = self.A, self.n, self.d
        ties = A.ties
        tie_rows = self.tie_rows()
        n_ties = sum(n.qty(c) for c in ties) or sum(int(p.get("quantity") or 0) for p in tie_rows)
        tie_name = n.name(ties[0]) if ties else (n.row_name(tie_rows[0]) if tie_rows else None)
        anchors = self.tie_anchor_keys()
        n_anchor = (d.cad_summary().get("info") or {}).get("tie_anchors")
        rows, per = [], []
        routes = [r for r in d.routes if r.length_mm]
        for r in routes:
            ref = next((x.split(".", 1)[0] for x in (r.to, r.frm) if x and not x.startswith("BP")), None)
            inst = d.instance(ref) if ref else {}
            L = (inst.get("harness") or {}).get("length_mm") or cable_len_mm(r.cable)
            row = next((p for p in self.harness_rows() if ref and ref in (p.get("refs") or [])), None)
            nm = (n.row_name(row) if row else r.cable or r.id)
            mod = d.module_name(ref) if ref else ""
            if L:
                ex = float(L) - float(r.length_mm)
                near = self.nearest_anchor(r, anchors)
                if ex > 25:
                    runs = max(2, math.ceil(ex / FOLD_MM))
                    where = (f"at tie anchor {near}" if near else
                             (f"at the nearest of the {n_anchor} printed tie anchors on the floor" if n_anchor else "near the cartridge end"))
                    act = (f"fold the extra {ex:.0f} mm into a flat Z-fold of {runs} runs (suggested fold length {FOLD_MM} mm, not a CAD dimension) close to the cartridge "
                           f"end and hold it {where} with 1 tie")
                else:
                    act = "no fold needed; tie it once at the nearest anchor" if near or n_anchor else "no fold needed"
                per.append(ex)
                rows.append(f"| {r.frm or '?'} → {ref or r.to or '?'} ({mod}) | {nm} | {int(L)} mm | {float(r.length_mm):.0f} mm | {ex:.0f} mm | {act} |")
        table = ("| Cable | Part | Length | CAD route | Extra | What to do with it |\n|---|---|---:|---:|---:|---|\n" + "\n".join(rows)) if rows else ""
        n_used = len(rows)
        spare = (n_ties - n_used) if n_ties else 0
        body = (
            "Lay each cable along the path shown, without twisting it, and leave a little slack at both plugs so no "
            "plug is pulled sideways. Keep cables away from screw bosses, the closing seam and the speaker cone.\n\n"
            + (table + "\n\n" if table else "")
            + (f"Use 1 **{tie_name}** per cable ({n_used} of the {n_ties} supplied" + (f"; {spare} spare" if spare > 0 else "") + "): pass it through "
               + ("the printed tie anchor" if n_anchor or anchors else "a tie point") + ", round the fold, pull it snug (not tight) and cut the tail "
               "flush with the flush cutters. Keep the cut tails for the bin, not the gadget." if tie_name else "")
            + ("" if anchors else (f"\n\nThe {n_anchor} tie anchors are moulded into the shell floor; their exact positions are not exported "
                                   "with this build, so they are not labelled in the picture." if n_anchor else ""))
            + "\n\nThe routes are display envelopes. Cable stiffness and plug housings are not modeled."
        )
        show = self.ctx_show()
        show.update({c.name: "focus" for c in A.cables + ties})
        labels = []
        for c in A.cables[:7]:
            r = next((r for r in d.routes if r.component == c.name or r.id == c.name), None)
            ref = c.ref or (r.to.split(".")[0] if r and r.to else "")
            labels.append(self.lbl(c, d.module_name(ref) if ref else (c.label or camel_words(c.name)),
                                   f"to {r.frm.split('.', 1)[-1]}" if r and r.frm else "", prio=1))
        for k in anchors:
            labels.append(LabelSpec(text=f"Tie anchor {k.split('.')[-1]}", anchor=k, priority=2))
        drawn = "cable routes" + (" and the tie anchors labelled" if anchors else "")
        figs = [FigureSpec(
            file=self.fig_name("routing"), title=f"{self.num}  Route and tie the cables",
            panels=[PanelSpec(subtitle="Open side view", show=show, view_dir=self.interior_dir(),
                              focus=self.body_names() or [c.name for c in A.cables], labels=labels, label_room=0.26)],
            footer="Cable shapes are display envelopes only | check real slack, bends and plug access",
            alt=f"The open gadget with every {drawn}.",
            caption="Routes show occupied space, not the exact cable path. Check slack and that nothing crosses the seam.",
        )] if A.shell and (A.cables or ties) else []
        self.add(Step(
            id="route-cables", title="Route and tie the cables",
            use=join_and(([f"{n_ties} × **{tie_name}**"] if tie_name else []) + ["the cables plugged in the previous step"]) + ".",
            tools=["Flush cutters for the tie tails"] if tie_name else [],
            tools_note="" if tie_name else "Fingers only.",
            body=body,
            expected="Every cable follows its route with slack at both plugs, its extra length is folded and tied, nothing crosses the seam or a screw boss, and no plug is pulled sideways.",
            stop="a cable is too short to reach without pulling on its plug, or it has to cross the seam. Photograph the route; do not extend or cut a cable.",
            parts=[c.name for c in A.cables + ties + A.harness_parts] + [p.get("module_id") for p in tie_rows if p.get("module_id")]
                  or ([A.backplane.name] if A.backplane else []),
            highlight=[c.name for c in A.cables + ties], figures=figs, adds=[c.name for c in ties],
            rows=[p.get("module_id") for p in tie_rows if p.get("module_id")],
        ))

    def nearest_anchor(self, r, keys: list[str]) -> Optional[str]:
        if not keys or not r.points:
            return None
        p0 = r.points[0]
        k = min(keys, key=lambda k: math.dist(self.d.anchors[k], p0))
        return k.split(".")[-1]

    # ------------------------------------------------------------------ re-check before closing
    def step_flash(self) -> None:
        A, n, d = self.A, self.n, self.d
        mods = [g for g in [*A.features, *A.internals] if g.module]
        checks = dedupe([SELFTEST_CHECK.get(g.role, f"the {n.noun(g)} responds") for g in mods])
        port, uart_stop = self.uart_text()
        how, exp = self.selftest_text()
        usb_row = self.usb_cable_row()
        usb = connector_dir(A, A.core) if A.core else None
        body = (
            "Keep the gadget open. Everything is now mounted, so re-run the self-test to catch a cable that came loose. "
            f"Connect the computer to {port}" + (f" (it faces {axis_phrase(usb)})" if usb else "") + " with the "
            + (f"**{n.row_name(usb_row)}**" if usb_row else "USB-C data cable") + ". The firmware is already on the board from the "
            f"bench test (step {self.ids.get('bench-test', '?')}); flash again only if you changed it, the same way: "
            + ("`sha256sum -c SHA256SUMS` in `firmware/` first, then the Flash button or esptool with `firmware-merged.bin`.\n\n"
               if self.selftest.get("sha256sums") else "the Flash button or esptool.\n\n")
            + f"{how} Check each module in turn:\n\n" + "\n".join(f"- {c}" for c in checks)
            + f"\n\n{exp} Compare it with the line you recorded at the bench test.\n\n"
            "Unplug the USB cable before you close the gadget. These are checks for you to make; nothing here records them as passed."
        )
        show = self.ctx_show()
        labels = []
        if A.core:
            ak = next((k for k in anchors_for(A, A.core.ref or A.core.name) if "usb" in k.lower()), None)
            labels.append(LabelSpec(text="Core board UART port" if "UART" in port else "Core board USB port", anchor=ak, priority=3)
                          if ak else self.lbl(A.core, "Core board", "USB port for flashing", prio=3))
            show[A.core.name] = "focus"
        if A.backplane:
            pk = next((k for k in anchors_for(A, A.backplane.ref or "BP1") if "PWR" in k.upper()), None)
            if pk:
                labels.append(LabelSpec(text="USB-C power input", anchor=pk, priority=2))
        seen_ref = set()
        for g in mods[:6]:
            if g.ref in seen_ref:
                continue
            seen_ref.add(g.ref)
            labels.append(self.lbl(g.module, (g.label or n.noun(g).capitalize()), prio=2))
        figs = [FigureSpec(
            file="manual/open-back.png", title="The open gadget, ready for the re-check",
            panels=[PanelSpec(subtitle="Everything installed and plugged in; closing part off", show=show, view_dir=self.interior_dir(),
                              focus=self.body_names() or list(show), labels=labels, label_room=0.26)],
            footer=FOOTER,
            alt="The open gadget with the core board's port and each module labelled.",
            caption="Placement view. It does not show that the firmware runs or that any module works.",
        )] if A.shell else []
        parts = [c.name for c in [A.core, A.backplane] if c] + [g.module.name for g in mods]
        self.add(Step(
            id="flash-and-test", title="Re-run the self-test with everything mounted",
            use=f"The open gadget from the previous steps and 1 × **{n.row_name(usb_row)}**." if usb_row else "The open gadget from the previous steps and a USB-C data cable.",
            tools=["A computer with a serial monitor"],
            body=body,
            expected="The self-test summary line again shows `\"ok\":true` and `\"failed\":0`, and every module behaves as listed.",
            stop="the board resets repeatedly, any part gets warm, or a module that passed the bench test now fails. Unplug USB first, then check that module's cable at both ends against the wiring table",
            extra_stops=[uart_stop] if uart_stop else [],
            parts=parts + ([usb_row.get("module_id")] if usb_row and usb_row.get("module_id") else []),
            highlight=[A.core.name] if A.core else parts, figures=figs,
            rows=[usb_row.get("module_id")] if usb_row and usb_row.get("module_id") else [],
        ))

    # ------------------------------------------------------------------ close
    def step_close(self) -> None:
        A, n, d = self.A, self.n, self.d
        cov = A.covers
        scr = A.cover_fasteners
        feet = A.feet
        ax = A.open_dir
        depth = abs(sum(self.shell_size[i] * ax[i] for i in range(3))) or self.scale
        offs = {c.name: vscale(ax, 0.75 * depth) for c in cov}
        offs.update({c.name: vscale(ax, 0.75 * depth + 30.0) for c in scr})
        show = self.ctx_show(ghost_body=False)
        for c in A.cables:
            show[c.name] = "solid"
        show.update({c.name: "focus" for c in cov + scr})
        lab_a = [self.lbl(c, n.human(c), f"fits along {axis_short(vscale(ax, -1))}", prio=3) for c in cov]
        for c in scr:
            lab_a.append(self.lbl(c, f"{n.qty(c)} × {n.short(c)}", prio=2))
        if A.shell:
            lab_a.append(self.lbl(A.shell, "Keep every cable", "inside the seam", prio=1))
        lab_b = [self.lbl(c, "Seam closed evenly", prio=2) for c in cov]
        for c in scr:
            lab_b.append(self.lbl(c, "Tighten gently, in stages", prio=1))
        view = vnorm(vadd(vscale(ax, 1.0), (-0.6, 0.0, 0.55))) if ax[1] > 0.5 else vnorm(vadd(vscale(ax, 1.0), (0.5, -0.7, 0.0)))
        info = d.cad_summary().get("info") or {}
        usb_op = (info.get("usb_c") or {}).get("opening") or {}
        feet_xy = info.get("feet") or []
        foot_txt = ""
        view_b = vnorm((view[0], view[1], -0.6)) if feet else view
        label_rows = self.label_rows()
        if feet:
            show_b = dict(show)
            show_b.update({c.name: "focus" for c in feet})
            q = sum(n.qty(c) for c in feet)
            pos = ""
            if feet_xy:
                xs = sorted({round(abs(p[0]), 1) for p in feet_xy})
                ys = sorted({round(p[1], 1) for p in feet_xy})
                pos = (f", centred at x ±{xs[0]:g} mm" if len(xs) == 1 else f", centred at x ±{min(xs):g} to ±{max(xs):g} mm")
                pos += f" and y {ys[0]:+g} to {ys[-1]:+g} mm from the centre of the base"
                fb = feet[0].bbox
                if fb:
                    pos += f" (outer edges at x ±{max(abs(fb[0]), abs(fb[1])):.1f} mm)"
            foot_txt = (f"\n\nTurn it over and stick the {q} × **{n.name(feet[0])}** on the base{pos}, on a clean, dry surface, "
                        "as the B panel shows.")
            lab_b.append(self.lbl(feet[0], f"{q} × {n.short(feet[0])}", prio=1))
        else:
            show_b = show
        if label_rows:
            lib = d.lib.get(label_rows[0].get("module_id")) or {}
            where = "on the base" if re.search(r"\bbase\b", str((lib.get("mount") or {}).get("notes") if isinstance(lib.get("mount"), dict) else "")) else "on a flat, clean area of the closing part"
            foot_txt += (f"\n\nStick the **{n.row_name(label_rows[0])}** {where}, clear of the feet, screws and openings. "
                         "Keep its packing kit for shipping.")
        figs = [FigureSpec(
            file=self.fig_name("close"), title=f"{self.num}  Close the {camel_words(cov[0].name)}",
            panels=[PanelSpec(subtitle=f"A  Separated along {axis_short(ax)}", show=show, offsets=offs, view_dir=view,
                              focus=[c.name for c in cov] + self.body_names(), labels=lab_a, arrow_comps=[c.name for c in cov]),
                    PanelSpec(subtitle="B  Closed" + ("; seen from below to show the feet" if feet else ""), show=show_b,
                              view_dir=view_b, focus=[c.name for c in cov] + self.body_names(), labels=lab_b)],
            footer=FOOTER + " | check cable clearance and screw access on the real print",
            alt="The closing part separated from the shell with its screws, then closed" + (" and the feet seen from below." if feet else "."),
            caption="The image does not prove that the seam closes without pinching a cable. Close it by hand first.",
        )] if A.shell else []
        q = sum(n.qty(c) for c in scr)
        usb_txt = ""
        if usb_op.get("w") and usb_op.get("h"):
            usb_txt = (f" Its {float(usb_op['w']):g} × {float(usb_op['h']):g} mm USB-C opening must line up with the power input on "
                       "the backplane; the USB-C power cord is fed through it later, from outside.")
        body = (
            "Check that the USB cable from the self-test is unplugged and that no cable lies across the seam. "
            f"Bring `{cov[0].name}` straight on along {axis_phrase(vscale(ax, -1))}.{usb_txt} It should seat evenly by hand, "
            "without pressure. "
            + (f"Fit the {q} × **{n.name(scr[0])}** and tighten them a little at a time, in a cross pattern, until snug. "
               "Never use the screws to pull the seam shut. No torque value has been established." if scr else "")
            + foot_txt
        )
        self.add(Step(
            id="close-cover", title=f"Close the {camel_words(cov[0].name)}",
            use=n.use(cov + scr + feet, extra=[f"1 × **{n.row_name(p)}**" for p in label_rows]),
            tools=["A screwdriver that fits the supplied screws"] if scr else [],
            tools_note="" if scr else "Fingers only.",
            body=body,
            expected="The seam is closed evenly all round, no cable is pinched, the USB-C opening lines up with the power input, and the gadget stands level on its feet.",
            stop="the part rocks, a gap stays open, or it needs screw force to close. Open it again and look for a cable, a plug or a print edge in the way; photograph it if you cannot find it.",
            parts=[c.name for c in cov + scr + feet] + [p.get("module_id") for p in label_rows if p.get("module_id")],
            highlight=[c.name for c in cov + scr + feet], figures=figs,
            adds=[c.name for c in cov + scr + feet], rows=[p.get("module_id") for p in label_rows if p.get("module_id")],
        ))

    # ------------------------------------------------------------------ final
    def step_final(self) -> None:
        A, n, d = self.A, self.n, self.d
        show = {c.name: "solid" for c in d.comps.values() if c.kind in ("PRINT", "PURCHASE")}
        labels = []
        sh = A.shell
        seen_ref = set()
        for g in A.features[:6]:
            if not g.module or g.ref in seen_ref:
                continue
            seen_ref.add(g.ref)
            nrm = g.normal or (0.0, -1.0, 0.0)
            origin = vadd(g.module.center, vscale(nrm, 400.0))
            labels.append(LabelSpec(text=(g.label or n.noun(g).capitalize()), comp=None if sh else g.module.name, priority=2,
                                    ray=([sh.name] + [c.name for c in g.prints] + [g.module.name], origin, vscale(nrm, -1.0), g.module.name) if sh else None))
        figs = [FigureSpec(
            file="manual/assembled.png", title=f"{self.d.product_name}: assembled",
            panels=[PanelSpec(subtitle="Front view: +X (the gadget's left) is on your right", show=show, view_dir=FRONT_34,
                              focus=list(show), labels=labels, label_room=0.24)],
            footer=FOOTER,
            alt=f"The finished {self.d.product_name} from the front, with each control and opening labelled.",
            caption="Saved CAD appearance only. Complete the final checks below before regular use.",
        )] if sh else []
        supply = (self.d.spec.get("electronics") or {}).get("power") or {}
        adapters = self.adapter_rows()
        usb_row = self.usb_cable_row()
        sup = (f"the **{n.row_name(adapters[0])}** (the 5 V 3 A USB-C adapter in the box)" if adapters
               else "a USB-C 5 V 3 A supply" + (" (not included)" if not supply.get("adapter_in_box") else ""))
        body = (
            f"Plug {sup} into the wall and the " + (f"**{n.row_name(usb_row)}**" if usb_row else "USB-C cord")
            + " through the USB-C opening into the gadget's power input. "
            + ("A mains adapter sold in India must be registered under BIS CRS (the Compulsory Registration Scheme) and carry its "
               "R-number on the label: check for it before use. " if adapters and str(self.d.spec.get("region") or "IN").upper() == "IN" else "")
            + "Keep the supply and its plug outside the gadget, and route the cord where nobody trips on it"
            + (" and away from a bed" if self.child() else "") + ".\n\n"
            "Work through the final checks listed at the end of this guide. They are actions for you, not passed "
            "results: record what you observe. Do not leave a gadget that fails a check in use."
        )
        self.add(Step(
            id="final-checks", title="Final checks",
            use=f"The finished {self.d.product_name}, " + (f"1 × **{n.row_name(usb_row)}** and " if usb_row else "") + f"{sup}.",
            tools=[], tools_note="None" + (" (adult supervision)." if self.child() else "."),
            body=body,
            expected="Every final check is done and recorded, and each one behaves as described.",
            stop="any check fails, the gadget gets hot, smells, rattles or tips over. Unplug it and do not use it until the cause is found.",
            parts=[c.name for c in A.body + A.covers] + [p.get("module_id") for p in adapters + ([usb_row] if usb_row else []) if p.get("module_id")],
            highlight=[c.name for c in A.body + A.covers], figures=figs,
            rows=[p.get("module_id") for p in adapters + ([usb_row] if usb_row else []) if p.get("module_id")],
        ))

    # ------------------------------------------------------------------ wall mount (catalog package D, hand-off d-to-a #3)
    def wall_mount(self) -> dict:
        """The CAD's wall-mount record (cad-summary info.split_info.mount, written by D's rear cover), else {}."""
        m = ((self.d.cad_summary().get("info") or {}).get("split_info") or {}).get("mount") or {}
        return m if isinstance(m, dict) and m.get("kind") == "wall" and m.get("keyholes") else {}

    def wall_rows(self) -> list[dict]:
        """The wall screw + plug set the parts list carries (a purchased row with no CAD part)."""
        return [p for p in self.d.purchased() if re.search(r"^WALL_|wall plug|wall screw", f"{p.get('module_id') or ''} {p.get('name') or ''}", re.I)]

    def step_hang(self) -> None:
        """Hang it on the wall: every number comes from the CAD's mount record; screw pull-out is not modelled."""
        n, m = self.n, self.wall_mount()
        rows = self.wall_rows()
        kh = m.get("keyholes") or []
        hang_z = min(float(k["hang"][1]) for k in kh if isinstance(k.get("hang"), (list, tuple))) if kh else None
        sp, so = m.get("spacing_mm"), m.get("screw_standoff_mm")
        slot = (m.get("keyhole_mm") or {}).get("slot_len")
        notch = m.get("plug_notch") or {}
        what = f"the **{n.row_name(rows[0])}**" if rows else "two wall screws in wall plugs"
        lines = [
            (f"Mark two points {float(sp):g} mm apart on a level line, where the screws will go." if sp else
             "Mark the two screw points on a level line, as far apart as the two keyholes.")
            + (f" The gadget hangs below them: its bottom edge ends up {hang_z:g} mm below the marks." if hang_z is not None else ""),
            f"Drill a hole for a wall plug at each mark, tap the plugs in, and drive the screws ({what}) in"
            + (f" until each head stands {float(so):g} mm off the wall." if so else " until each head stands just off the wall."),
        ]
        if notch:
            lines.append("Plug a right-angle (L-shaped) USB-C cable into the gadget first: the plug sits in the notch under the USB-C "
                         "opening and the cord runs out under the gadget. A straight plug would push the gadget off the wall.")
        lines.append("Put the round ends of the two keyholes over the screw heads, push the gadget flat to the wall and lower it"
                     + (f" {float(slot):g} mm" if slot else "") + " until it rests on the screws.")
        lines.append("Screw pull-out and the wall are not modelled: pull gently down and out before you let go. Physical check "
                     "still needed.")
        body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
        self.add(Step(
            id="hang-on-wall", title="Hang it on the wall",
            use=f"The closed {self.d.product_name}" + (f" and {n.row_name(rows[0])}" if rows else " and two wall screws with wall plugs") + ".",
            tools=["Pencil and a spirit level", "Drill with a bit that fits the wall plugs", "A screwdriver that fits the wall screws"],
            body=body,
            expected="Both screw heads sit in the narrow ends of the keyholes, the gadget is flat against the wall and level, and it does "
                     "not lift off when pulled gently down and out.",
            stop="a plug turns in its hole, a screw head does not pass the keyhole, or the gadget tips away from the wall. Take it down; "
                 "do not leave it hanging",
            parts=[c.name for c in self.A.body + self.A.covers] + [p.get("module_id") for p in rows if p.get("module_id")],
            highlight=[c.name for c in self.A.covers or self.A.body],
            rows=[p.get("module_id") for p in rows if p.get("module_id")],
        ))

    # ------------------------------------------------------------------ printed-only (catalog package A)
    def feet_position(self, feet: list[Comp]) -> str:
        """Where the feet go, from the CAD's saved foot points (cad-summary info.feet) and the feet component box."""
        info = self.d.cad_summary().get("info") or {}
        feet_xy = info.get("feet") or []
        if not feet_xy:
            return ""
        xs = sorted({round(abs(p[0]), 1) for p in feet_xy})
        ys = sorted({round(p[1], 1) for p in feet_xy})
        pos = (f", centred at x ±{xs[0]:g} mm" if len(xs) == 1 else f", centred at x ±{min(xs):g} to ±{max(xs):g} mm")
        return pos + f" and y {ys[0]:+g} to {ys[-1]:+g} mm from the centre of the base"

    def step_feet(self) -> None:
        A, n = self.A, self.n
        feet = A.feet
        q = sum(n.qty(c) for c in feet)
        show = self.ctx_show(ghost_body=False)
        show.update({c.name: "focus" for c in feet})
        figs = [FigureSpec(
            file=self.fig_name("feet"), title=f"{self.num}  Fit the feet",
            panels=[PanelSpec(subtitle="Seen from below", show=show, view_dir=BELOW_34, focus=[c.name for c in feet] + self.body_names(),
                              labels=[self.lbl(feet[0], f"{q} × {n.short(feet[0])}", "in the round seats", prio=2)])],
            footer=FOOTER, alt=f"The printed part seen from below with the {q} feet in their round seats.",
            caption="Shows where the feet go. It does not prove they stick; press each one firmly.",
        )] if A.shell else []
        self.add(Step(
            id="fit-feet", title="Fit the feet",
            use=n.use(feet), tools=[], tools_note="Fingers only.",
            body=(f"Turn the part over onto a soft cloth. Wipe the base clean and dry, then stick the {q} × **{n.name(feet[0])}** into "
                  f"the {q} shallow round seats on the base{self.feet_position(feet)}. Press each one for a few seconds."),
            expected="Every foot sits flat in its seat and the part stands level on the table without rocking.",
            stop="a foot will not stick or the part rocks. Clean the seat with a dry cloth and use a new foot; do not glue it",
            parts=[c.name for c in feet], highlight=[c.name for c in feet], figures=figs, adds=[c.name for c in feet],
        ))

    def step_final_printed(self) -> None:
        A, n, d = self.A, self.n, self.d
        show = {c.name: "solid" for c in d.comps.values() if c.kind in ("PRINT", "PURCHASE")}
        sh = A.shell
        figs = [FigureSpec(
            file="manual/assembled.png", title=f"{d.product_name}: finished",
            panels=[PanelSpec(subtitle="Front view", show=show, view_dir=FRONT_34, focus=list(show), labels=[], label_room=0.24)],
            footer=FOOTER, alt=f"The finished {d.product_name} from the front.",
            caption="Saved CAD appearance only. Complete the final checks below before regular use.",
        )] if sh else []
        pr = d.spec.get("printed") if isinstance(d.spec.get("printed"), dict) else {}
        info = (d.cad_summary().get("info") or {}).get("printed") or {}
        mat = ""
        try:
            mat = str((d.slice_profile() or {}).get("filament_type") or "").split(";")[0].strip()
        except Exception:  # noqa: BLE001
            mat = ""
        lines = [f"This design has no electronics: there is nothing to plug in, charge or switch on."]
        lines.append(f"Printed {mat or 'plastic'} is not food-safe and not for hot liquids: do not use it for food or drink, even if it "
                     "is shaped like a cup, bowl or teapot.")
        if info.get("drain_hole") or pr.get("drain_hole"):
            lines.append("Water runs out of the drain hole: stand it on a saucer or tray if it holds a plant.")
        lines.append("No load or fit is promised: nothing here was tested for weight, and how it fits anything else is a physical "
                     "check.")
        if self.child():
            lines.append("Small printed parts can be a choking hazard: an adult checks it is whole before a child uses it.")
        body = "\n\n".join(lines) + ("\n\nWork through the final checks listed at the end of this guide. They are actions for you, not "
                                      "passed results: record what you observe.")
        self.add(Step(
            id="final-checks", title="Final checks",
            use=f"The finished {d.product_name}.", tools=[], tools_note="Adult supervision." if self.child() else "",
            body=body,
            expected="Every final check is done and recorded, and the part stands level and whole.",
            stop="a part is cracked, has sharp edges or rocks on the table. Do not use it until it is reprinted",
            parts=[c.name for c in A.body + A.feet], highlight=[c.name for c in A.body], figures=figs,
        ))

    def child(self) -> bool:
        aud = self.d.spec.get("audience") or {}
        flags = (self.d.spec.get("safety") or {}).get("flags") or []
        return bool(aud.get("child_directed")) or "CHILD_DIRECTED" in flags


# --------------------------------------------------------------------------------------------- print data helpers
SUPPORT_FRAG = re.compile(r"(;\s*|,\s*|^)(no supports?|supports?[^;]*|tree supports?[^;]*)(?=;|$)", re.I)


def print_notes(p: dict) -> list[str]:
    """Weight, time, the slicer's support flag and the orientation note without its own support claims (MIN-13)."""
    bits = []
    g = p.get("grams")
    if g is not None:
        g = float(g)
        bits.append("<1 g (slicer estimate)" if g < 1 else f"about {round(g)} g (slicer estimate)")
    if p.get("hours"):
        bits.append(f"about {float(p['hours']):.1f} h (slicer estimate)")
    if p.get("supports") is not None:
        sg = p.get("support_g")
        bits.append("slicer adds supports" + (f" (about {float(sg):.1f} g)" if sg else "") if p["supports"] else "no supports (slicer)")
    note = str(p.get("orientation_note") or "")
    if note:
        note = SUPPORT_FRAG.sub("", note).strip(" ;,")
        if note:
            bits.append(note)
    return bits


def print_settings(data) -> str:
    """The process settings line, read from print/slice.ini (MIN-13)."""
    ini = data.slice_profile()
    if not ini:
        return ""
    st = data.slice_stats()
    b = []
    if ini.get("nozzle_diameter"):
        b.append(f"{ini['nozzle_diameter'].split(',')[0]} mm nozzle")
    if ini.get("layer_height"):
        b.append(f"{ini['layer_height']} mm layers")
    if ini.get("perimeters"):
        b.append(f"{ini['perimeters']} walls")
    if ini.get("top_solid_layers") and ini.get("bottom_solid_layers"):
        b.append(f"{ini['top_solid_layers']} top / {ini['bottom_solid_layers']} bottom layers")
    if ini.get("fill_density"):
        b.append(f"{ini['fill_density']} {ini.get('fill_pattern', '')} infill".replace("  ", " ").strip())
    if ini.get("filament_type"):
        b.append(f"{ini['filament_type'].split(';')[0]} filament")
    if ini.get("temperature"):
        b.append(f"nozzle {ini['temperature'].split(',')[0]} °C")
    if ini.get("bed_temperature"):
        b.append(f"bed {ini['bed_temperature'].split(',')[0]} °C")
    s = "**Print settings** (from `print/slice.ini`, the profile the weights and times above were sliced with): " + ", ".join(b) + "."
    over = [p for p in data.printed() if isinstance(p.get("profile_override"), dict)]
    for p in over:  # a part the order stage sliced with its own profile (e.g. tipped onto its side over its own cavity)
        o = p["profile_override"]
        where = [w for w, on in (("allowed on the part", str(o.get("support_material_buildplate_only")) == "0"),
                                 ("under bridges", str(o.get("dont_support_bridges")) == "0")) if on]
        s += (f" `{p.get('name')}` was sliced with `{o.get('file')}` instead: the same settings with supports "
              + " and ".join(where or ["as the file says"]) + f", because it is {o.get('why')}.")
    mats = {str(p.get("material") or "").split()[0].upper() for p in data.printed() if p.get("material")}
    ft = ini.get("filament_type", "").split(";")[0].upper()
    if ft and mats and ft not in mats:
        s += (f" The parts list names {join_and(sorted(mats))} for some parts while the slicer profile is {ft}: use the parts "
              "list material and set the temperatures your filament maker gives for it; the weights and times are estimates either way.")
    return s


def role_is(c: Comp, role: str, data) -> bool:
    from .model import role_of

    return role_of(c, data) == role


def cable_len_mm(cable: Optional[str]) -> Optional[int]:
    m = re.search(r"(\d{2,4})\s*mm", str(cable or ""))
    return int(m.group(1)) if m else None


def shorten(s: str, k: int) -> str:
    s = str(s or "")
    return s if len(s) <= k else s[: k - 1].rstrip() + "…"


def rear_warning(open_dir: Vec) -> str:
    if open_dir[1] > 0.5:
        return REAR_NOTE + "."
    if open_dir[2] < -0.5:
        return "seen from below."
    return ""


def plan(A: Assembly, issues: Optional[Issues] = None) -> list[Step]:
    return Planner(A, issues).plan()
