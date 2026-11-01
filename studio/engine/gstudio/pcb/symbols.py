"""KiCad symbol library access: load real symbols from /usr/share/kicad/symbols, flatten `extends`,
and report exact pin connection points so schematic labels attach.

Coordinates: library symbols use Y up. Schematic sheets use Y down. `pin_points()` returns
sheet-relative offsets (Y down) for a symbol placed at rotation 0/90/180/270 (counter-clockwise on screen).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import sexpr
from .sexpr import Q

SYMBOL_DIR = Path(os.environ.get("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols"))


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    etype: str          # power_in, passive, bidirectional, ...
    x: float            # library coordinates (Y up), connection point
    y: float
    angle: float        # direction from connection point toward the body
    length: float
    unit: int           # 0 = common to all units
    hidden: bool


@dataclass
class LibSymbol:
    lib_id: str          # "Device:R"
    node: list           # flattened (symbol "Device:R" ...) ready for lib_symbols
    pins: list[Pin]
    units: int

    def pin(self, number: str) -> Pin:
        for p in self.pins:
            if p.number == number:
                return p
        raise KeyError(f"{self.lib_id} has no pin {number!r}")


@lru_cache(maxsize=None)
def _load_lib(lib: str) -> dict[str, list]:
    path = SYMBOL_DIR / f"{lib}.kicad_sym"
    text = path.read_text(encoding="utf-8")
    root = sexpr.parse(text)
    return {str(n[1]): n for n in sexpr.find_all(root, "symbol")}


def _rename_units(node: list, old: str, new: str) -> None:
    for sub in sexpr.find_all(node, "symbol"):
        name = str(sub[1])
        if name.startswith(old + "_"):
            sub[1] = Q(new + name[len(old):])


def _flatten(lib: str, name: str) -> list:
    syms = _load_lib(lib)
    if name not in syms:
        raise KeyError(f"symbol {lib}:{name} not found in {SYMBOL_DIR}")
    node = sexpr.deep_copy(syms[name])
    ext = sexpr.find(node, "extends")
    if ext is None:
        return node
    parent = _flatten(lib, str(ext[1]))
    pname = str(parent[1])
    out = sexpr.deep_copy(parent)
    out[1] = Q(name)
    _rename_units(out, pname, name)
    # child properties override parent properties; other child flags override too
    child_props = {str(p[1]): p for p in sexpr.find_all(node, "property")}
    new: list = []
    for item in out:
        if isinstance(item, list) and item and item[0] == "property" and str(item[1]) in child_props:
            new.append(child_props.pop(str(item[1])))
        else:
            new.append(item)
    # append child-only properties after the last property
    last_prop = max((i for i, x in enumerate(new) if isinstance(x, list) and x and x[0] == "property"), default=1)
    for p in child_props.values():
        last_prop += 1
        new.insert(last_prop, p)
    for key in ("exclude_from_sim", "in_bom", "on_board", "pin_names", "pin_numbers"):
        c = sexpr.find(node, key)
        if c is not None:
            for i, x in enumerate(new):
                if isinstance(x, list) and x and x[0] == key:
                    new[i] = c
                    break
    return new


def _pins_of(node: list) -> tuple[list[Pin], int]:
    pins: list[Pin] = []
    units = 1
    base = str(node[1])
    for sub in sexpr.find_all(node, "symbol"):
        m = re.match(r"^(.*)_(\d+)_(\d+)$", str(sub[1]))
        if not m:
            continue
        unit, style = int(m.group(2)), int(m.group(3))
        if style not in (0, 1):
            continue  # skip De Morgan alternates
        units = max(units, unit)
        for p in sexpr.find_all(sub, "pin"):
            at = sexpr.find(p, "at")
            length = sexpr.find(p, "length")
            nm = sexpr.find(p, "name")
            nb = sexpr.find(p, "number")
            hidden = any(x == "hide" for x in p) or (sexpr.find(p, "hide") is not None and sexpr.find(p, "hide")[1:2] != ["no"])
            pins.append(Pin(
                number=str(nb[1]), name=str(nm[1]), etype=str(p[1]),
                x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0,
                length=float(length[1]) if length else 0.0, unit=unit, hidden=hidden,
            ))
    del base
    return pins, units


@lru_cache(maxsize=None)
def get(lib_id: str) -> LibSymbol:
    lib, name = lib_id.split(":", 1)
    node = _flatten(lib, name)
    node = sexpr.deep_copy(node)
    node[1] = Q(lib_id)
    pins, units = _pins_of(node)
    return LibSymbol(lib_id=lib_id, node=node, pins=pins, units=units)


def from_node(lib_id: str, node: list) -> LibSymbol:
    """Wrap a generated (symbol ...) node (e.g. the devkit socket symbol)."""
    node = sexpr.deep_copy(node)
    node[1] = Q(lib_id)
    pins, units = _pins_of(node)
    return LibSymbol(lib_id=lib_id, node=node, pins=pins, units=units)


def transform(x: float, y: float, rot: int) -> tuple[float, float]:
    """Library point (Y up) -> sheet offset (Y down) for a symbol rotated `rot` degrees CCW on screen."""
    sx, sy = x, -y
    r = rot % 360
    if r == 0:
        return sx, sy
    if r == 90:
        return sy, -sx
    if r == 180:
        return -sx, -sy
    if r == 270:
        return -sy, sx
    raise ValueError("rotation must be a multiple of 90")


def pin_direction(p: Pin, rot: int) -> tuple[float, float]:
    """Unit vector (sheet coords) pointing from the pin's connection point AWAY from the body."""
    import math

    a = math.radians(p.angle)
    # pin angle points from connection point toward the body (library coords, Y up)
    bx, by = math.cos(a), math.sin(a)
    dx, dy = transform(-bx, -by, rot)
    return round(dx), round(dy)


__all__ = ["Pin", "LibSymbol", "get", "from_node", "transform", "pin_direction", "SYMBOL_DIR"]
