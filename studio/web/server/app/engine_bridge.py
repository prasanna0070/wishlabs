"""Adapter to the light part of the engine (gstudio.spec, gstudio.library, gstudio.normalize).

The web image bundles only those modules (no CadQuery). Everything here degrades gracefully while the
foundation package is still being written: the schema falls back to ARCHITECTURE.md §3 as text, the library
table falls back to a compact table built from library/modules.json, and validation falls back to minimal
shape checks (clearly reported as such).
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger("gstudio.engine")

TOP_KEYS = {"schema", "name", "slug", "tagline", "audience", "region", "colors", "body", "decor", "features",
            "electronics", "behaviour", "safety"}
FAMILIES = {"silhouette", "revolve", "blob"}


class EngineBridge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._loaded = False
        self.spec_mod = None
        self.lib_mod = None
        self.norm_mod = None
        self.library = None
        self.import_error: str | None = None

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            eng = str(self.settings.engine_dir)
            if eng not in sys.path and Path(eng).is_dir():
                sys.path.insert(0, eng)
            try:
                self.spec_mod = importlib.import_module("gstudio.spec")
            except Exception as exc:  # noqa: BLE001 - engine may not exist yet
                self.import_error = f"gstudio.spec: {exc}"
            try:
                self.lib_mod = importlib.import_module("gstudio.library")
                self.library = self._make_library()
            except Exception as exc:  # noqa: BLE001
                self.import_error = (self.import_error or "") + f" gstudio.library: {exc}"
            try:
                self.norm_mod = importlib.import_module("gstudio.normalize")
            except Exception as exc:  # noqa: BLE001
                self.import_error = (self.import_error or "") + f" gstudio.normalize: {exc}"
            if self.import_error:
                log.warning("engine partly unavailable: %s", self.import_error)
            self._loaded = True

    def _make_library(self):
        Library = getattr(self.lib_mod, "Library")
        for args in ((), (self.settings.library_dir,), (str(self.settings.library_dir / "modules.json"),)):
            try:
                return Library(*args)
            except TypeError:
                continue
        return Library()

    @property
    def available(self) -> bool:
        """True when gstudio.spec imports (schema validation works); normalize/library are used when present."""
        self._load()
        return self.spec_mod is not None

    @property
    def complete(self) -> bool:
        self._load()
        return self.spec_mod is not None and self.norm_mod is not None and self.library is not None

    # ------------------------------------------------------------------ prompt material
    def spec_schema_text(self) -> str:
        self._load()
        fn = getattr(self.spec_mod, "spec_json_schema", None) if self.spec_mod else None
        Spec = getattr(self.spec_mod, "Spec", None) if self.spec_mod else None
        if fn is not None or Spec is not None:
            try:
                sch = fn(compact=True) if fn is not None else Spec.model_json_schema()
                return json.dumps(sch, separators=(",", ":"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Spec.model_json_schema failed: %s", exc)
        return self._schema_from_architecture()

    def _schema_from_architecture(self) -> str:
        p = self.settings.docs_dir / "ARCHITECTURE.md"
        try:
            text = p.read_text()
        except FileNotFoundError:
            return "(spec schema unavailable)"
        m = re.search(r"## 3\. Design spec.*?(?=\n## 4\.)", text, re.S)
        return m.group(0) if m else text[:8000]

    def library_table(self) -> str:
        self._load()
        if self.library is not None and hasattr(self.library, "summary_for_llm"):
            try:
                return str(self.library.summary_for_llm())
            except Exception as exc:  # noqa: BLE001
                log.warning("summary_for_llm failed: %s", exc)
        return self._library_from_json()

    def fit_rules_text(self) -> str:
        """Hard fit rules computed from the engine's own data (backplane-v1 cartridge keep-out, module face and
        depth), so the agent gets them right the first time instead of through repair rounds. Silent on failure."""
        self._load()
        lines: list[str] = []
        try:
            bp = self.library.backplane if self.library is not None else None
            bp = bp() if callable(bp) else bp
            cart = bp["cartridge"]
            kx0, kx1, ky0, ky1 = cart["keepout_plan_board"]
            kz = float(cart["keepout_height_above_floor"])
            clear = float(getattr(self.norm_mod, "CLEAR", 3.0))
            gap = float(getattr(self.norm_mod, "APERTURE_MARGIN", 3.0))
            wall = 2.4
            need_w = (kx1 - kx0) + 2 * clear + 2 * wall
            need_d = (ky1 - ky0) + clear + float(cart.get("rear_gap_mm", 0.2)) + 2 * wall
            h = kz + wall
            lines += [
                f"- Core cartridge: it lies flat on the inner floor at the rear. The body must be at least {need_w:.0f} mm "
                f"wide and {need_d:.0f} mm deep (front to back) over its first {h:.0f} mm of height, so plan a body depth "
                f"of {need_d + 10:.0f} mm or more and a wide, flat-bottomed lower body.",
                f"- Modules behind the front wall must clear the cartridge: a module that reaches below z = {h + 2:.0f} "
                "collides unless the body is deeper; put deep modules (big button, 40 mm speaker) higher on the face.",
                f"- Openings (feature apertures, grilles) need {gap:.0f} mm clear between each other and from engraved "
                "decor (eyes, mouth, face plate edge); modules behind the wall must not overlap each other.",
            ]
        except Exception as exc:  # noqa: BLE001
            log.info("fit rules unavailable: %s", exc)
        rows = []
        for m in self.raw_modules():
            ap = m.get("aperture") or {}
            if not ap.get("feature_allowed") or not (m.get("backplane_v1") or {}).get("supported", True):
                continue
            face = m.get("face_mm")
            depth = (m.get("depth_mm") or 0) + (m.get("harness_clearance_mm") or 0)
            if face and depth:
                rows.append(f"{m['id']}: front {float(face[0]):g} x {float(face[1]):g} mm, needs {float(depth):g} mm "
                            "behind the wall (module + cable)")
        if rows:
            lines.append("- Feature modules (footprint on the face, depth behind the wall):\n  " + "\n  ".join(rows))
        lines += ["- Outline points at least 1 mm apart; do not repeat the first point at the end.",
                  "- Overall size 60-260 mm on every axis, depth included."]
        return "\n".join(lines)

    def raw_modules(self) -> list[dict]:
        d = self.settings.library_dir
        mods: list[dict] = []
        files = [d / "modules.json"] if (d / "modules.json").exists() else sorted(d.glob("modules-*.json"))
        for f in files:
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                data = data.get("modules", [])
            mods.extend(m for m in data if isinstance(m, dict) and m.get("id"))
        return mods

    def _library_from_json(self) -> str:
        mods = self.raw_modules()
        if not mods:
            return "(module library not available yet)"
        rows = ["id | role | name | dims_mm | aperture | price_inr"]
        for m in mods:
            dims = m.get("dims_mm")
            if isinstance(dims, dict):
                dims = "x".join(str(dims.get(k)) for k in ("w", "d", "h", "x", "y", "z") if k in dims)
            ap = m.get("aperture")
            ap_s = (ap.get("needs") or ap.get("kind") or ap.get("type")) if isinstance(ap, dict) else ("yes" if ap else "-")
            rows.append(f"{m['id']} | {m.get('role', '?')} | {m.get('name') or m.get('title') or ''} | "
                        f"{dims or ''} | {ap_s or '-'} | {m.get('price_inr_planning', '')}")
        return "\n".join(rows)

    def module_names(self) -> dict[str, dict]:
        """id -> {name, role} for brief parts lists."""
        out: dict[str, dict] = {}
        for m in self.raw_modules():
            out[m["id"]] = {"name": m.get("name") or m.get("title") or m["id"], "role": m.get("role", "")}
        self._load()
        if self.library is not None and not out:
            for attr in ("modules", "_modules", "by_id"):
                coll = getattr(self.library, attr, None)
                if isinstance(coll, dict):
                    for k, v in coll.items():
                        name = getattr(v, "name", None) or (v.get("name") if isinstance(v, dict) else None)
                        role = getattr(v, "role", None) or (v.get("role") if isinstance(v, dict) else "")
                        out[k] = {"name": name or k, "role": role or ""}
                    break
        return out

    # ------------------------------------------------------------------ validation
    def validate(self, spec: Any) -> dict:
        """{ok, errors: [str], warnings: [str], normalized: dict|None, engine: 'local'|'minimal'}"""
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"spec is not valid JSON: {exc}"], "warnings": [],
                        "normalized": None, "engine": "minimal"}
        if not isinstance(spec, dict):
            return {"ok": False, "errors": ["spec must be a JSON object"], "warnings": [], "normalized": None,
                    "engine": "minimal"}
        if self.complete:
            try:
                return self._validate_pipeline(spec)
            except ImportError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.exception("pipeline.validate crashed")
                return {"ok": False, "errors": [f"validator crashed: {type(exc).__name__}: {exc}"[:400]],
                        "warnings": [], "normalized": None, "engine": "local"}
        if self.available:
            try:
                return self._validate_engine(spec)
            except Exception as exc:  # noqa: BLE001 - surface engine crashes as errors, not 500s
                log.exception("engine validation crashed")
                return {"ok": False, "errors": [f"validator crashed: {type(exc).__name__}: {exc}"[:400]],
                        "warnings": [], "normalized": None, "engine": "local"}
        return self._validate_minimal(spec)

    def _validate_pipeline(self, spec: dict) -> dict:
        """The engine's own contract: gstudio.pipeline.validate -> {ok, errors, normalized, warnings}."""
        pipeline = importlib.import_module("gstudio.pipeline")
        r = pipeline.validate(spec)
        return {"ok": bool(r.get("ok")), "errors": [str(e) for e in r.get("errors") or []],
                "warnings": [str(w) for w in r.get("warnings") or []], "normalized": r.get("normalized"),
                "engine": "local"}

    def _validate_engine(self, spec: dict) -> dict:
        """gstudio.spec.load_spec (schema + cross checks), then gstudio.normalize.normalize (library, fit rules,
        implied parts). Both raise gstudio.errors.SpecError whose .errors are {path, msg, fix?} dicts."""
        load = getattr(self.spec_mod, "load_spec", None)
        try:
            model = load(spec) if load else getattr(self.spec_mod, "Spec").model_validate(spec)
        except Exception as exc:  # noqa: BLE001 - SpecError / ValidationError
            return {"ok": False, "errors": _exc_errors(exc), "warnings": [], "normalized": None, "engine": "local"}
        fn = (getattr(self.norm_mod, "normalize", None) or getattr(self.norm_mod, "normalize_spec", None)) \
            if self.norm_mod is not None else None
        if not callable(fn) or self.library is None:
            ids = set(self.module_names())
            errs = []
            if ids:
                for k, mid in [("electronics.core", model.electronics.core)] + \
                        [(f"features[{f.id}].module", f.module) for f in model.features]:
                    if mid not in ids:
                        errs.append(f"{k}: '{mid}' is not in the library")
            return {"ok": not errs, "errors": errs,
                    "warnings": ["engine normalize/library not available yet; schema and library-id checks only"],
                    "normalized": _dump(model) if not errs else None, "engine": "local-partial"}
        params = list(inspect.signature(fn).parameters)
        try:
            res = fn(model, self.library) if len(params) >= 2 else fn(model)
        except Exception as exc:  # noqa: BLE001 - SpecError with .errors, or a crash
            errs = _exc_errors(exc)
            if not getattr(exc, "errors", None) and type(exc).__name__ not in ("SpecError", "ValidationError"):
                log.exception("normalize crashed")
                errs = [f"validator crashed: {type(exc).__name__}: {exc}"[:400]]
            return {"ok": False, "errors": errs, "warnings": [], "normalized": None, "engine": "local"}
        out = _interpret_normalize_result(res, model)
        derived = (out.get("normalized") or {}).get("derived") or {}
        out["warnings"] = list(out.get("warnings") or []) + [str(w) for w in derived.get("warnings") or []]
        return out

    def _validate_minimal(self, spec: dict) -> dict:
        errors: list[str] = []
        extra = sorted(set(spec) - TOP_KEYS)
        if extra:
            errors.append(f"unknown top-level keys: {', '.join(extra)}")
        if spec.get("schema") != "gadget-spec/1":
            errors.append('schema must be "gadget-spec/1"')
        for k in ("name", "slug", "body", "features", "electronics", "behaviour"):
            if k not in spec:
                errors.append(f"{k}: required")
        body = spec.get("body") or {}
        if body and body.get("family") not in FAMILIES:
            errors.append("body.family must be silhouette, revolve or blob")
        ids = set(self.module_names())
        feats = spec.get("features") or []
        seen = set()
        for i, f in enumerate(feats if isinstance(feats, list) else []):
            fid = f.get("id") if isinstance(f, dict) else None
            if not fid:
                errors.append(f"features[{i}].id: required")
                continue
            if fid in seen:
                errors.append(f"features[{fid}]: duplicate id")
            seen.add(fid)
            if ids and f.get("module") not in ids:
                errors.append(f"features[{fid}].module: '{f.get('module')}' is not in the library")
        core = (spec.get("electronics") or {}).get("core")
        if ids and core and core not in ids:
            errors.append(f"electronics.core: '{core}' is not in the library")
        return {"ok": not errors, "errors": errors,
                "warnings": ["engine validator not available here; minimal checks only"],
                "normalized": spec if not errors else None, "engine": "minimal"}


def _dump(model: Any) -> dict:
    if hasattr(model, "to_json_dict"):
        return model.to_json_dict(include_derived=True)
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True, exclude_none=False)
    if isinstance(model, dict):
        return model
    return json.loads(json.dumps(model, default=str))


def _err_str(e: Any) -> str:
    if isinstance(e, str):
        return e
    if isinstance(e, dict):
        loc = e.get("loc") or e.get("path") or e.get("field") or "spec"
        msg = e.get("msg") or e.get("message") or json.dumps(e)
        fix = e.get("fix")
        s = f"{_loc(loc)}: {msg}" if loc else str(msg)
        return f"{s} Fix: {fix}" if fix else s
    for a in ("message", "msg"):
        if hasattr(e, a):
            return str(getattr(e, a))
    return str(e)


def _loc(loc: Any) -> str:
    if isinstance(loc, (list, tuple)):
        out = ""
        for p in loc:
            out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
        return out
    return str(loc)


def _exc_errors(exc: Any) -> list[str]:
    errs = getattr(exc, "errors", None)
    if callable(errs):            # pydantic ValidationError.errors()
        return format_validation_error(exc)
    if isinstance(errs, (list, tuple)) and errs:
        return [_err_str(e) for e in errs][:30]
    return [str(exc)[:600] or type(exc).__name__]


def format_validation_error(exc: Any) -> list[str]:
    errs = getattr(exc, "errors", None)
    if callable(errs):
        try:
            items = errs()
            return [f"{_loc(e.get('loc'))}: {e.get('msg')}" for e in items][:25]
        except Exception:  # noqa: BLE001
            pass
    return [str(exc)[:800]]


def _interpret_normalize_result(res: Any, model: Any) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    normalized: Any = None
    if isinstance(res, tuple):
        for item in res:
            if isinstance(item, list) and all(isinstance(x, str) for x in item):
                warnings.extend(item)
            elif normalized is None:
                normalized = item
    elif isinstance(res, dict) and ("errors" in res or "ok" in res):
        errors = [_err_str(e) for e in res.get("errors") or []]
        warnings = [_err_str(w) for w in res.get("warnings") or []]
        normalized = res.get("spec") or res.get("normalized")
    else:
        for attr in ("errors",):
            v = getattr(res, attr, None)
            if isinstance(v, list):
                errors = [_err_str(e) for e in v]
        v = getattr(res, "warnings", None)
        if isinstance(v, list):
            warnings = [_err_str(w) for w in v]
        normalized = getattr(res, "spec", None) or res
    if normalized is None:
        normalized = model
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "normalized": _dump(normalized) if not errors else None, "engine": "local"}
