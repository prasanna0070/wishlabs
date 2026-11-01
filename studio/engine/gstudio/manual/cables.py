"""How each harness meets its module (MAJ-10): the module-end termination, housing positions and wire colours.

Sources, best first:
1. electrical.json `instances[].harness.module_end_housings[]` (+ `module_end_order`, `made_to_drawing`), written by
   the electrical stage from the module's library connector (REVIEW-1 split, BLK-3/MAJ-4);
2. the purchased cable's library row `module_end {family, housings[{housing, positions[{pos, module_pin, wire_colour}]}]}`
   (BLK-2);
3. a fallback derived here from the module's library `module_connector` (type, pin_order) and the harness wires
   in electrical.json, labelled as such.
Factory pre-wiring (build.json purchased[].prewire / factory_work) wins over all three: nothing to fit at the module end.
Nothing here invents a pin position: when no source gives one, the position is left out and the text says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .data import ManualData

FAMILIES = ("jst_ph", "jst_xh", "dupont_2.54", "spade_2.8", "factory_prewired")
FAMILY_WORDS = {
    "jst_ph": "JST-PH 2.0 mm plug",
    "jst_xh": "JST-XH 2.5 mm plug",
    "dupont_2.54": "Dupont 2.54 mm female housing",
    "spade_2.8": "insulated 2.8 mm female spade receptacle",
    "factory_prewired": "lead pre-wired at the factory",
}


@dataclass
class Position:
    pos: Optional[int]
    module_pin: str
    colour: Optional[str] = None
    slot_pin: Optional[int] = None
    empty: bool = False  # a housing position with no wire (the header pin stays free)


@dataclass
class Housing:
    name: str
    positions: list[Position] = field(default_factory=list)


@dataclass
class ModuleEnd:
    ref: str
    slot: str
    family: str  # one of FAMILIES, or "unknown"
    housings: list[Housing] = field(default_factory=list)
    made_to_drawing: bool = False
    factory: Optional[dict] = None
    source: str = ""
    header_fitted_unknown: bool = False  # header module whose header may not come fitted (requires_soldering unknown)
    cable_name: Optional[str] = None
    cable_sku: Optional[str] = None
    length_mm: Optional[int] = None
    mismatch: Optional[str] = None  # the purchased cable's module end cannot mate the module's connector (stale data)

    @property
    def positions(self) -> list[Position]:
        return [p for h in self.housings for p in h.positions]

    def pin1(self) -> Optional[Position]:
        for h in self.housings:
            for p in h.positions:
                if p.pos == 1:
                    return p
        return None

    # ------------------------------------------------------------------ text
    def instruction(self, module: str) -> str:
        """One sentence (or two) that tells the maker how this cable meets the module."""
        if self.mismatch:
            return (f"**Cable mismatch at the {module}:** {self.mismatch}. They do not mate. Do not force or modify the cable; "
                    "stop and ask for the corrected cable before this step.")
        if self.family == "factory_prewired":
            lead = (self.factory or {}).get("lead")
            return (f"The **{module}** arrives pre-wired from our factory" + (f" with a {lead} lead" if lead else "")
                    + "; there is nothing to fit at the module end. Do not cut or re-solder the lead.")
        if self.family == "dupont_2.54":
            parts = []
            for k, h in enumerate(self.housings):
                seq = "; ".join((f"position {p.pos} stays empty (pin {p.module_pin})" if p.empty else
                                 f"position {p.pos} → **{p.module_pin}**" + (f" ({p.colour})" if p.colour else ""))
                                for p in h.positions if p.pos is not None)
                parts.append(f"housing {chr(65 + k)} ({len(h.positions)} positions): {seq}" if len(self.housings) > 1 else seq)
            if not parts or not any(parts):
                return (f"Push the cable's Dupont 2.54 mm female housing onto the **{module}** header pins, matching each "
                        "wire to the pin label printed on the module (see the wiring table); positions are not recorded for this cable.")
            n = len(self.housings)
            return (f"Fit the cable to the **{module}**: push the {'two ' if n == 2 else ''}Dupont 2.54 mm female housing"
                    + ("s" if n > 1 else "") + " straight onto its header pins, position 1 first: "
                    + " | ".join(parts) + ". Read each pin label on the module before pushing; the housing must sit fully down.")
        if self.family == "spade_2.8":
            seq = ", ".join(f"the {p.colour + ' ' if p.colour else ''}wire on **{p.module_pin}**" for p in self.positions)
            return (f"Fit the cable to the **{module}**: push its two insulated 2.8 mm female spade receptacles fully onto the "
                    f"microswitch terminals at the back, {seq}. The terminal names are moulded next to each tab.")
        if self.family in ("jst_ph", "jst_xh"):
            return (f"Plug the cable's other {FAMILY_WORDS[self.family]} into the **{module}**'s socket until it latches; "
                    "it is keyed and fits one way.")
        return (f"Fit the cable to the **{module}** at the module end as the wiring table shows; the module-end termination "
                "is not recorded for this cable, so check it against the parts list before continuing.")

    def stop_rules(self, module: str) -> list[str]:
        out = []
        if self.mismatch:
            return [f"the cable does not mate with the {module} ({self.mismatch}): stop and ask for the corrected cable"]
        if self.family == "dupont_2.54":
            out.append(f"the {module} has no header pins fitted: stop, because this build has no soldering step; photograph the board edge and ask")
            if self.made_to_drawing:
                p1 = self.pin1()
                out.append(f"the housing order at the {module} differs from the list above"
                           + (f" (position 1 must be {p1.module_pin})" if p1 else "")
                           + ": this cable is made to drawing and crosses wires between its ends; do not re-pin it or swap a housing")
        if self.family == "spade_2.8":
            out.append("a spade receptacle is loose on its tab or the tabs are not labelled COM and NO: stop and photograph the switch")
        if self.mismatch:
            return (f"**Cable mismatch at the {module}:** {self.mismatch}. They do not mate. Do not force or modify the cable; "
                    "stop and ask for the corrected cable before this step.")
        if self.family == "factory_prewired":
            out.append(f"the {module} arrives without its lead, or a solder joint is broken: stop; do not solder it yourself")
        if self.family == "unknown":
            out.append(f"the cable does not fit the {module}'s connector: stop and photograph both")
        return out


# --------------------------------------------------------------------------------------------- readers
def _norm_pin(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _family_word(x: Optional[str]) -> Optional[str]:
    s = str(x or "").lower()
    if not s:
        return None
    if s in FAMILIES:
        return s
    if "factory" in s or "prewire" in s or "pre-wire" in s:
        return "factory_prewired"
    if "spade" in s or "2.8" in s:
        return "spade_2.8"
    if "dupont" in s or "header" in s or "2.54" in s:
        return "dupont_2.54"
    if "xh" in s:
        return "jst_xh"
    if "jst" in s or s.startswith("ph"):
        return "jst_ph"
    return None


def _housings_from(raw, wires: list[dict]) -> list[Housing]:
    colour = {_norm_pin(w.get("module_pin")): w.get("color") or w.get("colour") for w in wires}
    sp = {_norm_pin(w.get("module_pin")): w.get("slot_pin") for w in wires}
    out = []
    for k, h in enumerate(raw or []):
        if not isinstance(h, dict):
            continue
        poss = []
        for j, p in enumerate(h.get("positions") or []):
            if not isinstance(p, dict):
                continue
            mp = str(p.get("module_pin") or p.get("pin") or "")
            pos = p.get("pos", p.get("position", j + 1))
            empty = bool(p.get("empty"))
            sp_ = p.get("slot_pin", p.get("port_pin"))
            poss.append(Position(pos=int(pos) if str(pos).isdigit() else None, module_pin=mp,
                                 colour=None if empty else (p.get("wire_colour") or p.get("wire_color") or p.get("color") or colour.get(_norm_pin(mp))),
                                 slot_pin=None if empty else (sp_ if sp_ is not None else sp.get(_norm_pin(mp))), empty=empty))
        name = str(h.get("housing") or h.get("name") or chr(ord("A") + k))
        out.append(Housing(name=name, positions=poss))
    return out


def a_an(word: str) -> str:
    return "an" if str(word)[:1].lower() in "aeiou" else "a"


def expected_family(ctype: str, loc: str = "") -> Optional[str]:
    """The module-end family a module's own connector needs (None when only factory pre-wiring can serve it)."""
    c = str(ctype or "").lower()
    if c.startswith("header"):
        return "dupont_2.54"
    if c.startswith("jst_ph"):
        return "jst_ph"
    if c.startswith("jst_xh"):
        return "jst_xh"
    if c.startswith("spade") or (c == "wire_leads" and "spade" in str(loc).lower()):
        return "spade_2.8"
    return None


def module_ends(data: ManualData) -> dict[str, ModuleEnd]:
    """ModuleEnd per harnessed instance ref (modules plugged straight into a socket have none)."""
    out: dict[str, ModuleEnd] = {}
    core_ref = (data.electrical.get("core") or {}).get("ref")
    for inst in data.instances:
        ref, slot = inst.get("ref"), inst.get("slot")
        h = inst.get("harness") or {}
        if not ref or not slot or ref == core_ref or not h or not (h.get("wires") or []):
            continue
        kind = data.backplane.slots[slot].kind if data.backplane and slot in data.backplane.slots else ""
        if kind in ("socket", "power"):
            continue
        wires = list(h.get("wires") or [])
        mod = data.lib.get(inst.get("module")) or {}
        conn = mod.get("module_connector") if isinstance(mod.get("module_connector"), dict) else {}
        ctype = str(conn.get("type") or "").lower()
        loc = str(conn.get("location") or "").lower()
        cable_row = next((p for p in data.purchased() if p.get("kind") == "cable" and ref in (p.get("refs") or [])), None)
        cable_lib = data.lib.get(cable_row.get("module_id")) if cable_row and cable_row.get("module_id") else {}
        me = ModuleEnd(ref=ref, slot=slot, family="unknown", length_mm=h.get("length_mm"),
                       cable_name=h.get("cable_name") or (cable_row or {}).get("name"), cable_sku=h.get("sku") or (cable_row or {}).get("sku"))
        pw = data.prewire(ref)
        if pw or h.get("factory_prewired") or "factory_prewired" in (str(h.get("module_end") or "").lower(), str(h.get("module_end_family") or "").lower()):
            me.family = "factory_prewired"
            me.factory = dict(pw or {})
            if not me.factory.get("pads"):
                pads = [x for hh in (h.get("module_end_housings") or []) if isinstance(hh, dict) for x in (hh.get("pads") or [])]
                if pads:
                    me.factory["pads"] = pads
            me.housings = _housings_from(h.get("module_end_housings") or [], wires) if h.get("module_end_housings") else []
            me.source = "build.json factory pre-wiring" if pw else "electrical.json harness (factory_prewired)"
            out[ref] = me
            continue
        raw_h = h.get("module_end_housings")
        lib_me = (cable_lib or {}).get("module_end") if isinstance((cable_lib or {}).get("module_end"), dict) else None
        if raw_h:
            me.housings = _housings_from(raw_h, wires)
            me.family = (_family_word(h.get("module_end_family")) or _family_word((lib_me or {}).get("family"))
                         or _family_word(h.get("module_end")) or _family_word(ctype) or "unknown")
            me.made_to_drawing = bool(h.get("made_to_drawing"))
            me.source = "electrical.json harness.module_end_housings"
        elif lib_me and lib_me.get("housings"):
            me.housings = _housings_from(lib_me.get("housings"), wires)
            me.family = _family_word(lib_me.get("family")) or "unknown"
            me.made_to_drawing = bool(h.get("made_to_drawing") or (cable_lib or {}).get("made_to_drawing"))
            me.source = f"library {cable_row.get('module_id')} module_end"
            want = expected_family(ctype, loc)
            if want and me.family != "unknown" and want != me.family:
                have_w, want_w = FAMILY_WORDS.get(me.family, me.family), FAMILY_WORDS.get(want, want)
                me.mismatch = f"the parts list cable ends in {a_an(have_w)} {have_w}, but the module needs {a_an(want_w)} {want_w}"
        else:
            # fallback: the module's own connector data from the library, and the wire colours from electrical.json
            fam = _family_word(ctype)
            if ctype == "wire_leads" and "spade" in loc:
                fam = "spade_2.8"
            elif ctype in ("wire_leads", "solder_pads", "none", ""):
                fam = None
            me.family = fam or "unknown"
            order = [str(x) for x in (conn.get("pin_order") or [])]
            by = {_norm_pin(w.get("module_pin")): w for w in wires}
            seq = [p for p in order if _norm_pin(p) in by] or [str(w.get("module_pin")) for w in wires]
            poss = [Position(pos=i + 1 if me.family == "dupont_2.54" else None, module_pin=p,
                             colour=(by.get(_norm_pin(p)) or {}).get("color"), slot_pin=(by.get(_norm_pin(p)) or {}).get("slot_pin"))
                    for i, p in enumerate(seq)]
            me.housings = [Housing(name=f"1x{len(poss)}", positions=poss)] if poss else []
            port_seq = [_norm_pin(w.get("module_pin")) for w in sorted(wires, key=lambda w: w.get("slot_pin") or 0)]
            mod_seq = [_norm_pin(p) for p in seq]
            me.made_to_drawing = bool(h.get("made_to_drawing")) or (me.family == "dupont_2.54" and port_seq != mod_seq)
            me.source = "library module_connector pin order + electrical.json wires (derived by the manual stage)"
        if me.family == "dupont_2.54":
            me.header_fitted_unknown = mod.get("requires_soldering") is None
        out[ref] = me
    return out


# --------------------------------------------------------------------------------------------- look-alike ports
def lookalike_ports(data: ManualData, used_slots: list[str]) -> list[list[str]]:
    """Groups of used ports that share connector family and pin count, so a lead fits either (MAJ-10)."""
    bp = data.backplane
    if not bp:
        return []
    groups: dict[tuple, list[str]] = {}
    for sid in used_slots:
        sl = bp.slots.get(sid)
        if not sl or sl.kind != "jst_ph":
            continue
        fam = "xh" if "xh" in str(sl.footprint or "").lower() else "ph"
        groups.setdefault((fam, len(sl.pins)), []).append(sid)
    return [sorted(v) for v in groups.values() if len(v) > 1]
