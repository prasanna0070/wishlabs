"""Generated KiCad library items: named-pin socket symbols for plug-in modules and NPTH post footprints.

Module sockets (devkit headers J1/J3, amp breakout, microSD module) use KiCad's own PinSocket footprints, but a
generic Conn_01xNN symbol would hide what each pin is. These symbols carry the module's pin names (GPIO numbers,
LRC/BCLK/..., MISO/MOSI/...) so the schematic reads like the module. They live in the project library `gstudio`
(gstudio.kicad_sym next to the project) and are embedded in the schematic's lib_symbols like any other symbol.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import sexpr
from .sexpr import Q

LIB = "gstudio"
PITCH = 2.54


def _font(size=1.27, thick=None, hide=False, justify=None):
    f = ["font", ["size", size, size]]
    if thick is not None:
        f.append(["thickness", thick])
    e = ["effects", f]
    if justify:
        e.append(["justify", *justify])
    if hide:
        e.append(["hide", "yes"])
    return e


def _snap_up(v: float, step: float = PITCH) -> float:
    return round(math.ceil(v / step - 1e-9) * step, 4)


def socket_symbol(name: str, title: str, pins: list[tuple[str, str]], side: str = "left",
                  footprint: str = "", datasheet: str = "") -> list:
    """Single-row socket symbol, pins on `side`, pin names inside the body, pin 1 at the top."""
    n = len(pins)
    top = (n - 1) / 2 * PITCH
    maxlen = max((len(nm) for _, nm in pins), default=3)
    bw = _snap_up(maxlen * 1.27 * 0.85 + 2.54)
    if side == "left":
        bx0, bx1, px, ang = -2.54, -2.54 + bw, -5.08, 0
    else:
        bx0, bx1, px, ang = 2.54 - bw, 2.54, 5.08, 180
    by0, by1 = top + 1.27, -top - 1.27
    gfx = ["symbol", Q(f"{name}_0_1"),
           ["rectangle", ["start", bx0, by0], ["end", bx1, by1],
            ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]]
    unit = ["symbol", Q(f"{name}_1_1")]
    for i, (num, nm) in enumerate(pins):
        y = round(top - i * PITCH, 4)
        et = "bidirectional" if nm.startswith("GPIO") else "passive"
        unit.append(["pin", et, "line", ["at", px, y, ang], ["length", 2.54],
                     ["name", Q(nm), _font()], ["number", Q(num), _font()]])
    return ["symbol", Q(f"{LIB}:{name}"),
            ["pin_names", ["offset", 1.016]],
            ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            ["property", Q("Reference"), Q("J"), ["at", bx0, by0 + 1.27, 0], _font(justify=["left"])],
            ["property", Q("Value"), Q(title), ["at", bx0, by1 - 1.27, 0], _font(justify=["left"])],
            ["property", Q("Footprint"), Q(footprint), ["at", 0, 0, 0], _font(hide=True)],
            ["property", Q("Datasheet"), Q(datasheet), ["at", 0, 0, 0], _font(hide=True)],
            ["property", Q("Description"), Q(f"Socket for {title}; pin names follow the module"), ["at", 0, 0, 0],
             _font(hide=True)],
            gfx, unit]


def npth_footprint(d: float) -> tuple[str, list]:
    name = f"NPTH_{d:.1f}mm"
    r = d / 2
    node = ["footprint", Q(name), ["version", 20241229], ["generator", Q("gstudio")], ["layer", Q("F.Cu")],
            ["descr", Q(f"Non-plated hole d{d} mm (printed support post passes through)")],
            ["tags", Q("NPTH hole post")],
            ["property", Q("Reference"), Q("REF**"), ["at", 0, -r - 1.2, 0], ["layer", Q("F.Fab")], _font(1.0, 0.15)],
            ["property", Q("Value"), Q(name), ["at", 0, r + 1.2, 0], ["layer", Q("F.Fab")], _font(1.0, 0.15)],
            ["attr", "exclude_from_pos_files", "exclude_from_bom", "allow_missing_courtyard"],
            ["fp_circle", ["center", 0, 0], ["end", r + 0.5, 0], ["stroke", ["width", 0.05], ["type", "solid"]],
             ["fill", "no"], ["layer", Q("F.CrtYd")]],
            ["fp_circle", ["center", 0, 0], ["end", r + 0.15, 0], ["stroke", ["width", 0.12], ["type", "solid"]],
             ["fill", "no"], ["layer", Q("F.SilkS")]],
            ["pad", Q(""), "np_thru_hole", "circle", ["at", 0, 0], ["size", d, d], ["drill", d],
             ["layers", Q("*.Cu"), Q("*.Mask")]]]
    return name, node


def write_project_libs(pcb_dir: Path, gen_symbols: list[list], footprints: list[tuple[str, list]],
                       sym_libs: set[str], fp_libs: set[str]) -> dict:
    """Write gstudio.kicad_sym, gstudio.pretty/ and project fp-lib-table / sym-lib-table.

    The tables name every KiCad library the design uses (via ${KICAD9_SYMBOL_DIR} / ${KICAD9_FOOTPRINT_DIR}), so
    the project is self-contained: kicad-cli on a bare builder and KiCad on a desktop resolve the same files.
    """
    pretty = pcb_dir / f"{LIB}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    for name, node in footprints:
        (pretty / f"{name}.kicad_mod").write_text(sexpr.dumps(node) + "\n")
    lib_nodes = []
    for s in gen_symbols:
        c = sexpr.deep_copy(s)
        c[1] = Q(str(s[1]).split(":", 1)[1])
        lib_nodes.append(c)
    (pcb_dir / f"{LIB}.kicad_sym").write_text(sexpr.dumps(
        ["kicad_symbol_lib", ["version", 20241209], ["generator", Q("gstudio")], ["generator_version", Q("9.0")],
         *lib_nodes]) + "\n")
    fp_rows = [f'\t(lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB}.pretty")(options "")(descr "Gadget Studio generated"))']
    fp_rows += [f'\t(lib (name "{n}")(type "KiCad")(uri "${{KICAD9_FOOTPRINT_DIR}}/{n}.pretty")(options "")(descr ""))'
                for n in sorted(set(fp_libs) - {LIB})]
    sym_rows = [f'\t(lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB}.kicad_sym")(options "")(descr "Gadget Studio generated"))']
    sym_rows += [f'\t(lib (name "{n}")(type "KiCad")(uri "${{KICAD9_SYMBOL_DIR}}/{n}.kicad_sym")(options "")(descr ""))'
                 for n in sorted(set(sym_libs) - {LIB})]
    (pcb_dir / "fp-lib-table").write_text("(fp_lib_table\n\t(version 7)\n" + "\n".join(fp_rows) + "\n)\n")
    (pcb_dir / "sym-lib-table").write_text("(sym_lib_table\n\t(version 7)\n" + "\n".join(sym_rows) + "\n)\n")
    return {"pretty": str(pretty), "symbol_lib": str(pcb_dir / f"{LIB}.kicad_sym")}


__all__ = ["socket_symbol", "npth_footprint", "write_project_libs", "LIB"]
