"""Capability packs (CATALOG-PLAN 0.5): optional library bundles that can be switched on or off.

    from gstudio import packs
    packs.enabled("sensing")          # True when the pack is on
    packs.enabled_set()               # frozenset of the pack names that are on
    packs.visible(row)                # False for a library row (or anything with a "pack" key) whose pack is off
    packs.pack_files()                # [(name, path)] for library/packs/<name>.json (never enabled.json)
    packs.prompt_rules()              # [(name, [rule, ...])] of the packs that are on
    packs.example_specs()             # [path] of library/packs/examples/<pack>/<slug>/spec.json for the packs that are on

The switch is library/packs/enabled.json = {"enabled": ["sensing", ...]}, created and edited only by the integrator;
a missing file means nothing is on. Env GS_PACKS_ENABLED=a,b replaces the file list (tests and VM runs only); env
GS_PACKS_OFF=a+b removes packs at runtime (the kill switch; '+' because web.sh passes knobs comma-free, ',' also
works). The env is read on every call; the file is cached by its mtime. A pack that is off is invisible: Library drops
its rows, normalize and the spec reject its fields, and the agent sees none of its rules or examples.

Pure Python (light-engine rule, CATALOG-PLAN 0.5.5): the web image imports this with no CadQuery and without the
firmware, manual, order or pcb packages.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

_PKG = Path(__file__).resolve().parent
_FILE_CACHE: dict[str, tuple[int, frozenset]] = {}
ENABLED_FILE = "enabled.json"


def library_dir(path: Optional[str | os.PathLike] = None) -> Optional[Path]:
    """The library folder (holds modules.json): explicit path > $GS_LIBRARY > the same fallbacks as gstudio.library."""
    cands: list[Path] = []
    if path:
        cands.append(Path(path))
    env = os.environ.get("GS_LIBRARY")
    if env:
        cands.append(Path(env))
    cands += [_PKG.parent.parent / "library", _PKG / "library", Path.cwd() / "library", Path.cwd().parent / "library"]
    for c in cands:
        if c.is_file():
            return c.parent
        if (c / "modules.json").is_file():
            return c
    return None


def packs_dir(lib_dir: Optional[str | os.PathLike] = None) -> Optional[Path]:
    d = library_dir(lib_dir)
    return d / "packs" if d is not None else None


def _split(v: Optional[str]) -> list[str]:
    return [x for x in re.split(r"[+,\s]+", v or "") if x]


def _file_set(lib_dir: Optional[str | os.PathLike]) -> frozenset:
    d = packs_dir(lib_dir)
    if d is None:
        return frozenset()
    f = d / ENABLED_FILE
    try:
        mt = f.stat().st_mtime_ns
    except OSError:
        return frozenset()
    hit = _FILE_CACHE.get(str(f))
    if hit is not None and hit[0] == mt:
        return hit[1]
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        names = data.get("enabled") if isinstance(data, dict) else None
        s = frozenset(str(x).strip() for x in (names or []) if isinstance(x, str) and x.strip())
    except (OSError, ValueError):
        s = frozenset()  # a broken switch file turns every pack off (fail closed)
    _FILE_CACHE[str(f)] = (mt, s)
    return s


def enabled_set(lib_dir: Optional[str | os.PathLike] = None) -> frozenset:
    """Pack names that are on: $GS_PACKS_ENABLED (when set, even empty) or enabled.json, minus $GS_PACKS_OFF."""
    env = os.environ.get("GS_PACKS_ENABLED")
    base = frozenset(_split(env)) if env is not None else _file_set(lib_dir)
    off = set(_split(os.environ.get("GS_PACKS_OFF")))
    return frozenset(n for n in base if n not in off)


def enabled(name: Optional[str], lib_dir: Optional[str | os.PathLike] = None) -> bool:
    return bool(name) and str(name) in enabled_set(lib_dir)


def row_packs(row: Any) -> list[str]:
    """The pack(s) a row belongs to: [] for core rows; a generated harness row used only by pack rows lists them."""
    p = row.get("pack") if isinstance(row, dict) else None
    if not p:
        return []
    return [p] if isinstance(p, str) else [str(x) for x in p if x]


def visible(row: Any, lib_dir: Optional[str | os.PathLike] = None, on: Optional[frozenset] = None) -> bool:
    """True for core rows and for rows of a pack that is on (any of them, for a harness row shared by several packs)."""
    names = row_packs(row)
    if not names:
        return True
    on = enabled_set(lib_dir) if on is None else on
    return any(n in on for n in names)


def pack_files(lib_dir: Optional[str | os.PathLike] = None) -> list[tuple[str, Path]]:
    """Every pack data file, sorted by name: library/packs/<name>.json (enabled.json and the examples folder skipped)."""
    d = packs_dir(lib_dir)
    if d is None or not d.is_dir():
        return []
    return [(f.stem, f) for f in sorted(d.glob("*.json")) if f.name != ENABLED_FILE and f.is_file()]


def load_pack(name: str, lib_dir: Optional[str | os.PathLike] = None) -> Optional[dict]:
    for n, f in pack_files(lib_dir):
        if n == name:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return data if isinstance(data, dict) else None
    return None


def prompt_rules(lib_dir: Optional[str | os.PathLike] = None) -> list[tuple[str, list[str]]]:
    """[(pack, rules)] for the packs that are on, in name order; rules are the pack file's `prompt_rules` strings."""
    on = enabled_set(lib_dir)
    out = []
    for name, _ in pack_files(lib_dir):
        if name not in on:
            continue
        data = load_pack(name, lib_dir) or {}
        rules = [str(r).strip() for r in (data.get("prompt_rules") or []) if isinstance(r, str) and str(r).strip()]
        if rules:
            out.append((name, rules))
    return out


def example_specs(lib_dir: Optional[str | os.PathLike] = None, loadable: bool = True) -> list[Path]:
    """library/packs/examples/<pack>/<slug>/spec.json of the packs that are on (sorted). With `loadable` (default) an
    example whose spec does not load with the packs that are on (for example a printed + sculpt example while pack
    printed is off: "not available yet") is left out, so nobody is shown or builds a spec it cannot use (d-to-e 9)."""
    d = packs_dir(lib_dir)
    if d is None:
        return []
    on = enabled_set(lib_dir)
    out: list[Path] = []
    for name in sorted(on):
        ex = d / "examples" / name
        if ex.is_dir():
            out += sorted(p for p in ex.glob("*/spec.json") if p.is_file())
    if loadable:
        out = [p for p in out if _loads(p)]
    return out


def _loads(p: Path) -> bool:
    try:
        from .spec import load_spec  # imported here: packs.py stays importable on its own
    except Exception:  # noqa: BLE001 - without the spec module the file is kept
        return True
    try:
        load_spec(p)
    except Exception:  # noqa: BLE001 - SpecError: a field of a pack that is off, or a broken file
        return False
    return True


__all__ = ["enabled", "enabled_set", "visible", "row_packs", "pack_files", "load_pack", "prompt_rules", "example_specs",
           "library_dir", "packs_dir"]
