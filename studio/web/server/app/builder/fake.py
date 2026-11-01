"""BUILDER=fake: no pipeline. A build copies a pre-generated example output tree (examples/<slug>/_out/) into the
version, walking status.json through the real stage list so the UI can show progress. When no example output
exists yet, it writes a small, clearly-labelled placeholder version instead (placeholder GLB/PNG, minimal
build.json with no prices and no passed tests)."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

from ..config import Settings
from ..engine_bridge import EngineBridge
from ..projects import vkey
from ..util import now_iso
from . import placeholder
from .base import BridgeValidateMixin, status_doc

log = logging.getLogger("gstudio.builder.fake")

STAGES = [("electrical", "Wiring and pins"), ("cad", "Shell and CAD"), ("pcb", "Backplane PCB"),
          ("firmware", "Firmware"), ("order", "Print slicing and price"), ("manual", "Build manual"),
          ("checks", "Digital checks"), ("package", "Export")]


FAKE_LINES = {
    "electrical": "Planning the wiring and pin map for the chosen parts.",
    "cad": "Modelling the shell and fitting the parts inside.",
    "pcb": "Laying out the backplane board.",
    "firmware": "Writing and compiling the firmware.",
    "order": "Slicing the print files and working out the price.",
    "manual": "Drawing the build manual from the model.",
    "checks": "Running the digital checks.",
    "package": "Packaging the files for download.",
}


class FakeBuilder(BridgeValidateMixin):
    kind = "fake"

    def __init__(self, settings: Settings, bridge: EngineBridge, store):
        self.settings = settings
        self.bridge = bridge
        self.store = store

    # ------------------------------------------------------------------ example lookup
    def example_out(self, spec: dict) -> Path | None:
        if not self.settings.fake_use_examples:
            return None
        ex = self.settings.examples_dir
        cands = []
        if self.settings.fake_slug:
            cands.append(ex / self.settings.fake_slug / "_out")
        if spec.get("slug"):
            cands.append(ex / str(spec["slug"]) / "_out")
        cands.append(ex / "bedtime-robot" / "_out")
        if ex.is_dir():
            cands += sorted(p / "_out" for p in ex.iterdir() if p.is_dir())
        for c in cands:
            if (c / "build.json").exists() or (c / "spec.json").exists():
                return c
        return None

    # ------------------------------------------------------------------ preview
    async def preview(self, spec: dict) -> dict:
        png = await asyncio.to_thread(placeholder.render_png, spec, 384)
        glb = await asyncio.to_thread(placeholder.render_glb, spec)
        return {"ok": True, "png": png, "glb": glb, "warnings": ["placeholder preview (fake builder), not CAD"],
                "errors": []}

    # ------------------------------------------------------------------ build
    async def build(self, pid: str, n: int, spec: dict, brief: dict | None, options: dict) -> dict:
        skey = vkey(pid, n, "status.json")
        stages = [{"id": i, "label": lab, "state": "pending", "started": None, "ended": None, "message": ""}
                  for i, lab in STAGES]
        per = max(self.settings.fake_build_seconds, 0.0) / len(stages)
        msgs: list[dict] = []
        for k, st in enumerate(stages):
            st["state"], st["started"] = "running", now_iso()
            msgs.append({"text": FAKE_LINES.get(st["id"], f"Working on {st['label'].lower()}."), "ts": now_iso(),
                         "stage": st["id"]})
            await asyncio.to_thread(self.store.write_json, skey,
                                    status_doc("running", stages, percent=round(100 * k / len(stages)),
                                               messages=msgs, fake=True))
            await asyncio.sleep(per)
            st["state"], st["ended"], st["message"] = "done", now_iso(), "fake builder"
            if st["id"] == "cad":   # live 3D draft, as the real CAD stage writes early
                await asyncio.to_thread(self.store.write_bytes, vkey(pid, n, "cad/draft.glb"),
                                        placeholder.render_glb(spec, "Work in progress"), "model/gltf-binary")
        src = self.example_out(spec)
        if src is not None:
            n_files = await asyncio.to_thread(self.store.put_tree, src, f"projects/{pid}/v{n}",
                                              lambda rel: rel != "status.json")
            note = f"copied {n_files} files from examples/{src.parent.name}/_out (fake builder)"
        else:
            await asyncio.to_thread(self._write_placeholder_version, pid, n, spec, brief)
            note = "placeholder outputs (fake builder; no example output exists yet)"
        msgs.append({"text": "Packaging finished (development builder: placeholder outputs).", "ts": now_iso(),
                     "stage": "package"})
        final = status_doc("done", stages, percent=100, fake=True, note=note, messages=msgs)
        await asyncio.to_thread(self.store.write_json, skey, final)
        return {"ok": True, "state": "done", "note": note}

    def _write_placeholder_version(self, pid: str, n: int, spec: dict, brief: dict | None) -> None:
        files: dict[str, bytes] = {}
        name = spec.get("name") or "Gadget"
        files["spec.json"] = json.dumps(spec, indent=1).encode()
        files["project.json"] = json.dumps({"schema": "gadget-project/1", "name": name, "slug": spec.get("slug"),
                                            "version": n, "created": now_iso(), "engine_version": "fake",
                                            "brief": brief, "placeholder": True}, indent=1).encode()
        files["cad/assembly.glb"] = placeholder.render_glb(spec)
        comps = {"PRINT_Shell": {"kind": "PRINT", "label": "Shell", "spec_path": "body", "placeholder": True}}
        for i, f in enumerate(spec.get("features") or []):
            camel = "".join(w.capitalize() for w in str(f.get("id", f"f{i}")).split("_"))
            comps[f"PURCHASE_{camel}"] = {"kind": "PURCHASE", "label": f.get("label") or f.get("id"),
                                          "feature_id": f.get("id"), "module_id": f.get("module"),
                                          "spec_path": f"features[{i}]", "placeholder": True}
        for i, d in enumerate(spec.get("decor") or []):
            comps[f"REFERENCE_Decor{i}"] = {"kind": "REFERENCE", "label": f"{d.get('type', 'decor')} {i}",
                                            "spec_path": f"decor[{i}]", "placeholder": True}
        files["cad/components.json"] = json.dumps(comps, indent=1).encode()
        files["manual/assembled.png"] = placeholder.render_png(spec, 512)
        purchased = [{"name": f.get("label") or f.get("id"), "quantity": 1, "requiresSoldering": False,
                      "module_id": f.get("module"), "price_inr_planning": None, "price_inr_seen": None,
                      "listing": None, "notes": "placeholder row (fake builder)", "modelPart": None}
                     for f in spec.get("features") or []]
        build = {
            "schema": "gadget-build/1", "placeholder": True,
            "purchased": purchased,
            "printed": [{"name": "PRINT_Shell", "file": None, "material": "PETG",
                         "color": (spec.get("colors") or {}).get("body"), "grams": None, "hours": None,
                         "supports": None, "orientation_note": "placeholder"}],
            "steps": [
                {"id": "print-shell", "title": "Print the shell", "instruction": "**Use:** PRINT_Shell x1\n**Tools:** "
                 "3D printer\n\nPlaceholder step from the fake builder.", "expected": "A printed shell.",
                 "parts": ["PRINT_Shell"], "highlight": ["PRINT_Shell"], "camera": None,
                 "visuals": [{"path": "manual/assembled.png", "alt": "Placeholder front view",
                              "caption": "Placeholder image, not a CAD render."}]},
                {"id": "fit-core", "title": "Fit the core cartridge", "instruction": "**Use:** core cartridge x1\n"
                 "**Tools:** none\n\nPlaceholder step.", "expected": "Cartridge seated.",
                 "parts": ["PRINT_Shell"], "highlight": [], "camera": None, "visuals": []}],
            "wiring": {"runtime": "esp32-arduino", "board": "esp32-s3-devkitc-1", "code": None,
                       "parts": [], "connections": [], "steps": []},
            "tests": [{"title": "Print the fit coupon and check the button fit", "expected": "Button clicks in.",
                       "kind": "physical", "status": "not_run"},
                      {"title": "Listen at bedside volume", "expected": "Comfortable, clear audio.",
                       "kind": "physical", "status": "not_run"},
                      {"title": "30-minute closed-case warmth check", "expected": "Case stays cool to touch.",
                       "kind": "physical", "status": "not_run"},
                      {"title": "Table stability nudge test", "expected": "Does not tip.",
                       "kind": "physical", "status": "not_run"}],
            "firmware": {"framework": "arduino", "board": "esp32-s3-devkitc-1", "compiled": False, "size": None,
                         "tests_passed": 0, "tests_total": 0},
            "pcb": {"layers": 2, "size_mm": None, "drc_errors": None, "erc_errors": None, "routed": False,
                    "unrouted": None, "files": []},
            "order": {"list_price_inr": None, "status": "NOT_QUOTED"},
        }
        files["build.json"] = json.dumps(build, indent=1).encode()
        files["order/quote.json"] = json.dumps({"status": "NOT_QUOTED", "list_price_inr": None,
                                                "reasons": ["placeholder version from the fake builder"]},
                                               indent=1).encode()
        files["checks/digital-checks.json"] = json.dumps({"checks": [], "placeholder": True}).encode()
        files["BUILD_GUIDE.md"] = (f"# {name}\n\nPlaceholder build guide from the fake builder. "
                                   "No CAD, PCB or firmware was generated.\n").encode()
        manifest = {"engine_version": "fake", "created": now_iso(),
                    "files": [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                              for p, b in sorted(files.items())]}
        files["manifest.json"] = json.dumps(manifest, indent=1).encode()
        buf = io.BytesIO()
        slug = spec.get("slug") or "gadget"
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p, b in sorted(files.items()):
                z.writestr(f"{slug}/{p}", b)
        files["export.zip"] = buf.getvalue()
        for p, b in files.items():
            self.store.write_bytes(vkey(pid, n, p), b)
