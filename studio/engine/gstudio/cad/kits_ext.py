"""CAD kit registry: catalog packs add mount kits without editing features.py, modules.py or footprint.py.

A pack adds a kit by creating a module `gstudio/cad/kits_<name>.py` next to this file. Each one may define any of:

    KITS        {kind: fn(kit, F, S, O, desc, fz, colors, spec_path)}
                Builds the kit into `kit` (same contract as the built-in kit_* functions in features.py; the feature
                label is kit.label, its id kit.feature_id). Called by features.build_kit.
    CLASSIFY    {aperture need: kind}
                Library row aperture.needs -> kit kind. Called by modules.classify.
    FOOTPRINTS  {kind: fn(desc, fz=None, skin=2.4) -> list of local boxes}
                Pure-Python keep-out of the whole kit, used by the layout validator before any CAD is built
                (footprint.kit_footprint -> normalize.kit_conflicts). A local box is
                    {"part": "PURCHASE_Servo", "kind": "module", "what": "servo body",
                     "u": [a0, a1], "v": [b0, b1], "depth": [d0, d1]}
                with u/v in the kit's local frame (u right, v up, before the feature rotation, origin at features[].at)
                and depth in mm inward from the outer surface (>= 0 inside the body). part "PRINT_Shell" marks shell
                material (pads and bosses may touch other shell material); kind "pad" is a mounting pad.
                The function may take only `desc`: fz and skin are passed only to parameters it names.
    DESCRIBE    {kind: fn(d, entry) -> None}   (optional)
                Adds kind-specific geometry to the CAD descriptor `d` built by modules.describe from library row
                `entry`. Registry kinds also get d["dims"] = the row's dims_mm {x, y, z} (floats or None).

Rules
- Built-in kinds and needs always win: the registry is consulted only before the generic fallbacks (kit_board in
  build_kit, ROLE_KIND in classify, the board footprint in kit_footprint). A key that names a built-in kind or need is
  ignored and reported in errors().
- One key in two files is an error: the first file (sorted by name) keeps it and errors() reports the clash.
- A kits_*.py that fails to import is skipped and reported in errors(), so one broken pack never breaks normalize.
  Tests assert errors() == [] (tests/test_cat_kits_registry.py).
- Light-engine rule (CATALOG-PLAN 0.5.5): the web image runs normalize with no CadQuery, and normalize imports
  cad.footprint -> cad.modules -> this registry. So this module and every kits_*.py must import with no CadQuery and
  without gstudio.firmware, manual, order or pcb: import cadquery and the features.py helpers INSIDE the KITS functions.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# Kinds built into features.build_kit / footprint.kit_footprint / modules.describe, and needs built into
# modules.classify. A registry entry never replaces one of these.
BUILTIN_KINDS = frozenset({"display", "speaker", "snap_button", "tactile", "led", "led_ring", "mic", "touch", "sensor",
                           "sensor_window", "core", "amp", "storage", "generic"})
BUILTIN_NEEDS = frozenset({"none", "display_window", "grille", "button_hole", "led_window", "mic_port", "touch_zone", "vent",
                           "sensor_window", "window", "lens"})
TABLES = ("KITS", "CLASSIFY", "FOOTPRINTS", "DESCRIBE")


class Registry:
    def __init__(self) -> None:
        self.kits: dict[str, Callable] = {}
        self.classify: dict[str, str] = {}
        self.footprints: dict[str, Callable] = {}
        self.describe: dict[str, Callable] = {}
        self.owner: dict[tuple, str] = {}  # (table, key) -> module name
        self.modules: list[str] = []
        self.errors: list[str] = []

    def kinds(self) -> set[str]:
        return set(self.kits) | set(self.footprints) | set(self.describe) | set(self.classify.values())

    def add(self, mod: Any) -> None:
        name = getattr(mod, "__name__", repr(mod)).rsplit(".", 1)[-1]
        self.modules.append(name)
        for table in TABLES:
            src = getattr(mod, table, None) or {}
            if not isinstance(src, dict):
                self.errors.append(f"{name}.{table} must be a dict, got {type(src).__name__}")
                continue
            dst = {"KITS": self.kits, "CLASSIFY": self.classify, "FOOTPRINTS": self.footprints, "DESCRIBE": self.describe}[table]
            builtin = BUILTIN_NEEDS if table == "CLASSIFY" else BUILTIN_KINDS
            for key, val in src.items():
                if key in builtin:
                    self.errors.append(f"{name}.{table}[{key!r}] names a built-in {'need' if table == 'CLASSIFY' else 'kind'}; ignored")
                    continue
                if table == "CLASSIFY" and not (isinstance(val, str) and val):
                    self.errors.append(f"{name}.CLASSIFY[{key!r}] = {val!r}: must be a kind name")
                    continue
                if table != "CLASSIFY" and not callable(val):
                    self.errors.append(f"{name}.{table}[{key!r}] is not callable")
                    continue
                if key in dst:
                    self.errors.append(f"{name}.{table}[{key!r}] clashes with {self.owner[(table, key)]}; kept {self.owner[(table, key)]}")
                    continue
                dst[key] = val
                self.owner[(table, key)] = name


def _pack_module_names() -> list[str]:
    here = Path(__file__).resolve().parent
    return sorted(m.name for m in pkgutil.iter_modules([str(here)]) if m.name.startswith("kits_") and m.name != "kits_ext")


def build_registry(modules: Optional[Iterable[Any]] = None) -> Registry:
    """Registry from the given module objects, or (None) from every gstudio/cad/kits_*.py."""
    reg = Registry()
    if modules is None:
        mods = []
        for name in _pack_module_names():
            try:
                mods.append(importlib.import_module(f"{__package__}.{name}"))
            except Exception as exc:  # one broken pack must not break normalize or CAD
                reg.errors.append(f"{name}: import failed: {type(exc).__name__}: {exc}")
        modules = mods
    for mod in modules:
        reg.add(mod)
    return reg


_REG: Optional[Registry] = None


def registry() -> Registry:
    """The process-wide registry (built on first use; tests swap it with monkeypatch.setattr(kits_ext, "_REG", ...))."""
    global _REG
    if _REG is None:
        _REG = build_registry()
    return _REG


def errors() -> list[str]:
    return list(registry().errors)


def kit_fn(kind: str) -> Optional[Callable]:
    """features.build_kit: the pack's kit builder for `kind`, or None (built-in or generic kit)."""
    if kind in BUILTIN_KINDS:
        return None
    return registry().kits.get(kind)


def classify_need(needs: str) -> Optional[str]:
    """modules.classify: the pack kind for an aperture need, or None."""
    if not needs or needs in BUILTIN_NEEDS:
        return None
    return registry().classify.get(needs)


def is_pack_kind(kind: str) -> bool:
    return kind not in BUILTIN_KINDS and kind in registry().kinds()


def describe_fn(kind: str) -> Optional[Callable]:
    if kind in BUILTIN_KINDS:
        return None
    return registry().describe.get(kind)


def footprint_boxes(kind: str, desc: dict, fz: Optional[dict] = None, skin: float = 2.4) -> Optional[list[dict]]:
    """footprint.kit_footprint: the pack's local keep-out boxes for `kind`, or None when no pack footprint exists."""
    if kind in BUILTIN_KINDS:
        return None
    fn = registry().footprints.get(kind)
    if fn is None:
        return None
    try:
        params = inspect.signature(fn).parameters
        has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        params, has_kw = {}, False
    kw = {k: v for k, v in (("fz", fz), ("skin", skin)) if has_kw or k in params}
    boxes = fn(desc, **kw)
    out = []
    for b in boxes or []:
        u, v, dd = b["u"], b["v"], b["depth"]
        out.append({"part": str(b.get("part") or "PURCHASE_Module"), "kind": str(b.get("kind") or "module"),
                    "what": str(b.get("what") or kind), "u": [float(u[0]), float(u[1])], "v": [float(v[0]), float(v[1])],
                    "depth": [float(dd[0]), float(dd[1])]})
    return out


__all__ = ["Registry", "build_registry", "registry", "errors", "kit_fn", "classify_need", "is_pack_kind", "describe_fn",
           "footprint_boxes", "BUILTIN_KINDS", "BUILTIN_NEEDS"]
