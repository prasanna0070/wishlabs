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
            self._refresh_packs()
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

    def _packs_mod(self):
        """gstudio.packs (CATALOG-PLAN 0.5), or None while the engine has no pack switch."""
        try:
            return importlib.import_module("gstudio.packs")
        except Exception:  # noqa: BLE001
            return None

    def _refresh_packs(self) -> None:
        """Rebuild the Library when the set of packs that are on changed (enabled.json edited, env differs)."""
        lib = self.library
        was = getattr(lib, "packs_on", None)
        pm = self._packs_mod() if was is not None else None
        if pm is None:
            return
        try:
            now = pm.enabled_set(getattr(lib, "dir", None) or self.settings.library_dir)
        except Exception:  # noqa: BLE001
            return
        if now != was:
            with self._lock:
                if getattr(self.library, "packs_on", None) != now:
                    try:
                        self.library = self._make_library()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("library reload for packs failed: %s", exc)

    def packs_on(self) -> frozenset:
        """Names of the packs that are on (empty when the engine has no pack switch)."""
        self._load()
        pm = self._packs_mod()
        try:
            return frozenset(pm.enabled_set(self.settings.library_dir)) if pm else frozenset()
        except Exception:  # noqa: BLE001
            return frozenset()

    def pack_prompt_rules(self) -> list[tuple[str, list[str]]]:
        """[(pack, rules)] of the packs that are on (library/packs/<name>.json prompt_rules)."""
        self._load()
        pm = self._packs_mod()
        try:
            return list(pm.prompt_rules(self.settings.library_dir)) if pm else []
        except Exception as exc:  # noqa: BLE001
            log.warning("pack prompt rules unavailable: %s", exc)
            return []

    def pack_example_paths(self) -> list[Path]:
        """library/packs/examples/<pack>/<slug>/spec.json of the packs that are on."""
        self._load()
        pm = self._packs_mod()
        try:
            return list(pm.example_specs(self.settings.library_dir)) if pm else []
        except Exception:  # noqa: BLE001
            return []

    def pack_files_signature(self) -> tuple:
        """What the cached system prompt depends on beyond the library file: the switch and every pack file."""
        on = tuple(sorted(self.packs_on()))
        files = []
        d = self.settings.library_dir / "packs"
        if d.is_dir():
            for f in sorted(d.rglob("*.json")):
                try:
                    files.append((str(f), f.stat().st_mtime_ns))
                except OSError:
                    continue
        return (on, tuple(files))

    def role_available(self, role: str) -> bool:
        """True when the agent can design with a part of this role (pack parts only while their pack is on)."""
        self._load()
        fn = getattr(self.library, "role_available", None)
        try:
            return bool(fn(role)) if callable(fn) else False
        except Exception:  # noqa: BLE001
            return False

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
            gadget_only = " (gadgets only: a printed object has no electronics)" if "printed" in self.packs_on() else ""
            lines += [
                f"- Core cartridge{gadget_only}: it lies flat on the inner floor at the rear. The body must be at least {need_w:.0f} mm "
                f"wide and {need_d:.0f} mm deep (front to back) over its first {h:.0f} mm of height, so plan a body depth "
                f"of {need_d + 10:.0f} mm or more and a wide, flat-bottomed lower body. On a silhouette body the front "
                "and rear edge rounds eat into that floor: add the edge_round to the depth (or keep edge_round small).",
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
        ring = getattr(self.norm_mod, "NIGHT_LIGHT_RING", None) if self.norm_mod is not None else None
        ring_row = next((m for m in self.raw_modules() if m.get("id") == ring), None) if ring else None
        if ring_row and ring_row.get("face_mm"):
            fw, fh = (float(v) for v in ring_row["face_mm"][:2])
            lines.append(f"- Night light: a NIGHT_USE design gets the {ring} ring ({fw:g} x {fh:g} mm on the face, behind a "
                         "diffuser) as its light feature (the validator swaps a small LED for it). Plan a clear band "
                         f"about {fh + 10:g} mm tall for it across the front face, with the speaker below it and the "
                         "eyes above it, or put it on the top; keep it clear of every other kit.")
        try:
            lines += self._port_rules()
        except Exception as exc:  # noqa: BLE001
            log.info("port rules unavailable: %s", exc)
        printed = "printed" in self.packs_on()
        lines += ["- Outline points at least 1 mm apart; do not repeat the first point at the end.",
                  "- Overall size 60-260 mm on every axis, depth included"
                  + (" (a printed object with no electronics: 20-260 mm)." if printed else ".")]
        return "\n".join(lines)

    def _port_rules(self) -> list[str]:
        """Port capacity in plain words, from the parts the agent can use now (pack parts only while their pack is on);
        normalize enforces it (slot_full, i2c_clash)."""
        lib = self.library
        if lib is None or not hasattr(lib, "agent_visible"):
            return []
        io: list[str] = []
        five: list[str] = []
        for m in lib.modules:
            if not lib.agent_visible(m):
                continue
            slots = list((m.get("backplane_v1") or {}).get("slots") or [])
            word = PORT_WORDS.get(m.get("role")) or str((m.get("manual") or {}).get("word") or m.get("role") or "").replace("_", " ")
            if slots and all(str(x).startswith("J_IO") for x in slots):
                if word and word not in io:
                    io.append(word)
            elif slots == ["J_LED"] and word and word not in five:
                five.append(word)
        if not io and not five:
            return []
        txt = "- Ports (the validator rejects more): "
        if io:
            txt += f"3 simple ports shared by {_and(io)}; "
        txt += "3 I2C ports, and every I2C part needs its own address"
        if five:
            txt += f"; one 5 V port for {' or '.join(five)}" + (", so a design has only one of them" if len(five) > 1 else "")
        return [txt + "."]

    def raw_modules(self) -> list[dict]:
        """Library rows straight from the JSON; rows of packs that are off are left out (CATALOG-PLAN 0.5.3), and
        every pack row is left out when the engine cannot say which packs are on (fail closed)."""
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
        if any(m.get("pack") for m in mods):
            on = self.packs_on()
            pm = self._packs_mod()
            mods = [m for m in mods if not m.get("pack") or (pm is not None and pm.visible(m, on=on))]
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
        """The engine's own contract: gstudio.pipeline.validate -> {ok, errors, normalized, warnings, nudges?}."""
        pipeline = importlib.import_module("gstudio.pipeline")
        r = pipeline.validate(spec)
        return {"ok": bool(r.get("ok")), "errors": [str(e) for e in r.get("errors") or []],
                "warnings": [str(w) for w in r.get("warnings") or []], "normalized": r.get("normalized"),
                "nudges": nudges_from(r), "engine": "local"}

    # ------------------------------------------------------------------ partial builds (MIN-37)
    def stage_ids(self) -> tuple[str, ...]:
        """The engine's stage ids (gstudio.pipeline.STAGE_IDS), else the documented default list."""
        self._load()
        try:
            return tuple(importlib.import_module("gstudio.pipeline").STAGE_IDS)
        except Exception:  # noqa: BLE001
            return DEFAULT_STAGES

    def electrical_for(self, spec: dict) -> dict | None:
        """electrical.json the engine would write for this spec (gstudio.electrical.build_electrical on the
        normalized spec), or None when the engine is not available here."""
        self._load()
        if not self.complete:
            return None
        try:
            el = importlib.import_module("gstudio.electrical")
            load = getattr(self.spec_mod, "load_spec", None)
            model = load(spec) if load else self.spec_mod.Spec.model_validate(spec)
            res = self.norm_mod.normalize(model, self.library)
            sp = res[0] if isinstance(res, tuple) else res
            return el.build_electrical(sp, self.library)
        except Exception as exc:  # noqa: BLE001
            log.info("electrical_for unavailable: %s", exc)
            return None

    def with_defaults(self, spec: Any) -> Any:
        """The spec with every schema default filled in (no normalize, no derived), so a key the model left out
        is not reported as a change when its default equals the old value (MAJ-34). The input when unavailable."""
        self._load()
        if self.spec_mod is None or not isinstance(spec, dict):
            return spec
        try:
            load = getattr(self.spec_mod, "load_spec", None)
            model = load(spec) if load else self.spec_mod.Spec.model_validate(spec)
            d = model.to_json_dict(include_derived=False) if hasattr(model, "to_json_dict") else _dump(model)
            d.pop("derived", None)
            return d
        except Exception:  # noqa: BLE001
            return spec

    def nudges_from_cad(self, spec: dict, components: Any, cad_checks: Any) -> list | None:
        """foundation's gstudio.normalize.nudges_from_cad for a built version (MAJ-8); None when unavailable."""
        self._load()
        fn = getattr(self.norm_mod, "nudges_from_cad", None) if self.norm_mod is not None else None
        if not callable(fn) or not isinstance(components, dict) or not cad_checks:
            return None
        try:
            load = getattr(self.spec_mod, "load_spec", None)
            model = load(spec) if load else self.spec_mod.Spec.model_validate(spec)
            return list(fn(model, components, cad_checks) or [])
        except Exception as exc:  # noqa: BLE001
            log.info("nudges_from_cad unavailable: %s", exc)
            return None

    def decor_fields(self) -> dict[str, set[str]]:
        """decor type -> the field names its schema model accepts (MAJ-34: which shape words have a parameter)."""
        self._load()
        out: dict[str, set[str]] = {}
        if self.spec_mod is None:
            return out
        for cls_name in ("FacePlate", "Eye", "Mouth", "Blush", "Ear", "Text"):
            cls = getattr(self.spec_mod, cls_name, None)
            fields = getattr(cls, "model_fields", None) if cls is not None else None
            if not fields:
                continue
            t = fields.get("type")
            ann = getattr(t, "annotation", None)
            args = getattr(ann, "__args__", None) or ()
            key = str(args[0]) if args else cls_name.lower()
            out[key] = set(fields)
        return out

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
        out["nudges"] = nudges_from(out)
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


DEFAULT_STAGES = ("normalize", "electrical", "cad", "pcb", "firmware", "order", "manual", "checks", "docs", "package")


PORT_WORDS = {"button": "buttons", "touch": "touch pads", "buzzer": "a buzzer", "motion": "a PIR motion sensor",
              "hall": "a magnet (hall) sensor", "endstop": "an end-stop switch", "light_sensor": "an LDR light sensor",
              "led": "an LED ring or pixel", "servo": "a servo arm", "servo_continuous": "a continuous servo",
              "soil_moisture": "a soil probe", "level_switch": "a float switch"}


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def nudges_from(r: Any) -> list:
    """Layout nudges from the validator (MAJ-8), wherever foundation puts them: top-level `nudges` or
    `conflicts`, or normalized.derived.{nudges, layout_conflicts}. Each kept as given (str or dict)."""
    if not isinstance(r, dict):
        return []
    out: list = []
    for k in ("nudges", "conflicts", "layout_conflicts"):
        v = r.get(k)
        if isinstance(v, list):
            out += v
    der = ((r.get("normalized") or {}).get("derived") or {}) if isinstance(r.get("normalized"), dict) else {}
    for k in ("nudges", "layout_conflicts"):
        v = der.get(k)
        if isinstance(v, list):
            out += [x for x in v if x not in out]
    return out


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
