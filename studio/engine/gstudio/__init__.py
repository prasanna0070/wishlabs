"""Gadget Studio engine.

Importing `gstudio` is cheap: it never imports cadquery, OCP, pcbnew, vtk or PlatformIO. Stage packages
(gstudio.cad, gstudio.pcb, gstudio.firmware, gstudio.manual, gstudio.order) are imported only when used.

Lazy top-level names (resolved on first access):
  load_spec, validate_spec, Spec, spec_json_schema   from gstudio.spec
  Library                                           from gstudio.library
  (use `from gstudio.normalize import normalize`; the submodule name would shadow a lazy function)
  BuildContext                                      from gstudio.context
"""

from __future__ import annotations

import importlib
from typing import Any

from .errors import SpecError, StageError
from .version import ENGINE_VERSION, SPEC_SCHEMA

__version__ = ENGINE_VERSION

_LAZY = {
    "load_spec": ("spec", "load_spec"),
    "validate_spec": ("spec", "validate_spec"),
    "Spec": ("spec", "Spec"),
    "spec_json_schema": ("spec", "spec_json_schema"),
    "Library": ("library", "Library"),
    "BuildContext": ("context", "BuildContext"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod, attr = _LAZY[name]
        value = getattr(importlib.import_module(f".{mod}", __name__), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'gstudio' has no attribute {name!r}")


__all__ = ["ENGINE_VERSION", "SPEC_SCHEMA", "SpecError", "StageError", *_LAZY]
