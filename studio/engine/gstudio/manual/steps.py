"""Assembly steps: generated from the classified components, never from a hardcoded list.

Each Step carries the build.json fields (id, title, instruction, expected, parts, highlight, camera) and
the FigureSpecs for its illustrations. Text follows the house style: imperative, exact names and
quantities, orientation as an axis plus a gadget-relative word, an expected result and a stop rule.
Numbers only appear when they come from saved data (CAD, library, electrical, slicer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .data import Comp, camel_words, first
from .figs import (FRONT_34, REAR_34, TOP_34, BELOW_34, FigureSpec, LabelSpec, MeshSpec, PanelSpec,
                   camera_hint)
from .model import (ROLE_NOUN, Assembly, Group, Vec, anchor_owner, anchors_for, axis_phrase, axis_short,
                    bbox_size, comp_qty, connector_dir, dominant_axis, overhang_dir, vadd, vnorm, vscale)

FOOTER = "Rendered from the saved CAD | +Z up | purchased parts are simplified envelopes | not a physical test"
FACE_PART = {"speaker": "cone", "display": "screen", "button": "cap", "led": "LEDs", "mic": "sound port",
             "sensor": "sensor face", "touch": "pad", "buzzer": "sound opening"}


@dataclass
class Step:
    id: str
    title: str
    use: str
    tools: str
    body: str
    expected: str
    stop: str
    parts: list[str]
    highlight: list[str]
    figures: list[FigureSpec] = field(default_factory=list)
    camera: Optional[dict] = None
    adds: list[str] = field(default_factory=list)
    extra_images: list[str] = field(default_factory=list)  # shared images (wiring.png, ports.png...)

    @property
    def instruction(self) -> str:
        parts = [f"**Use:** {self.use}", f"**Tools:** {self.tools}", self.body.strip()]
        if self.stop:
            parts.append(f"**Stop if:** {self.stop}")
        return "\n\n".join(p for p in parts if p)


# --------------------------------------------------------------------------------------------- naming
class Namer:
    def __init__(self, A: Assembly):
        self.A, self.d = A, A.data

    def name(self, c: Comp) -> str:
        if c.kind == "PRINT" or c.kind == "TEST":
            return f"`{c.name}`"
        if "#" in c.name or (not c.ref and c.module_id and re.search(r"screw|insert|feet|foot|tie|nut|gasket|bolt", c.name, re.I)):
            # kit hardware: the CAD label says which screws these are; drop its "xN" count (quantities are separate)
            lab = re.sub(r"\s*[x×]\s?\d+\s*$", "", c.label or "").strip()
            if lab:
                return lab
        for p in self.d.purchased():
            if p.get("modelPart") == c.name and p.get("name"):
                return str(p["name"])
        if c.ref or c.module_id:
            return self.d.module_name(c)
        return c.label if c.label else camel_words(c.name)

    def qty(self, c: Comp) -> int:
        return comp_qty(self.d, c)[0]

    def use(self, comps: list[Comp], extra: list[str] = ()) -> str:
        items = []
        seen = set()
        for c in comps:
            if c is None or c.name in seen:
                continue
            seen.add(c.name)
            n = self.name(c)
            items.append(f"{self.qty(c)} × {n}" if c.kind in ("PRINT", "TEST") else f"{self.qty(c)} × **{n}**")
        items.extend(extra)
        return join_and(items) + "." if items else "no parts."

    def noun(self, g: Group) -> str:
        return ROLE_NOUN.get(g.role, g.role)

    def short(self, c: Comp) -> str:
        """Label text for images (lbl() wraps long lines)."""
        if c.kind in ("PRINT", "TEST"):
            return c.name
        return self.name(c)


def slot_desc(slot) -> Optional[str]:
    """Human description of a backplane slot: its title, else a non-KiCad footprint string."""
    if slot is None:
        return None
    if slot.label:
        return str(slot.label)
    fp = str(slot.footprint or "")
    return fp if fp and ":" not in fp else None


def slot_note(slot) -> Optional[str]:
    """Orientation notes the backplane slot map states for a socket (quoted, not invented)."""
    if slot is None:
        return None
    bits = []
    for key in ("facing", "card_slot_side", "orientation"):
        v = slot.raw.get(key) if isinstance(slot.raw, dict) else None
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("up", "down"):
            bits.append(v.strip().rstrip("."))
    return "; ".join(bits) if bits else None


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


# --------------------------------------------------------------------------------------------- planner
class Planner:
    def __init__(self, A: Assembly):
        self.A, self.d, self.n = A, A.data, Namer(A)
        self.installed: list[str] = []
        self.steps: list[Step] = []
        self.bboxes = {c.name: c.bbox for c in [*self.d.comps.values(), *self.d.subcomps.values()] if c.bbox}
        self.shell_size = bbox_size(A.shell.bbox) if A.shell and A.shell.bbox else (120.0, 100.0, 140.0)
        self.scale = max(self.shell_size)

    # ------------------------------------------------------------------ helpers
    @property
    def num(self) -> int:
        return len(self.steps) + 1

    def fig_name(self, slug: str) -> str:
        return f"manual/step-{self.num:02d}-{slug}.png"

    def interior_dir(self) -> Vec:
        return REAR_34 if self.A.open_dir[1] > 0.5 else BELOW_34 if self.A.open_dir[2] < -0.5 else vnorm(vadd(self.A.open_dir, (0.4, -0.3, 0.6)))

    def body_names(self) -> list[str]:
        return [c.name for c in self.A.body]

    def ctx_show(self, ghost_body: bool = True) -> dict[str, str]:
        show = {n: "solid" for n in self.installed}
        for n in self.body_names():
            show[n] = "ghost" if ghost_body else "solid"
        return show

    def explode(self, comps: list[Comp], axis: Vec, dist: float) -> dict[str, Vec]:
        return {c.name: vscale(axis, dist) for c in comps}

    def add(self, step: Step) -> Step:
        base, k = step.id, 2
        while any(x.id == step.id for x in self.steps):
            step.id = f"{base}-{k}"
            k += 1
        step.camera = step.camera or self._camera(step)
        # build.json joins on component names: a subset "BASE#i,j" is reported as its component BASE
        step.parts = list(dict.fromkeys(n.split("#", 1)[0] for n in step.parts))
        step.highlight = list(dict.fromkeys(n.split("#", 1)[0] for n in step.highlight))
        self.steps.append(step)
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
                out += wrap(x, 30, 2 if not out else 1)
        text = "\n".join(out[:3])
        return LabelSpec(text=text, comp=c.name, priority=prio)

    # ------------------------------------------------------------------ the plan
    def plan(self) -> list[Step]:
        A = self.A
        self.step_check_parts()
        coupon = self.coupon_files()
        bed = self.bed_figure() if (self.d.print_files or A.prints) else None
        if coupon:
            self.step_fit_coupon(coupon, bed)
        if A.prints:
            self.step_print(bed)
        self.installed.extend(self.body_names())
        if A.inserts:
            self.step_inserts()
        if A.backplane or A.core:
            self.step_cartridge()
            self.step_mount_cartridge()
        for g in A.features:
            self.step_feature(g)
        for g in A.internals:
            self.step_feature(g, internal=True)
        if [i for i in self.d.instances if i.get("slot")] or A.cables:
            self.step_plug()
        if A.cables or A.ties:
            self.step_route()
        self.step_flash()
        if A.covers:
            self.step_close()
        self.step_final()
        return self.steps

    # ------------------------------------------------------------------ 1. parts
    def step_check_parts(self) -> None:
        A, n = self.A, self.n
        buys = [c for c in A.purchases]
        kit_fig = self.kit_figure(buys) if buys else None
        ex_fig = self.exploded_figure()
        n_print = len(A.prints)
        body = (
            "Lay every purchased item on a clear table and tick it off against the parts list: exact module, "
            "quantity and connector pin count. Keep screws, inserts and feet in a small tray"
            + (", away from children" if self.child() else "") + ".\n\n"
            "Do not substitute a module. The backplane ports, cable pin counts and firmware pin numbers are fixed "
            "for these exact modules. Compare each module's printed pin labels with the wiring table "
            "(`manual/wiring.png`) now, before anything is printed or plugged.\n\n"
            + (f"The {n_print} printed part{'s' if n_print != 1 else ''} come later (step "
               f"{self.num + (2 if self.coupon_files() else 1)}). " if n_print else "")
            + "The exploded view shows where every part ends up."
        )
        self.add(Step(
            id="check-parts", title="Check the parts",
            use=n.use(buys),
            tools="None. A clear table, a small parts tray and the parts list.",
            body=body,
            expected="Every line of the parts list is present in the stated quantity, and each module's printed pin labels match the wiring table.",
            stop="a module, its pin labels or a cable's pin count differ from the parts list or the wiring table. Photograph both sides of the module and ask before continuing; do not swap in a similar part.",
            parts=[c.name for c in buys], highlight=[c.name for c in buys],
            figures=[f for f in (kit_fig, ex_fig) if f],
        ))

    def kit_figure(self, comps: list[Comp]) -> Optional[FigureSpec]:
        items = [c for c in comps if c.bbox]
        if not items:
            return None
        show = {c.name: "solid" for c in items}
        labels = [self.lbl(c, f"{self.n.qty(c)} × {self.n.short(c)}", prio=1) for c in items]
        return FigureSpec(
            file=self.fig_name("parts"), title=f"{self.num}  Check the purchased parts",
            panels=[PanelSpec(subtitle="Every purchased item, one of each shown; the label gives the quantity used",
                              show=show, view_dir=(0.0, -0.8, 1.0), layout="grid", label_room=0.0, pad=1.12, labels=labels)],
            footer="Saved CAD envelopes of the purchased parts, not product photos | all drawn to the same scale",
            alt="Every purchased part laid out on a table, one of each, labelled with the quantity used and its name.",
            caption="CAD envelopes, not photos: check the real parts against the parts list and the wiring table.",
            size=(2000, 1250),
        )

    def exploded_figure(self) -> Optional[FigureSpec]:
        A = self.A
        if not A.shell:
            return None
        ax = A.open_dir
        depth = abs(sum(self.shell_size[i] * ax[i] for i in range(3))) or self.scale
        show, offs, labels = {}, {}, []
        show[A.shell.name] = "solid"
        for c in A.inserts:
            show[c.name] = "solid"
        labels.append(self.lbl(A.shell, A.shell.name, "main shell", prio=2))
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
                labels.append(self.lbl(g.module, (g.label or self.n.noun(g).capitalize()), prio=1))
        if A.backplane:
            labels.append(self.lbl(A.backplane, "Core cartridge", "(backplane + core board)", prio=2))
        for c in A.covers[:1]:
            labels.append(self.lbl(c, c.name, f"fits along {axis_short(vscale(ax, -1))}", prio=2))
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
            alt=f"Exploded view of the {self.d.product_name}: shell, front modules, core cartridge and closing part separated along one axis.",
            caption="Separation distances are for clarity only. Cables are hidden.",
            size=(2000, 1100),
        )

    # ------------------------------------------------------------------ 2-3. prints
    def coupon_files(self) -> list[str]:
        return [p.name for p in self.d.print_files if p.name.upper().startswith("TEST_")]

    def bed_figure(self) -> Optional[FigureSpec]:
        files = self.d.print_files
        meshes: list[MeshSpec] = []
        labels: list[LabelSpec] = []
        color = self.A.shell.color if self.A.shell else (0.55, 0.78, 0.75)
        notes = {str(p.get("file", "")).split("/")[-1]: p for p in self.d.printed()}
        for p in files:
            nm = p.stem
            c = self.d.comps.get(nm)
            col = c.color if c else (0.86, 0.86, 0.83) if nm.upper().startswith("TEST_") else color
            meshes.append(MeshSpec(path=str(p), color=col, name=nm))
            note = first(notes.get(p.name, {}), "orientation_note", default="") or first((c.raw.get("print") if c else None) or {}, "orientation_note", default="")
            tail = "fit test, not a gadget part" if nm.upper().startswith("TEST_") else (note or "as exported, on the bed")
            labels.append(LabelSpec(text=f"{nm}\n{shorten(tail, 40)}", comp=nm, priority=1))
        if not meshes:
            return None
        return FigureSpec(
            file="manual/print-bed.png", title="Print the supplied STL files without scaling",
            panels=[PanelSpec(subtitle="Each part in the orientation it was exported in (the side resting on the bed is down)",
                              meshes=meshes, view_dir=(0.3, -0.75, 0.85), focus_meshes=True, labels=labels,
                              label_room=0.05)],
            footer="Saved print meshes (print/*.stl) laid side by side; not a packed print job | supports not shown",
            alt="The print STL files resting on a notional print bed in their exported orientation.",
            caption="Orientation view only. It does not show supports or prove that a printer can print the part.",
        )

    def step_fit_coupon(self, coupon: list[str], bed: Optional[FigureSpec]) -> None:
        A, n = self.A, self.n
        tested = [c for c in [*(g.module for g in A.features if g.role == "button"), *A.inserts[:1], *A.cartridge_fasteners[:1]] if c]
        names = ", ".join(f"`print/{f}`" for f in coupon)
        body = (
            f"Print {names} first, in the same material and with the same settings you will use for the shell. "
            "It is a small, cheap sample of the tight interfaces: try each real part in its matching feature by hand "
            + ("(" + join_and([n.name(c) for c in tested]) + "). " if tested else ". ")
            + "Parts should go in without force and stay put.\n\n"
            "If something binds or is loose, stop before printing the shell and record what binds. "
            "Do not scale the design to force a fit. Printer accuracy is not assumed: the designed clearances are "
            "trial values that this real coupon checks."
        )
        self.add(Step(
            id="fit-coupon", title="Print and try the fit coupon",
            use=f"1 × {', '.join('`' + f.rsplit('.', 1)[0] + '`' for f in coupon)}"
                + (" and, for trying only, " + join_and([f"1 × **{n.name(c)}**" for c in tested]) + "." if tested else "."),
            tools="Printer or printing service, ruler or caliper.",
            body=body,
            expected="The real parts fit the printed coupon by hand without force and are held without wobble. Keep the coupon as a reference; it is not a gadget part.",
            stop="a part binds, a hole is too tight or loose, or an insert will not seat square. Measure the hole and record the material and printer; send that with a photo before printing the shell.",
            parts=[f.rsplit(".", 1)[0] for f in coupon] + [c.name for c in tested],
            highlight=[c.name for c in tested],
            figures=[bed] if bed else [],
        ))

    def step_print(self, bed: Optional[FigureSpec]) -> None:
        A, n = self.A, self.n
        printed = {str(p.get("file", "")).split("/")[-1].rsplit(".", 1)[0]: p for p in self.d.printed()}
        lines = []
        for c in A.prints:
            p = dict(c.raw.get("print") or {})
            p.update(printed.get(c.name, {}))
            bits = []
            if p.get("material"):
                bits.append(str(p["material"]))
            if p.get("color"):
                bits.append(str(p["color"]))
            if p.get("grams"):
                bits.append(f"about {round(float(p['grams']))} g (slicer estimate)")
            if p.get("hours"):
                bits.append(f"about {float(p['hours']):.1f} h (slicer estimate)")
            if p.get("supports") is not None:
                bits.append("supports: " + ("yes" if p["supports"] else "none"))
            if p.get("orientation_note"):
                bits.append(str(p["orientation_note"]))
            lines.append(f"- `print/{c.name}.stl`" + (": " + "; ".join(bits) if bits else ""))
        colour = (self.d.spec.get("colors") or {}).get("body_name")
        body = (
            "Print each file from `print/` as exported: it is already in its print orientation, resting on the bed. "
            "Do not scale or rotate it.\n\n" + "\n".join(lines) + "\n\n"
            + (f"The design colour is {colour}. " if colour else "")
            + "Remove all support material. Deburr every opening (grille holes, windows, button and screw holes) "
            "so no strings or flakes are left. Check that every boss and clip is whole. Try the closing part on the "
            "empty shell before adding anything: it should seat evenly by hand."
        )
        self.add(Step(
            id="print-parts", title="Print and inspect the printed parts",
            use=n.use(A.prints),
            tools="Printer or printing service, slicer, flush cutters or a deburring tool, ruler or caliper.",
            body=body,
            expected="Every printed part is complete, unscaled and clean, with open holes and whole bosses, and the closing part seats on the empty shell by hand.",
            stop="a part is warped, a boss or clip is cracked, or the closing part will not seat without force. Reprint rather than sand or force it, and note which part and where.",
            parts=[c.name for c in A.prints], highlight=[c.name for c in A.prints],
            figures=[bed] if bed else [],
        ))

    # ------------------------------------------------------------------ 4. inserts
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

    def step_inserts(self) -> None:
        A, n = self.A, self.n
        ins = A.inserts
        qty = sum(n.qty(c) for c in ins)
        holders = list(A.body) + ([c for c in A.covers] if any(self.insert_axis(c) == A.open_dir for c in ins) else [])
        a_off = {c.name: vscale(self.insert_axis(c), 22.0) for c in ins}
        show_a = self.ctx_show()
        show_a.update({c.name: "focus" for c in ins})
        lab = [self.lbl(c, f"{n.qty(c)} × {n.short(c)}", f"pressed in along {axis_short(vscale(self.insert_axis(c), -1))}", prio=2) for c in ins]
        fig = FigureSpec(
            file=self.fig_name("inserts"), title=f"{self.num}  Install the heat-set inserts",
            panels=[
                PanelSpec(subtitle="A  Inserts above their bosses", show=show_a, offsets=a_off,
                          view_dir=self.interior_dir(), focus=[c.name for c in ins], min_extent=0.55 * self.scale, labels=lab,
                          arrow_comps=[c.name for c in ins]),
                PanelSpec(subtitle="B  Seated flush; shell shown transparent", show=show_a,
                          view_dir=self.interior_dir(), focus=[c.name for c in ins], min_extent=0.55 * self.scale,
                          labels=[self.lbl(c, "Top flush with the boss", prio=1) for c in ins[:1]]),
            ],
            footer=FOOTER + " | insert knurls not modeled",
            alt="Heat-set inserts shown above the printed bosses, then seated flush in them.",
            caption="Placement only. The image does not prove the insert is straight or fully melted in; check each one by eye.",
        )
        self.add(Step(
            id="install-inserts", title="Install the heat-set inserts",
            use=n.use(ins + holders),
            tools="Soldering iron with a heat-set insert tip (or a clean conical tip), tweezers, a flat metal tool to level each insert.",
            body=(
                f"Fit all {qty} inserts before any electronics go in. Set the iron to the temperature the insert "
                "supplier recommends for your print material. Stand each insert on its boss, then let the hot tip carry it "
                "in with light, straight pressure until its top is flush with the boss. Do not push it below flush, and "
                "do not tilt it. Lift the iron straight out and let the boss cool before the next one.\n\n"
                "Work in a ventilated place and park the iron in its stand between inserts. The tip and the insert stay "
                "hot after you lift the iron."
            ),
            expected=f"All {qty} inserts sit square and flush in their bosses, and no boss is cracked or bulged.",
            stop="an insert goes in crooked or sinks below flush, or a boss cracks. Let it cool, photograph it, and ask before using that boss.",
            parts=[c.name for c in ins + holders], highlight=[c.name for c in ins], figures=[fig], adds=[c.name for c in ins],
        ))

    # ------------------------------------------------------------------ 5-6. cartridge
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
            lines.append(
                f"Press the **{n.name(core)}** into the core-board sockets at **U1**"
                + (f" ({fp})" if fp else "")
                + (f" so its USB connectors point toward {axis_phrase(usb)}, over the board edge" if usb else "")
                + ". Push evenly on both ends until it sits fully down. Every pin must be inside a socket; none may hang outside a row."
                + (f" Slot map: {slot_note(slots.get('U1'))}." if slot_note(slots.get("U1")) else "")
            )
        for c in mods:
            inst = d.instance(c.ref)
            slot = inst.get("slot")
            fp = slot_desc(slots.get(slot))
            note = slot_note(slots.get(slot))
            lines.append(
                f"Plug the **{n.name(c)}** ({c.ref}) into socket **{slot or '?'}**" + (f" ({fp})" if fp else "")
                + ". Match its pin labels to the labels next to the socket before pressing it down."
                + (f" Slot map: {note}." if note else "")
            )
        if A.cartridge_prints:
            lines.append(f"Seat the backplane in {join_and([n.name(c) for c in A.cartridge_prints])} as shown.")
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
                alt="The core board, amplifier and microSD module lifted above the backplane, then seated in their sockets.",
                caption="Shows placement and orientation only; it does not prove every pin is in its socket. Check each row by eye.",
            ))
        self.add(Step(
            id="assemble-cartridge", title="Assemble the core cartridge",
            use=n.use(parts), tools="Fingers only. Work on a clean, dry, non-metal surface.",
            body=body,
            expected="The core board and every plug-in module sit fully down in their sockets, in the orientation shown, with no visible pin outside a socket row.",
            stop="a pin misses its socket, a module rocks, or a label on a module differs from the socket labels. Unplug it, photograph the header and the board markings, and ask. Do not bend pins straight by force.",
            parts=[c.name for c in parts], highlight=[c.name for c in parts], figures=figs,
        ))

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
        if A.core and usb:
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
        body = (
            f"Lower the cartridge onto its {qty or 'mounting'} bosses, component side up"
            + (f", with the core board's USB connectors toward {axis_phrase(usb)}" if usb else "")
            + ". The USB-C power input must line up with its opening in the shell. "
            + (f"Fit the {qty} × **{n.name(scr[0])}** through the board holes into the bosses. " if scr else "Fix it with the supplied screws. ")
            + "Tighten each one a little at a time, in a cross pattern, and stop as soon as it is snug: the board must not bend. "
            "No torque value has been established."
        )
        self.add(Step(
            id="mount-cartridge", title="Mount the core cartridge",
            use=n.use(scr, extra=["the assembled core cartridge from the previous step"]),
            tools="A screwdriver that fits the supplied screws." if scr else "Fingers only.",
            body=body,
            expected="The cartridge sits flat on all its bosses, every screw is snug, and the USB-C power input lines up with its opening.",
            stop="a screw will not start by hand, the board bends or rocks, or the USB-C input does not line up with its opening. Back the screws out and photograph the boss and the opening.",
            parts=[c.name for c in cart + scr], highlight=[c.name for c in cart + scr], figures=figs,
            adds=[c.name for c in cart + scr],
        ))

    # ------------------------------------------------------------------ 7-8. front modules
    def step_feature(self, g: Group, internal: bool = False) -> None:
        A, n, d = self.A, self.n, self.d
        if not g.module:
            return
        comps = g.comps
        mod = d.lib.get(g.module.module_id or d.instance(g.ref).get("module"))
        mk = mount_kind(mod)
        normal = g.normal or (0.0, -1.0, 0.0)
        outside = mk in ("panel_nut", "snap_out")
        noun = n.noun(g)
        fp = FACE_PART.get(g.role, "front")
        name = n.name(g.module)
        where = f"behind its opening ({g.label})" if g.label else "behind its opening"
        if internal:
            where = "in its place inside the shell"
        face_dir = axis_phrase(normal)
        conn = connector_dir(A, g.module, ("conn", "jst", "cable", "pin", "header"))

        if mk == "snap_out":
            how = (f"From the outside, push the **{name}** straight into its opening, {fp} toward {face_dir}, until its "
                   "clips catch. Check from inside that every clip has caught; do not lever against the cap.")
        elif mk == "panel_nut":
            how = (f"From the outside, pass the **{name}** through its opening so the {fp} {'face' if plural(fp) else 'faces'} {face_dir}. "
                   "From inside, fit its retaining nut or clip and tighten by hand; do not over-tighten the plastic.")
        elif mk == "snap":
            how = f"Press the **{name}** into its printed holder {where} until it is held, with the {fp} toward {face_dir}."
        elif mk == "screws":
            how = f"Hold the **{name}** against its bosses {where}, {fp} toward {face_dir}."
        elif mk == "adhesive":
            how = f"Peel the backing and press the **{name}** in place {where}, {fp} toward {face_dir}. Hold it firmly; it cannot be repositioned easily."
        elif mk == "press":
            how = f"Press the **{name}** straight into its pocket {where} until it sits flat against the stop, {fp} toward {face_dir}."
        else:
            how = f"Seat the **{name}** {where} so the {fp} {'face' if plural(fp) else 'faces'} {face_dir}. It must sit flat and square to the opening."
        lines = [how]
        if conn:
            lines.append(f"Its cable connector points toward {axis_phrase(conn)}; keep that side clear for the cable.")
        for c in g.extras:
            lines.append(f"Fit the {n.qty(c)} × **{n.name(c)}** where the image shows it, before the part that holds it.")
        for c in g.prints:
            lines.append(f"Fit `{c.name}` as shown in the image" + (" to hold it" if "ring" in c.name.lower() or "retain" in c.name.lower() or "clamp" in c.name.lower() else "") + ".")
        for c in g.fasteners:
            lines.append(f"Fix it with the {n.qty(c)} × **{n.name(c)}**. Tighten gently and stop as soon as it is snug.")
        if g.role == "speaker":
            lines.append("Do not press on the speaker cone. Keep the speaker leads free toward the cartridge.")
        if g.role == "display":
            lines.append("Hold the display by its edges and do not press on the glass.")
        if g.role == "mic":
            lines.append("Keep the sound port clear: no foam, tape or glue over it.")
        if g.role == "led":
            lines.append("Do not look directly into the LEDs from close up when they are tested later.")
        body = "\n\n".join(lines)

        thick = abs(sum(g.module.size[i] * normal[i] for i in range(3)))
        dist = max(34.0, 1.2 * thick + 26.0)
        ax_in = normal if outside else vscale(normal, -1.0)
        offs = {c.name: vscale(ax_in, dist * (1.0 + 0.35 * i)) for i, c in enumerate(comps)}
        show = self.ctx_show()
        show.update({c.name: "focus" for c in comps})
        lab_a = [self.lbl(g.module, n.short(g.module), f"{fp} toward {axis_short(normal)}", prio=3)]
        for c in g.prints:
            lab_a.append(self.lbl(c, c.name, prio=2))
        for c in g.fasteners:
            lab_a.append(self.lbl(c, f"{n.qty(c)} × {n.short(c)}", prio=1))
        lab_b = [self.lbl(g.module, (g.label or noun.capitalize()), f"{fp} {'face' if plural(fp) else 'faces'} {face_dir}", prio=3)]
        conn_key = next((k for k in anchors_for(A, g.module.ref or g.module.name)
                         if any(w in k.lower() for w in ("conn", "jst", "cable", "pin", "header"))), None)
        if conn and conn_key and not outside:
            # the connector is on the inside: label it in the interior view, where it can be seen
            (lab_b if outside else lab_a).append(LabelSpec(text=f"Cable connector\ntoward {axis_short(conn)}", anchor=conn_key, priority=1))
        out_view = vnorm(vadd(normal, (0.35, 0.0, 0.4) if abs(normal[2]) < 0.5 else (0.3, -0.5, 0.0)))
        in_view = self.interior_dir()
        focus = [c.name for c in comps]
        figs = []
        if A.shell:
            figs.append(FigureSpec(
                file=self.fig_name(g.key), title=f"{self.num}  Fit the {(g.label or noun).lower() if g.label else noun}",
                panels=[PanelSpec(subtitle=("A  Inserted from outside" if outside else f"A  Brought in from inside, along {axis_short(normal)}"),
                                  show=show, offsets=offs, view_dir=out_view if outside else in_view, focus=focus,
                                  min_extent=0.42 * self.scale, labels=lab_a, arrow_comps=[g.module.name]),
                        PanelSpec(subtitle="B  In place, seen from outside; shell transparent" if not internal else "B  In place; shell transparent",
                                  show=show, view_dir=out_view if not internal else in_view, focus=focus, min_extent=0.42 * self.scale,
                                  labels=lab_b)],
                footer=FOOTER + f" | {noun} details simplified",
                alt=f"The {noun} shown moving into position, then installed {('behind ' + g.label) if g.label else 'in the shell'}.",
                caption=f"Shows where the {noun} sits and which way it faces. It does not prove fit or retention; check it by hand.",
            ))
        title = f"Fit the {noun}" + (f" ({g.label})" if g.label and g.label.lower() != noun else "")
        self.add(Step(
            id=f"mount-{g.key}", title=title,
            use=n.use(comps),
            tools="A screwdriver that fits the supplied screws." if g.fasteners else "Fingers only.",
            body=body,
            expected=f"The {noun} sits square {('behind its opening' if not internal else 'in its place')} with the {fp} toward {face_dir}, held without force, and its cable is free.",
            stop=f"the {noun} does not sit flat, the {fp} {'are' if plural(fp) else 'is'} not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.",
            parts=[c.name for c in comps], highlight=[c.name for c in comps], figures=figs,
            adds=[c.name for c in comps],
        ))

    # ------------------------------------------------------------------ 9. harnesses
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
            if kind == "socket" and not h:
                continue  # plugged straight into a socket in the cartridge step
            conns.append((inst, h))
        lines = []
        hl = [A.backplane.name] if A.backplane else []
        parts = list(hl)
        for inst, h in conns:
            c = d.comp_for_ref(inst["ref"])
            nm = d.module_name(c or inst["ref"])
            cable = h.get("cable") or ""
            length = h.get("length_mm")
            wires = h.get("wires") or []
            order = ", ".join(f"{w.get('slot_pin')} {w.get('color')} {w.get('module_pin')}" for w in sorted(wires, key=lambda w: w.get("slot_pin") or 0) if w.get("color"))
            desc = cable or (f"{len(wires)}-wire cable" if wires else "its cable")
            if length and str(int(length)) not in desc:
                desc += f", {int(length)} mm"
            lines.append(
                f"**{inst['slot']}** ← {nm} ({inst['ref']}), {desc}." + (f" Pin order at the port: {order}." if order else "")
            )
            if c:
                parts.append(c.name)
                hl.append(c.name)
        parts += [c.name for c in A.harness_parts]
        hl += [c.name for c in A.cables]
        body = (
            "Disconnect USB-C power first. Plug each cable into the port named below. Every JST-PH plug is keyed and "
            "fits one way: line up the ridge on the plug with the slot in the header, then push straight in until it "
            "seats fully. Never pull a cable by its wires; hold the plug body.\n\n"
            + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
            + "\n\nThe pin order is listed from pin 1. Wire colours are a cross-check, not the identifier: "
            "read the port name on the board. The full table is in the wiring diagram."
        )
        self.add(Step(
            id="plug-harnesses", title="Plug the cables into their ports",
            use=n.use(A.harness_parts, extra=[f"{len(conns)} module cable{'s' if len(conns) != 1 else ''} (already on the modules or in the kit)"]),
            tools="Fingers only.",
            body=body,
            expected="Every cable is fully seated in the port named in the wiring table, and no port has the wrong cable.",
            stop="a plug will not go in without force, a cable's colours differ from the table, or a port named here is missing on your board. Photograph both ends; do not re-pin a housing or swap wires.",
            parts=parts, highlight=hl, extra_images=["manual/ports.png", "manual/wiring.png"],
            adds=[c.name for c in A.cables + A.harness_parts],
        ))

    # ------------------------------------------------------------------ 10. routing
    def step_route(self) -> None:
        A, n, d = self.A, self.n, self.d
        ties = A.ties
        rows = []
        for r in d.routes:
            cl = cable_len_mm(r.cable)
            if r.length_mm:
                txt = f"- {r.cable or r.id}: {r.frm or '?'} to {r.to or '?'}, CAD route about {round(float(r.length_mm))} mm"
                if cl:
                    txt += f" for a {cl} mm cable" + (" (enough slack)" if cl >= float(r.length_mm) + 10 else " (tight: check slack before tying)")
                rows.append(txt + ".")
        body = (
            "Lay each cable along the path shown, without twisting it, and leave a little slack at both plugs so no "
            "plug is pulled sideways. Keep cables away from screw bosses, the closing seam and the speaker cone."
            + (f" Gather them with the {sum(n.qty(c) for c in ties)} × **{n.name(ties[0])}** at the tie points shown; pull each tie snug, not tight, and trim the tail flush." if ties else "")
            + ("\n\nRoutes in the CAD:\n" + "\n".join(rows) if rows else "")
            + "\n\nThe routes are display envelopes. Cable stiffness and plug housings are not modeled."
        )
        show = self.ctx_show()
        show.update({c.name: "focus" for c in A.cables + ties})
        labels = []
        for c in A.cables[:7]:
            r = next((r for r in d.routes if r.component == c.name or r.id == c.name), None)
            labels.append(self.lbl(c, shorten(r.cable if r and r.cable else c.label or camel_words(c.name), 30),
                                   f"{r.to.split('.')[0] if r and r.to else ''} to {r.frm.split('.', 1)[-1] if r and r.frm else ''}".strip(" to") if r else "", prio=1))
        for c in ties[:1]:
            labels.append(self.lbl(c, f"{n.qty(c)} × cable tie", prio=2))
        figs = [FigureSpec(
            file=self.fig_name("routing"), title=f"{self.num}  Route and tie the cables",
            panels=[PanelSpec(subtitle="Open side view: " + rear_warning(A.open_dir), show=show, view_dir=self.interior_dir(),
                              focus=self.body_names() or [c.name for c in A.cables], labels=labels, label_room=0.26)],
            footer="Cable shapes are display envelopes only | check real slack, bends and plug access",
            alt="The open gadget with every cable route shown in colour and the tie points labelled.",
            caption="Routes show occupied space, not the exact cable path. Check slack and that nothing crosses the seam.",
        )] if A.shell and (A.cables or ties) else []
        self.add(Step(
            id="route-cables", title="Route and tie the cables",
            use=n.use(ties, extra=["the cables plugged in the previous step"]),
            tools="Flush cutters for the tie tails." if ties else "Fingers only.",
            body=body,
            expected="Every cable follows its route with slack at both plugs, nothing crosses the seam or a screw boss, and no plug is pulled sideways.",
            stop="a cable is too short to reach without pulling on its plug, or it has to cross the seam. Photograph the route; do not extend or cut a cable.",
            parts=[c.name for c in A.cables + ties + A.harness_parts] or ([A.backplane.name] if A.backplane else []),
            highlight=[c.name for c in A.cables + ties], figures=figs, adds=[c.name for c in ties],
        ))

    # ------------------------------------------------------------------ 11. flash + bench test
    def step_flash(self) -> None:
        A, n, d = self.A, self.n, self.d
        fw = d.firmware_dir
        ini = fw / "platformio.ini"
        speed = None
        if ini.is_file():
            m = re.search(r"^\s*monitor_speed\s*=\s*(\d+)", ini.read_text(errors="ignore"), re.M)
            speed = m.group(1) if m else None
        mods = [g for g in [*A.features, *A.internals] if g.module]
        checks = []
        for g in mods:
            checks.append({
                "speaker": "the speaker plays the test sound clearly, without crackle",
                "display": "the display shows the test image the right way up",
                "button": "each press of the button is reported once",
                "led": "the light shows each test colour",
                "mic": "the microphone level reacts when you speak near it",
            }.get(g.role, f"the {n.noun(g)} responds"))
        usb = connector_dir(A, A.core) if A.core else None
        body = (
            "Keep the gadget open. Connect a computer to the core board's USB port with a data-capable USB cable"
            + (f" (the port faces {axis_phrase(usb)})" if usb else "")
            + ". Then, from the delivered project folder:\n\n"
            "```sh\ncd firmware\npio run -t upload\npio device monitor" + (f" -b {speed}" if speed else "") + "\n```\n\n"
            "PlatformIO builds the delivered source and writes it to the board. Follow `SOFTWARE_SETUP.md` to run the "
            "self-test, and check each module in turn:\n\n"
            + "\n".join(f"- {c}" for c in checks)
            + "\n\nUnplug the USB cable before you close the gadget. These are checks for you to make; nothing here "
            "records them as passed."
        )
        show = self.ctx_show()
        labels = []
        if A.core:
            ak = next((k for k in anchors_for(A, A.core.ref or A.core.name) if "usb" in k.lower()), None)
            labels.append(LabelSpec(text="Core board USB port", anchor=ak, priority=3) if ak else self.lbl(A.core, "Core board", "USB port for flashing", prio=3))
            show[A.core.name] = "focus"
        if A.backplane:
            pk = next((k for k in anchors_for(A, A.backplane.ref or "BP1") if "PWR" in k.upper()), None)
            if pk:
                labels.append(LabelSpec(text="USB-C power input", anchor=pk, priority=2))
        for g in mods[:5]:
            labels.append(self.lbl(g.module, (g.label or n.noun(g).capitalize()), prio=1))
        figs = [FigureSpec(
            file="manual/open-back.png", title="The open gadget, ready for the bench test",
            panels=[PanelSpec(subtitle="Everything installed and plugged in; closing part off. " + rear_warning(A.open_dir),
                              show=show, view_dir=self.interior_dir(), focus=self.body_names() or list(show), labels=labels,
                              label_room=0.26)],
            footer=FOOTER,
            alt="The open gadget with the core board's USB port and every module labelled.",
            caption="Placement view. It does not show that the firmware runs or that any module works.",
        )] if A.shell else []
        parts = [c.name for c in [A.core, A.backplane] if c] + [g.module.name for g in mods]
        self.add(Step(
            id="flash-and-test", title="Flash the firmware and run the bench self-test",
            use=f"The open gadget from the previous steps{', 1 × **' + n.name(A.core) + '**' if A.core else ''} already installed, and a data-capable USB cable (not in the kit).",
            tools="A computer with PlatformIO (see `SOFTWARE_SETUP.md`).",
            body=body,
            expected="The upload finishes without errors and every module passes its self-test check by your observation.",
            stop="the upload fails, the board resets repeatedly, any part gets warm, or a module does not respond. Unplug USB first, then save the monitor output and check that module's cable against the wiring table.",
            parts=parts, highlight=[A.core.name] if A.core else parts, figures=figs,
        ))

    # ------------------------------------------------------------------ 12. close
    def step_close(self) -> None:
        A, n = self.A, self.n
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
        lab_a = [self.lbl(c, c.name, f"fits along {axis_short(vscale(ax, -1))}", prio=3) for c in cov]
        for c in scr:
            lab_a.append(self.lbl(c, f"{n.qty(c)} × {n.short(c)}", prio=2))
        if A.shell:
            lab_a.append(self.lbl(A.shell, "Keep every cable", "inside the seam", prio=1))
        lab_b = [self.lbl(c, "Seam closed evenly", prio=2) for c in cov]
        for c in scr:
            lab_b.append(self.lbl(c, "Tighten gently, in stages", prio=1))
        view = vnorm(vadd(vscale(ax, 1.0), (-0.6, 0.0, 0.55))) if ax[1] > 0.5 else vnorm(vadd(vscale(ax, 1.0), (0.5, -0.7, 0.0)))
        foot_txt = ""
        view_b = vnorm((view[0], view[1], -0.6)) if feet else view
        if feet:
            show_b = dict(show)
            show_b.update({c.name: "focus" for c in feet})
            q = sum(n.qty(c) for c in feet)
            foot_txt = f"\n\nTurn it over and stick the {q} × **{n.name(feet[0])}** at the positions shown, on a clean, dry surface."
            lab_b.append(self.lbl(feet[0], f"{q} × {n.short(feet[0])}", prio=1))
        else:
            show_b = show
        figs = [FigureSpec(
            file=self.fig_name("close"), title=f"{self.num}  Close the {camel_words(cov[0].name)}",
            panels=[PanelSpec(subtitle=f"A  Separated along {axis_short(ax)}", show=show, offsets=offs, view_dir=view,
                              focus=[c.name for c in cov] + self.body_names(), labels=lab_a, arrow_comps=[c.name for c in cov]),
                    PanelSpec(subtitle="B  Closed" + ("; seen from below to show the feet" if feet else ""), show=show_b,
                              view_dir=view_b, focus=[c.name for c in cov] + self.body_names(), labels=lab_b)],
            footer=FOOTER + " | check cable clearance and screw access on the real print",
            alt="The closing part separated from the shell with its screws, then closed.",
            caption="The image does not prove that the seam closes without pinching a cable. Close it by hand first.",
        )] if A.shell else []
        q = sum(n.qty(c) for c in scr)
        body = (
            "Check that the USB cable from the bench test is unplugged and that no cable lies across the seam. "
            f"Bring `{cov[0].name}` straight on along {axis_phrase(vscale(ax, -1))}. It should seat evenly by hand, "
            "without pressure. "
            + (f"Fit the {q} × **{n.name(scr[0])}** and tighten them a little at a time, in a cross pattern, until snug. "
               "Never use the screws to pull the seam shut. No torque value has been established." if scr else "")
            + foot_txt
        )
        self.add(Step(
            id="close-cover", title=f"Close the {camel_words(cov[0].name)}",
            use=n.use(cov + scr + feet),
            tools="A screwdriver that fits the supplied screws." if scr else "Fingers only.",
            body=body,
            expected="The seam is closed evenly all round, no cable is pinched, and the gadget stands level on its feet.",
            stop="the part rocks, a gap stays open, or it needs screw force to close. Open it again and look for a cable, a plug or a print edge in the way; photograph it if you cannot find it.",
            parts=[c.name for c in cov + scr + feet], highlight=[c.name for c in cov + scr + feet], figures=figs,
            adds=[c.name for c in cov + scr + feet],
        ))

    # ------------------------------------------------------------------ 13. final
    def step_final(self) -> None:
        A, n, d = self.A, self.n, self.d
        show = {c.name: "solid" for c in d.comps.values() if c.kind in ("PRINT", "PURCHASE")}
        labels = []
        sh = A.shell
        for g in A.features[:6]:
            if not g.module:
                continue
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
        body = (
            "Connect a USB-C 5 V supply" + (" (not included)" if not supply.get("adapter_in_box") else "") + ". "
            "Keep the supply and its plug outside the gadget, and route the cord where nobody trips on it"
            + (" and away from a bed" if self.child() else "") + ".\n\n"
            "Work through the final checks listed at the end of this guide. They are actions for you, not passed "
            "results: record what you observe. Do not leave a gadget that fails a check in use."
        )
        self.add(Step(
            id="final-checks", title="Final checks",
            use=f"The finished {self.d.product_name} and a USB-C 5 V supply" + (" (not included)." if not supply.get("adapter_in_box") else "."),
            tools="None" + (" (adult supervision)." if self.child() else "."),
            body=body,
            expected="Every final check is done and recorded, and each one behaves as described.",
            stop="any check fails, the gadget gets hot, smells, rattles or tips over. Unplug it and do not use it until the cause is found.",
            parts=[c.name for c in A.body + A.covers] or list(show)[:1], highlight=[], figures=figs,
        ))

    def child(self) -> bool:
        aud = self.d.spec.get("audience") or {}
        flags = (self.d.spec.get("safety") or {}).get("flags") or []
        return bool(aud.get("child_directed")) or "CHILD_DIRECTED" in flags


def wrap(text: str, width: int = 30, max_lines: int = 2) -> list[str]:
    """Word-wrap a label line; the last allowed line is shortened with an ellipsis if needed."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = shorten(lines[-1] + " …", width + 1).replace(" …", "…")
    return [shorten(x, width + 6) for x in lines]


def shorten(s: str, k: int) -> str:
    s = str(s or "")
    return s if len(s) <= k else s[: k - 1].rstrip() + "…"


def cable_len_mm(cable: Optional[str]) -> Optional[int]:
    m = re.search(r"(\d{2,4})\s*mm", str(cable or ""))
    return int(m.group(1)) if m else None


def rear_warning(open_dir: Vec) -> str:
    if open_dir[1] > 0.5:
        return "seen from the rear, so the gadget's left (+X) is on your left."
    if open_dir[2] < -0.5:
        return "seen from below."
    return ""


def plan(A: Assembly) -> list[Step]:
    return Planner(A).plan()
