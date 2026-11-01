"""BUILDER=local: run the engine CLI in a subprocess on this machine (the dev VM), PYTHONPATH=engine.

    python -m gstudio build <spec.json> <out> [--fast]
    python -m gstudio preview <spec.json> <outdir>
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from ..config import Settings
from ..engine_bridge import EngineBridge
from ..projects import vkey
from .base import BridgeValidateMixin, BuilderError, status_doc

log = logging.getLogger("gstudio.builder.local")


class LocalBuilder(BridgeValidateMixin):
    kind = "local"

    supports_seed = True          # runs in the version folder (or a temp copy of it), so `only` builds work

    def __init__(self, settings: Settings, bridge: EngineBridge, store):
        self.settings = settings
        self.bridge = bridge
        self.store = store
        self.python = os.environ.get("ENGINE_PYTHON", sys.executable)

    def _env(self) -> dict:
        env = dict(os.environ)
        eng = str(self.settings.engine_dir)
        env["PYTHONPATH"] = eng + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    async def _run(self, args: list[str], timeout: float, log_path: Path | None = None) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            self.python, "-m", "gstudio", *args, cwd=str(self.settings.studio_root), env=self._env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, f"timed out after {timeout:.0f} s"
        text = (out or b"").decode(errors="replace")
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(text)
        return proc.returncode or 0, text

    async def preview(self, spec: dict) -> dict:
        """`python -m gstudio preview` prints {ok, png, glb, warnings, errors} on stdout (logs go to stderr)."""
        with tempfile.TemporaryDirectory(prefix="gs-preview-") as td:
            sp = Path(td) / "spec.json"
            sp.write_text(json.dumps(spec))
            out = Path(td) / "out"
            proc = await asyncio.create_subprocess_exec(
                self.python, "-m", "gstudio", "preview", str(sp), str(out), cwd=str(self.settings.studio_root),
                env=self._env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                so, se = await asyncio.wait_for(proc.communicate(), self.settings.preview_timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"ok": False, "png": None, "glb": None, "warnings": [],
                        "errors": [f"preview timed out after {self.settings.preview_timeout_s:.0f} s"]}
            try:
                r = json.loads(so.decode() or "{}")
            except ValueError:
                return {"ok": False, "png": None, "glb": None, "warnings": [],
                        "errors": [f"preview crashed (exit {proc.returncode}): {se.decode(errors='replace')[-500:]}"]}
            png = Path(r["png"]) if r.get("png") else None
            glb = Path(r["glb"]) if r.get("glb") else None
            ok = bool(r.get("ok")) and png is not None and png.exists()
            return {"ok": ok, "png": png.read_bytes() if ok else None,
                    "glb": glb.read_bytes() if ok and glb and glb.exists() else None,
                    "warnings": [str(w) for w in r.get("warnings") or []],
                    "errors": [] if ok else [str(e) for e in r.get("errors") or ["preview produced no image"]]}

    async def build(self, pid: str, n: int, spec: dict, brief: dict | None, options: dict) -> dict:
        direct = self.store.local_path(vkey(pid, n, "status.json"))
        tmp = None
        if direct is not None:
            out = direct.parent
        else:
            tmp = tempfile.TemporaryDirectory(prefix=f"gs-build-{pid}-v{n}-")
            out = Path(tmp.name)
        out.mkdir(parents=True, exist_ok=True)
        if direct is None and options.get("only"):
            await asyncio.to_thread(self._download, f"projects/{pid}/v{n}", out)
        spec_path = out.parent / f".input-spec-v{n}.json" if direct is not None else out / ".input-spec.json"
        spec_path.write_text(json.dumps(spec, indent=1))
        if brief is not None:
            (spec_path.parent / f".brief-v{n}.json").write_text(json.dumps(brief, indent=1))
        args = ["build", str(spec_path), str(out), "--version", str(n), "--project-id", pid, "--quiet"] + \
            (["--fast"] if options.get("fast") else [])
        if brief is not None:
            args += ["--brief-file", str(spec_path.parent / f".brief-v{n}.json")]
        extra = engine_options(options, brief)
        if extra:
            opt_path = spec_path.parent / (f".options-v{n}.json" if direct is not None else ".options.json")
            opt_path.write_text(json.dumps(extra, indent=1))
            args += ["--options-file", str(opt_path)]
        status_path = out / "status.json"
        if not status_path.exists():
            status_path.write_text(json.dumps(status_doc("running", percent=0)))
        mirror_task = None
        if direct is None:
            mirror_task = asyncio.create_task(self._mirror_status(status_path, vkey(pid, n, "status.json")))
        t0 = time.time()
        try:
            code, text = await self._run(args, self.settings.build_timeout_s,
                                         log_path=out.parent / f".build-v{n}.log" if direct is not None
                                         else out / ".build.log")
        finally:
            if mirror_task:
                mirror_task.cancel()
        st = _read_json(status_path) or {}
        if code != 0 and st.get("state") != "error":
            st = {**status_doc("error", st.get("stages"), st.get("percent"),
                               error=f"pipeline exited {code}: {text.strip().splitlines()[-1][:300] if text.strip() else ''}")}
            status_path.write_text(json.dumps(st))
        elif code == 0 and st.get("state") not in ("done", "error"):
            st = {**st, "state": "done", "percent": 100}
            status_path.write_text(json.dumps(st))
        if direct is None:
            await asyncio.to_thread(self.store.put_tree, out, f"projects/{pid}/v{n}",
                                    lambda rel: not rel.startswith("."))
            tmp.cleanup()
        return {"ok": code == 0 and st.get("state") == "done", "seconds": round(time.time() - t0, 1),
                "state": st.get("state"), "error": st.get("error")}

    def _download(self, prefix: str, out: Path) -> int:
        """GCS store: bring a seeded version folder into the temp dir so an `only` run finds the earlier outputs."""
        n = 0
        for key in self.store.list_keys(prefix):
            rel = key[len(prefix.rstrip("/")) + 1:]
            if not rel or rel == "status.json":
                continue
            b = self.store.read_bytes(key)
            if b is None:
                continue
            t = out / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(b)
            n += 1
        return n

    async def _mirror_status(self, path: Path, key: str) -> None:
        last = None
        while True:
            await asyncio.sleep(2)
            try:
                b = path.read_bytes()
            except OSError:
                continue
            if b != last:
                await asyncio.to_thread(self.store.write_bytes, key, b, "application/json")
                last = b


def engine_options(options: dict, brief: dict | None) -> dict:
    """What goes into --options-file: everything except the flags the CLI already carries, plus the brief's version
    and concept id (MAJ-36: project.json records them)."""
    extra = {k: v for k, v in (options or {}).items() if k not in ("fast",) and v is not None}
    if brief:
        if brief.get("version") is not None:
            extra.setdefault("brief_version", brief.get("version"))
        if brief.get("concept_id"):
            extra.setdefault("concept_id", brief.get("concept_id"))
    return extra


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


__all__ = ["LocalBuilder", "BuilderError"]
