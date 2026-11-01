"""gstudio-builder: the private heavy-compute service (ARCHITECTURE §10).

    GET  /health                              {ok, version, engine_version, builds_running, max_builds, tools}
    POST /validate {spec}                     {ok, errors, normalized, warnings}
    POST /preview  {spec}                     {ok, png_b64, glb_b64, warnings, errors}
    POST /build    {project_id, version, spec, brief?, options?}
         -> runs `python -m gstudio build` into a temp dir; mirrors files + status.json to
            gs://$BUCKET/projects/<pid>/v<n>/ while it runs (status.json on every change, each finished stage's
            files as they land, everything at the end, status.json last); returns the build summary.

At most MAX_BUILDS (default 2) builds run at once per instance; a further /build gets 429 `rate_limited`.
Errors use the web API's shape: {"error": {"code", "message", "details"}}.

Env: BUCKET (unset = local only, nothing uploaded), STUDIO_ROOT (default: parent of this folder), MAX_BUILDS,
BUILD_TIMEOUT_S (default 3300, under Cloud Run's 3600 s request limit), WORK_DIR, KEEP_WORK=1 (debug).
Run: uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures as cf
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
STUDIO_ROOT = Path(os.environ.get("STUDIO_ROOT", HERE.parent))
ENGINE = STUDIO_ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

BUCKET = os.environ.get("BUCKET", "").strip()
MAX_BUILDS = int(os.environ.get("MAX_BUILDS", "2"))
BUILD_TIMEOUT_S = float(os.environ.get("BUILD_TIMEOUT_S", "3300"))
PREVIEW_TIMEOUT_S = float(os.environ.get("PREVIEW_TIMEOUT_S", "180"))
WORK_DIR = Path(os.environ.get("WORK_DIR", tempfile.gettempdir())) / "gstudio-builds"
KEEP_WORK = os.environ.get("KEEP_WORK") == "1"
SERVICE_VERSION = "builder-0.1.0"
PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SKIP_DIRS = {".pio", "__pycache__", "node_modules", ".cache", ".git"}

log = logging.getLogger("gstudio.builder")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="gstudio-builder", version=SERVICE_VERSION)
_running = 0
_lock = asyncio.Lock()
_pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="gcs")

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("model/stl", ".stl")
mimetypes.add_type("application/step", ".step")
mimetypes.add_type("text/markdown", ".md")
TEXT_EXT = {".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_prl", ".gbr", ".drl", ".brep", ".log", ".ini", ".h", ".cpp", ".txt", ".csv"}


def content_type(path: Path) -> str:
    """Known text formats first (the system table maps .gbr to application/rpki-ghostbusters), then mimetypes."""
    if path.suffix.lower() in TEXT_EXT:
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def err(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}}, status_code=status)


def engine_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    for v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        env.setdefault(v, "1")
    return env


# ============================================================================ GCS mirror


class Mirror:
    """Uploads changed files under `out` to gs://BUCKET/<prefix>/. No-op when BUCKET is unset."""

    def __init__(self, out: Path, prefix: str):
        self.out, self.prefix = out, prefix.strip("/")
        self.sent: dict[str, tuple[int, int]] = {}
        self.uploaded = 0
        self.errors: list[str] = []
        self._bucket = None
        if BUCKET:
            from google.cloud import storage  # lazy: not needed for local runs

            self._bucket = storage.Client().bucket(BUCKET)

    @property
    def enabled(self) -> bool:
        return self._bucket is not None

    def url(self) -> Optional[str]:
        return f"gs://{BUCKET}/{self.prefix}/" if self.enabled else None

    def _put(self, rel: str, path: Path) -> None:
        blob = self._bucket.blob(f"{self.prefix}/{rel}")
        ext = path.suffix.lower()
        ctype = content_type(path)
        if rel == "status.json":
            blob.cache_control = "no-store"
        for attempt in range(3):
            try:
                blob.upload_from_filename(str(path), content_type=ctype, timeout=120)
                return
            except Exception as exc:  # noqa: BLE001 - transient GCS errors: retry, then record
                if attempt == 2:
                    raise
                log.warning("upload %s failed (%s), retrying", rel, exc)
                time.sleep(1.5 * (attempt + 1))

    def changed(self) -> list[tuple[str, Path]]:
        out = []
        for root, dirs, names in os.walk(self.out):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                p = Path(root) / n
                rel = p.relative_to(self.out).as_posix()
                if rel == "status.json" or n.endswith(".tmp"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                key = (st.st_size, st.st_mtime_ns)
                if self.sent.get(rel) != key:
                    out.append((rel, p))
        return out

    async def sync(self) -> int:
        if not self.enabled:
            return 0
        todo = self.changed()
        loop = asyncio.get_running_loop()

        async def one(rel: str, p: Path):
            try:
                st = p.stat()
                await loop.run_in_executor(_pool, self._put, rel, p)
                self.sent[rel] = (st.st_size, st.st_mtime_ns)
                self.uploaded += 1
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{rel}: {exc}")
                log.error("upload failed for %s: %s", rel, exc)

        await asyncio.gather(*(one(r, p) for r, p in todo))
        return len(todo)

    async def put_file(self, rel: str) -> None:
        """Upload one file now (used for the live CAD draft). Paths must stay inside the out dir."""
        p = (self.out / rel).resolve()
        if not self.enabled or self.out.resolve() not in p.parents or not p.is_file():
            return
        try:
            st = p.stat()
            await asyncio.get_running_loop().run_in_executor(_pool, self._put, rel, p)
            self.sent[rel] = (st.st_size, st.st_mtime_ns)
            self.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{rel}: {exc}")

    async def put_status(self) -> None:
        p = self.out / "status.json"
        if self.enabled and p.exists():
            try:
                await asyncio.get_running_loop().run_in_executor(_pool, self._put, "status.json", p)
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"status.json: {exc}")


def write_status(out: Path, state: str, error: Optional[str], stages: Optional[list] = None, percent: float = 0.0) -> None:
    out.mkdir(parents=True, exist_ok=True)
    doc = {"state": state, "percent": percent, "stages": stages or [], "error": error, "failed": [],
           "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = out / ".status.json.tmp"
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(out / "status.json")


# ============================================================================ routes


@app.get("/health")
async def health():
    from gstudio.version import ENGINE_VERSION

    tools = {t: bool(shutil.which(t)) for t in ("prusa-slicer", "kicad-cli", "pio", "xvfb-run")}
    tools["java"] = bool(os.environ.get("GS_JAVA") and Path(os.environ["GS_JAVA"]).exists())
    tools["freerouting"] = bool(os.environ.get("GS_FREEROUTING_JAR") and Path(os.environ["GS_FREEROUTING_JAR"]).exists())
    return {"ok": True, "version": SERVICE_VERSION, "engine_version": ENGINE_VERSION, "builds_running": _running,
            "max_builds": MAX_BUILDS, "bucket": BUCKET or None, "tools": tools}


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise ValueError("body must be JSON")
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return body


@app.post("/validate")
async def validate(request: Request):
    try:
        body = await _json(request)
    except ValueError as exc:
        return err(422, "invalid", str(exc))
    if not isinstance(body.get("spec"), dict):
        return err(422, "invalid", "spec must be an object", {"errors": ["spec: must be an object"]})
    try:
        from gstudio.pipeline import validate as _validate

        r = await asyncio.to_thread(_validate, body["spec"])
    except Exception as exc:  # noqa: BLE001 - an engine fault, not a spec problem
        log.exception("validate failed")
        return err(502, "builder", f"validate failed inside the builder: {type(exc).__name__}: {exc}")
    return {"ok": r["ok"], "errors": r["errors"], "error_items": r.get("error_items", []), "normalized": r["normalized"],
            "warnings": r.get("warnings", [])}


@app.post("/preview")
async def preview(request: Request):
    try:
        body = await _json(request)
    except ValueError as exc:
        return err(422, "invalid", str(exc))
    if not isinstance(body.get("spec"), dict):
        return err(422, "invalid", "spec must be an object")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="preview-", dir=WORK_DIR))
    try:
        (work / "spec.json").write_text(json.dumps(body["spec"]))
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "gstudio", "preview", str(work / "spec.json"), str(work / "out"),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=engine_env())
        try:
            so, se = await asyncio.wait_for(proc.communicate(), PREVIEW_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            return err(502, "builder", f"preview timed out after {PREVIEW_TIMEOUT_S:.0f} s")
        try:
            r = json.loads(so.decode() or "{}")
        except ValueError:
            return err(502, "builder", "preview crashed", {"stderr": se.decode(errors="replace")[-3000:]})
        png = Path(r["png"]) if r.get("png") else None
        glb = Path(r["glb"]) if r.get("glb") else None
        out = {"ok": bool(r.get("ok")), "png_b64": base64.b64encode(png.read_bytes()).decode() if png and png.exists() else None,
               "glb_b64": base64.b64encode(glb.read_bytes()).decode() if glb and glb.exists() else None,
               "warnings": r.get("warnings", []), "errors": r.get("errors", []), "seconds": r.get("seconds")}
        if not out["ok"] and out["errors"] and proc.returncode == 1 and not png:
            return JSONResponse(out, status_code=422)
        return out
    finally:
        if not KEEP_WORK:
            shutil.rmtree(work, ignore_errors=True)


@app.post("/build")
async def build(request: Request):
    global _running
    try:
        body = await _json(request)
    except ValueError as exc:
        return err(422, "invalid", str(exc))
    pid, ver = str(body.get("project_id") or ""), body.get("version")
    if not PID_RE.match(pid):
        return err(422, "invalid", "project_id must match [A-Za-z0-9_-]{1,64}")
    if not isinstance(ver, int) or ver < 1 or ver > 100000:
        return err(422, "invalid", "version must be a positive integer")
    if not isinstance(body.get("spec"), dict):
        return err(422, "invalid", "spec must be an object")
    async with _lock:
        if _running >= MAX_BUILDS:
            return err(429, "rate_limited", f"{_running} builds already running on this builder (max {MAX_BUILDS}); retry shortly")
        _running += 1
    try:
        return await _run_build(pid, ver, body)
    finally:
        async with _lock:
            _running -= 1


async def _run_build(pid: str, ver: int, body: dict) -> Any:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{pid}-v{ver}-", dir=WORK_DIR))
    out = work / "out"
    out.mkdir()
    mirror = Mirror(out, f"projects/{pid}/v{ver}")
    t0 = time.monotonic()
    try:
        (work / "spec.json").write_text(json.dumps(body["spec"]))
        opts = dict(body.get("options") or {})
        opts = {k: v for k, v in opts.items() if not str(k).startswith("_") and k != "stage_modules"}  # no test hooks over HTTP
        opts.update({"project_id": pid, "version": ver})
        for k in ("brief", "brief_version", "concept_id"):
            if body.get(k) is not None:
                opts[k] = body[k]
        (work / "options.json").write_text(json.dumps(opts))
        write_status(out, "queued", None)
        await mirror.put_status()
        cmd = [sys.executable, "-m", "gstudio", "build", str(work / "spec.json"), str(out), "--options-file", str(work / "options.json"),
               "--version", str(ver), "--project-id", pid, "--json", "--quiet"]
        log.info("build %s v%s: %s", pid, ver, " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    env=engine_env(), start_new_session=True)
        stdout_task = asyncio.create_task(proc.stdout.read())
        stderr_task = asyncio.create_task(proc.stderr.read())
        last, last_states, timed_out = None, {}, False
        while True:
            try:
                await asyncio.wait_for(proc.wait(), 1.0)
                break
            except asyncio.TimeoutError:
                pass
            if time.monotonic() - t0 > BUILD_TIMEOUT_S:
                timed_out = True
                await _kill_group(proc)
                break
            last, last_states = await _tick(out, mirror, last, last_states)
        so, se = await stdout_task, await stderr_task
        try:
            summary = json.loads(so.decode() or "null")
        except ValueError:
            summary = None
        if timed_out or summary is None:
            why = (f"build timed out after {BUILD_TIMEOUT_S:.0f} s" if timed_out
                   else f"engine exited with code {proc.returncode} without a summary")
            st = _read_json(out / "status.json") or {}
            write_status(out, "error", why, st.get("stages"), st.get("percent", 0))
            (out / "logs").mkdir(exist_ok=True)
            (out / "logs" / "builder-stderr.log").write_text(se.decode(errors="replace")[-200000:])
            summary = {"state": "error", "error": why, "stages": {}, "seconds": round(time.monotonic() - t0, 1)}
        await mirror.sync()
        await mirror.put_status()  # last: readers treat a done status as "all files are there"
        summary.update({"project_id": pid, "version": ver, "gcs": mirror.url(), "uploaded_files": mirror.uploaded,
                        "upload_errors": mirror.errors[:20], "builder_seconds": round(time.monotonic() - t0, 1)})
        if not mirror.enabled:
            summary["local_out"] = str(out)
        return summary
    except Exception as exc:  # noqa: BLE001 - always leave an error status behind
        log.exception("build %s v%s crashed", pid, ver)
        write_status(out, "error", f"builder error: {type(exc).__name__}: {exc}")
        await mirror.put_status()
        return err(502, "builder", f"builder error: {type(exc).__name__}: {exc}")
    finally:
        if not KEEP_WORK and mirror.enabled:
            shutil.rmtree(work, ignore_errors=True)


async def _tick(out: Path, mirror: Mirror, last: Optional[str], last_states: dict) -> tuple[Optional[str], dict]:
    """Upload status.json when it changed; upload new files when any stage finished since the last tick; upload a
    new live CAD draft (status.draft / draft_rev) before the status that points at it."""
    p = out / "status.json"
    try:
        txt = p.read_text()
    except OSError:
        return last, last_states
    if txt == last:
        return last, last_states
    try:
        st = json.loads(txt)
    except ValueError:
        return last, last_states  # mid-write (should not happen: written atomically)
    states = {s["id"]: s["state"] for s in st.get("stages", [])}
    if st.get("draft") and st.get("draft_rev") != last_states.get("__draft_rev__"):
        await mirror.put_file(str(st["draft"]))
    states["__draft_rev__"] = st.get("draft_rev")
    finished = [k for k, v in states.items() if v in ("done", "failed", "skipped") and last_states.get(k) != v]
    if finished:
        await mirror.sync()
    if st.get("state") not in ("done", "error"):
        await mirror.put_status()  # a terminal status goes up only after the final full sync
    return txt, states


async def _kill_group(proc) -> None:
    """Stop the engine and every stage process under it (its own session), TERM first, then KILL."""
    import signal

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), 5)
            return
        except asyncio.TimeoutError:
            continue


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None
