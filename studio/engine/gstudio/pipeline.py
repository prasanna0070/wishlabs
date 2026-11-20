"""Build orchestration: spec -> normalize -> electrical -> (cad | pcb | firmware) -> order, manual -> checks -> finalize -> docs -> package.

    build(spec, out_dir, options=None, progress_cb=None) -> summary dict

* A small DAG scheduler. Light stages (normalize, electrical, checks, docs, package) run in this process. Heavy
  stages (cad, pcb, firmware, order, manual) each run in their own child process, so CPU-heavy work does not
  share one GIL, a crash (segfault in a native library) cannot take the build down, and per-stage stdout/stderr,
  including subprocess output such as `pio` or `kicad-cli`, is captured at the file-descriptor level into
  `out/logs/<stage>.log`.
* `status.json` is rewritten atomically after every stage transition (and, throttled, on progress messages):
  {state: queued|running|done|error, stages: [{id, label, state: queued|running|done|failed|skipped, started,
  ended, message, percent}], percent, error, failed: [...]}.
* Robust: a failed stage is marked `failed` and every stage that does not hard-depend on it still runs.
  A stage whose package or `run()` is not available yet is `skipped` with the reason. Nothing is faked.
* `only=[...]` runs the named stages plus the always-needed normalize and electrical. Stages that were not
  selected do not block their dependents: those read the previous outputs on disk.
* Every finished stage's record is saved to `out/.gstudio/stages/<id>.json` (summary, state, timings), so
  checks/docs/package can be re-run alone with `only`.
* REVIEW-1: `stories_audio` defaults to true for the story_player template (BLK-4). The CAD collision check is a gate
  (MAJ-6): on failure one repair round moves the clashing feature and rebuilds the CAD; if it still fails the version is
  `not_buildable` (summary/status `buildable`, `verdict`, `verdict_message`) and the order stage holds the quote.
  `finalize` runs gstudio.manual.finalize(ctx) after the checks so the guide's final checks match tests[] (MAJ-19).

Threading note: the child processes are forked (Linux). Call build() from a single-threaded process; the
builder service runs every build in its own `python -m gstudio build` subprocess for exactly this reason.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import importlib
import importlib.util
import json
import logging
import multiprocessing as mp
import os
import queue as _queue
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("gstudio.pipeline")

STATE_DIR = ".gstudio"
# ARCHITECTURE §5 outputs. A full build moves an earlier run's copies aside first, so nothing stale can be priced or shipped.
OUTPUT_NAMES = ("spec.json", "project.json", "electrical.json", "build.json", "PROJECT.md", "BUILD_GUIDE.md", "PARTS_EVIDENCE.md",
                "SOFTWARE_SETUP.md", "cad", "print", "pcb", "firmware", "manual", "checks", "order", "manifest.json", "export.zip",
                "status.json", "verify_delivery.py", "logs")
MAX_MESSAGES = 200
DRAFT_FILES = ("cad/draft.glb",)


# ============================================================================ stage table


@dataclass(frozen=True)
class StageDef:
    id: str
    label: str
    module: str                      # module path holding run(ctx); "" = built into the pipeline
    mode: str = "process"            # "process" | "inline"
    hard: tuple = ()                 # must have succeeded (when selected in this run)
    soft: tuple = ()                 # must have finished (any state) before this starts
    weight: float = 1.0              # share of the overall percent
    timeout: float = 1800.0          # seconds (process stages)


STAGES: tuple[StageDef, ...] = (
    StageDef("normalize", "Check the design", "", "inline", weight=1, timeout=120),
    StageDef("electrical", "Wiring and pin map", "gstudio.electrical", "inline", hard=("normalize",), weight=1, timeout=120),
    StageDef("cad", "Shell and CAD", "gstudio.cad", "process", hard=("normalize", "electrical"), weight=30, timeout=1500),
    StageDef("pcb", "Backplane PCB", "gstudio.pcb", "process", hard=("normalize", "electrical"), weight=30, timeout=1800),
    StageDef("firmware", "Firmware", "gstudio.firmware", "process", hard=("normalize", "electrical"), weight=25, timeout=1500),
    StageDef("order", "Print slicing and price", "gstudio.order", "process", hard=("normalize", "electrical"),
             soft=("cad", "pcb"), weight=8, timeout=900),  # pcb: the BP1 PCBA line is priced from pcb/pcba.json (MAJ-22)
    # manual reads build.json purchased/printed (order) and reports firmware/PCB results in its final checks and guide,
    # so it starts after those have finished (in any state), never on half-written outputs.
    StageDef("manual", "Build manual", "gstudio.manual", "process", hard=("normalize", "electrical", "cad"),
             soft=("order", "pcb", "firmware"), weight=20, timeout=1500),
    StageDef("checks", "Digital checks", "gstudio.checks", "inline", hard=("normalize",),
             soft=("cad", "pcb", "firmware", "order", "manual"), weight=2, timeout=300),
    # finalize re-renders the guide's final checks, known-issue banner and manual-summary from checks/digital-checks.json
    StageDef("finalize", "Final guide checks", "gstudio.manual", "inline", hard=("normalize",), soft=("checks", "manual"), weight=1,
             timeout=300),
    StageDef("docs", "Project documents", "gstudio.docs_gen", "inline", hard=("normalize",), soft=("checks", "finalize"), weight=1, timeout=120),
    StageDef("package", "Package and export", "gstudio.package", "inline", hard=("normalize",), soft=("docs",), weight=3, timeout=600),
)
STAGE_IDS = tuple(s.id for s in STAGES)
ALWAYS = ("normalize", "electrical")
ALIASES = {"docs_gen": "docs", "slice": "order", "quote": "order", "bom": "order", "final": "finalize"}
GATE_FILE = "gate.json"  # .gstudio/gate.json: the MAJ-6 collision gate verdict for this version


def parse_only(only: Any) -> Optional[list[str]]:
    """'cad,pcb' | ['cad','pcb'] | None -> validated stage ids (None = all)."""
    if not only:
        return None
    items = only.split(",") if isinstance(only, str) else list(only)
    out = []
    for raw in items:
        s = ALIASES.get(str(raw).strip(), str(raw).strip())
        if not s:
            continue
        if s not in STAGE_IDS:
            raise ValueError(f"unknown stage {raw!r}; stages are: {', '.join(STAGE_IDS)}")
        if s not in out:
            out.append(s)
    return out or None


# ============================================================================ status


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=_json_default))
    os.replace(tmp, path)


def _json_default(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=_json_default))


@dataclass
class StageRec:
    id: str
    label: str
    state: str = "queued"
    started: Optional[str] = None
    ended: Optional[str] = None
    message: str = ""
    percent: float = 0.0
    seconds: Optional[float] = None
    skip_kind: Optional[str] = None      # not_selected | unavailable | dependency
    error: Optional[str] = None
    details: Any = None
    summary: Any = None
    t0: float = 0.0

    def public(self) -> dict:
        d = {"id": self.id, "label": self.label, "state": self.state, "started": self.started, "ended": self.ended,
             "message": self.message, "percent": round(self.percent, 1)}
        if self.seconds is not None:
            d["seconds"] = round(self.seconds, 2)
        if self.error:
            d["error"] = self.error[:2000]
        return d


class Status:
    """Owns status.json. Every transition writes it; progress messages write at most every `throttle` s."""

    def __init__(self, out: Path, defs: list[StageDef], progress_cb: Optional[Callable[[dict], None]] = None,
                 throttle: float = 1.0):
        self.out, self.defs, self.cb, self.throttle = out, {d.id: d for d in defs}, progress_cb, throttle
        self.recs = {d.id: StageRec(d.id, d.label) for d in defs}
        self.state, self.error, self.started = "queued", None, now_iso()
        self._last = 0.0
        self.messages: list[dict] = []     # plain-English lines for the chat: {at, stage, text}
        self.draft: Optional[str] = None   # e.g. "cad/draft.glb" while the CAD stage refines a live draft
        self.draft_rev = 0
        self.verdict: dict = {}            # MAJ-6 {buildable, verdict, verdict_message, pairs, repair}

    def percent(self) -> float:
        tot = done = 0.0
        for sid, r in self.recs.items():
            w = self.defs[sid].weight
            if r.skip_kind == "not_selected":
                continue
            tot += w
            if r.state in ("done", "failed", "skipped"):
                done += w
            elif r.state == "running":
                done += w * max(0.02, min(0.97, r.percent / 100.0))
        return 100.0 * done / tot if tot else 100.0

    def as_dict(self) -> dict:
        failed = [r.id for r in self.recs.values() if r.state == "failed"]
        d = {"state": self.state, "percent": round(self.percent(), 1) if self.state != "done" else 100.0,
             "stages": [r.public() for r in self.recs.values()], "error": self.error, "failed": failed,
             "messages": self.messages[-MAX_MESSAGES:], "draft": self.draft, "draft_rev": self.draft_rev,
             "started": self.started, "updated": now_iso()}
        if self.verdict:
            d.update({k: self.verdict.get(k) for k in ("buildable", "verdict", "verdict_message")})
        return d

    def say(self, sid: str, text: str, write: bool = True) -> None:
        t = " ".join(str(text or "").split())[:400]
        if not t or (self.messages and self.messages[-1]["text"] == t):
            return
        self.messages.append({"at": now_iso(), "stage": sid, "text": t})
        if write:
            self.write(force=False)

    def set_draft(self, rel: str) -> None:
        self.draft, self.draft_rev = rel, self.draft_rev + 1
        self.write()

    def write(self, force: bool = True) -> None:
        t = time.monotonic()
        if not force and t - self._last < self.throttle:
            return
        self._last = t
        d = self.as_dict()
        try:
            write_json_atomic(self.out / "status.json", d)
        except OSError as exc:  # pragma: no cover - disk full etc.
            log.error("could not write status.json: %s", exc)
        if self.cb:
            try:
                self.cb(d)
            except Exception as exc:  # noqa: BLE001 - a broken callback must not kill the build
                log.warning("progress callback failed: %s", exc)

    # transitions ---------------------------------------------------------------------------
    def start(self, sid: str, message: str = "") -> None:
        r = self.recs[sid]
        r.state, r.started, r.t0, r.message, r.percent = "running", now_iso(), time.monotonic(), message or "started", 0.0
        self.state = "running"
        self.write()

    def progress(self, sid: str, message: str, percent: Optional[float]) -> None:
        r = self.recs.get(sid)
        if r is None or r.state != "running":
            return
        if message:
            r.message = str(message)[:300]
        if percent is not None:
            try:
                r.percent = max(r.percent, max(0.0, min(100.0, float(percent))))
            except (TypeError, ValueError):
                pass
        self.write(force=False)

    def finish(self, sid: str, state: str, message: str = "", *, error: str | None = None, details: Any = None,
               summary: Any = None, skip_kind: str | None = None) -> None:
        r = self.recs[sid]
        r.state, r.ended = state, now_iso()
        if r.started is None:
            r.started = r.ended
        r.seconds = (time.monotonic() - r.t0) if r.t0 else 0.0
        r.message = (message or r.message or state)[:500]
        r.percent = 100.0
        r.error, r.details, r.summary, r.skip_kind = error, details, summary, skip_kind
        try:
            line = completion_line(sid, r.label, state, summary, r.message, error)
        except Exception:  # noqa: BLE001 - a chat line must never break a build
            line = None
        if line:
            self.say(sid, line, write=False)
        self.write()


# ============================================================================ chat lines


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def completion_line(sid: str, label: str, state: str, summary: Any, message: str, error: Optional[str]) -> Optional[str]:
    """One plain-English sentence for the chat when a stage ends. Built only from the stage's own numbers."""
    s = summary if isinstance(summary, dict) else {}
    if state == "failed":
        return f"{label} failed: {(error or message or 'see its log').splitlines()[0][:200]}"
    if state == "skipped":
        return None if "not selected" in (message or "") else f"{label} skipped: {message}"
    if s.get("say"):
        return str(s["say"])
    if sid == "normalize":
        return "Checked the design against the module library."
    if sid == "electrical" and s.get("slots_used") is not None:
        return (f"Planned the wiring: {s.get('instances', '?')} modules on {len(s['slots_used'])} backplane slots"
                + (f", {s['nets']} nets." if s.get("nets") is not None else "."))
    if sid == "pcb":
        drc = s.get("drc_errors", (s.get("pcb") or {}).get("drc_errors") if isinstance(s.get("pcb"), dict) else None)
        return f"Routed the backplane PCB: {int(drc)} DRC errors." if _num(drc) is not None else f"Built the backplane PCB: {message}"
    if sid == "firmware":
        fw = s.get("firmware") if isinstance(s.get("firmware"), dict) else s
        size = next((v for v in (_num(fw.get(k)) for k in ("bin_bytes", "size_bytes", "firmware_bytes")) if v), None)
        if size is None and isinstance(fw.get("size"), dict):
            size = _num(fw["size"].get("flash_bytes") or fw["size"].get("bin_bytes"))
        tp, tt = fw.get("tests_passed"), fw.get("tests_total")
        if fw.get("compiled") is False:
            return f"Firmware project generated but not compiled: {message}"
        bits = ["Firmware compiled" + (f" ({size / 1024:.0f} KB)" if size else "")]
        if tt is not None:
            bits.append(f"{tp}/{tt} unit tests passed")
        return ", ".join(bits) + "."
    if sid == "order" and isinstance(s.get("delivered_price_inr"), dict):
        dp = s["delivered_price_inr"]
        tipped = s.get("tipped") if isinstance(s.get("tipped"), dict) else {}
        return (f"Sliced the print parts ({s.get('print_g', 0):.0f} g, {s.get('print_h', 0):.1f} h) and priced the build: about "
                f"₹{dp.get('pilot_one_off', 0):,} for a one-off, ₹{dp.get('steady_state', 0):,} at volume (an estimate, incl. GST)."
                + "".join(f" {k} does not fit the printer upright, so it prints on its side ({(v or {}).get('label')})"
                          + (" with supports." if (v or {}).get("supports") else ".") for k, v in sorted(tipped.items())))
    if sid == "manual":
        st = s.get("steps")
        st = len(st) if isinstance(st, list) else st
        png, svg = image_counts(s)
        if st is not None:
            return (f"Wrote the build manual: {st} steps" + (f" with {png} PNG illustrations rendered from the CAD" if png is not None else "")
                    + (f" and {svg} SVG diagram{'s' if svg != 1 else ''}" if svg else "") + ".")
    if sid == "finalize" and s.get("tests") is not None:
        return f"Brought the guide's final checks up to date: {s['tests']} checks listed" + (
            f", {s['failed']} failed first" if s.get("failed") else "") + "."
    if sid == "checks" and isinstance(s.get("summary"), dict):
        c = s["summary"]
        return f"Digital checks: {c.get('passed', 0)} passed, {c.get('failed', 0)} failed, {c.get('warning', 0)} warnings."
    if sid == "package" and s.get("files") is not None:
        return f"Packaged {s['files']} files into export.zip ({(s.get('zip_bytes') or 0) / 1e6:.1f} MB) with a checksum manifest."
    if sid == "docs":
        return None
    return f"{label}: {message}" if message and message != "done" else f"{label} done."


def image_counts(s: dict) -> tuple[Optional[int], Optional[int]]:
    """(PNG, SVG) image counts from a manual summary or manual-summary.json: explicit counts, else the image list."""
    png, svg = s.get("images_png"), s.get("images_svg")
    imgs = s.get("images")
    if isinstance(imgs, list) and (png is None or svg is None):
        png = png if png is not None else sum(1 for x in imgs if str(x).lower().endswith(".png"))
        svg = svg if svg is not None else sum(1 for x in imgs if str(x).lower().endswith(".svg"))
    if png is None and isinstance(s.get("images_rendered"), int) and not isinstance(imgs, list):
        png = s["images_rendered"]
    return (int(png) if png is not None else None), (int(svg) if svg is not None else None)


# ============================================================================ context helpers


class _FallbackContext:
    """Used ONLY when gstudio.context is unavailable (never in a real build): same surface as BuildContext."""

    def __init__(self, spec, lib, out, options=None, on_progress=None):
        self.spec, self.lib, self.out = spec, lib, Path(out)
        self.electrical, self.artifacts, self.options = None, {}, dict(options or {})
        self.on_progress, self.warnings, self.normalize_report = on_progress, [], None

    def progress(self, stage_id, message, percent=None):
        if self.on_progress:
            with contextlib.suppress(Exception):
                self.on_progress(stage_id, message, percent)

    def warn(self, stage_id, msg):
        self.warnings.append({"stage": stage_id, "msg": msg})

    def path(self, rel):
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def option(self, key, default=None):
        return self.options.get(key, default)


def spec_hash(spec_dict: dict) -> str:
    """sha256 of the canonical user spec (engine-derived data excluded)."""
    d = {k: v for k, v in spec_dict.items() if k != "derived"}
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def spec_to_dict(spec: Any, include_derived: bool = True) -> dict:
    if hasattr(spec, "to_json_dict"):
        return spec.to_json_dict(include_derived)
    if hasattr(spec, "model_dump"):
        return spec.model_dump(mode="json", by_alias=True)
    return dict(spec)


def load_spec_input(spec: Any) -> Any:
    """dict | path | JSON string -> dict (or a Spec model, passed through)."""
    if isinstance(spec, (str, Path)) and not str(spec).lstrip().startswith("{"):
        return json.loads(Path(spec).read_text())
    if isinstance(spec, str):
        return json.loads(spec)
    return spec


def prepare_context(spec: Any, out: Path, options: dict, on_progress) -> Any:
    """Load + normalize the spec into foundation's BuildContext. Raises SpecError on an invalid spec."""
    from gstudio.context import BuildContext  # foundation

    lib = options.get("_library")
    return BuildContext.prepare(load_spec_input(spec), out, lib=lib, options=_public_options(options), on_progress=on_progress)


def apply_option_defaults(ctx: Any, spec_dict: dict) -> None:
    """BLK-4: story audio is part of a story_player delivery unless the caller explicitly said no."""
    opts = getattr(ctx, "options", None)
    if not isinstance(opts, dict):
        return
    tmpl = (spec_dict.get("behaviour") or {}).get("template")
    if opts.get("stories_audio") is None:
        opts["stories_audio"] = tmpl == "story_player"
        opts["stories_audio_basis"] = ("default for the story_player template" if tmpl == "story_player" else
                                       f"default off for the {tmpl} template")
    else:
        opts["stories_audio"] = bool(opts["stories_audio"])
        opts["stories_audio_basis"] = "set by the caller"


# ============================================================================ MAJ-6 collision gate


def read_collisions(out: Path) -> dict:
    """{buildable: True|False|None, pairs: [{parts, mm3}], detail} from cad/digital-checks.json's collisions check."""
    try:
        d = json.loads((Path(out) / "cad" / "digital-checks.json").read_text())
    except (OSError, ValueError):
        return {"buildable": None, "pairs": [], "detail": "no cad/digital-checks.json"}
    c = next((x for x in d.get("checks") or [] if isinstance(x, dict) and x.get("id") == "collisions"), None)
    if c is None:
        return {"buildable": None, "pairs": [], "detail": "the CAD reported no collisions check"}
    pairs = [{"parts": list(i.get("parts") or []), "mm3": i.get("intersection_mm3")}
             for i in ((c.get("data") or {}).get("intersections") or []) if isinstance(i, dict)]
    return {"buildable": c.get("status") != "failed", "pairs": pairs, "detail": c.get("detail")}


def pairs_text(pairs: list[dict]) -> str:
    return "; ".join(f"{' / '.join(p['parts'])} ({mm3(p.get('mm3'))} mm3)" for p in pairs)


def mm3(v) -> str:
    v = float(v or 0)
    return f"{v:.1f}" if v >= 0.1 else f"{v:.3f}"


V_AXIS = {"front": (4, 5), "rear": (4, 5), "back": (4, 5), "left": (4, 5), "right": (4, 5), "top": (2, 3)}


def repair_plan(spec_dict: dict, pairs: list[dict], comps: dict) -> Optional[dict]:
    """One repair: move the feature that clashes with another feature's kit along v (the face's second `at` coordinate),
    away from it, by the overlap along v plus 2 mm. None when no pair is between two different features."""
    feats = {f.get("id"): f for f in spec_dict.get("features") or [] if isinstance(f, dict)}
    vote: dict[str, list] = {}
    for p in pairs:
        names = p.get("parts") or []
        if len(names) != 2:
            continue
        ca, cb = (comps.get(n) or {} for n in names)
        fa, fb = ca.get("feature_id"), cb.get("feature_id")
        if not fa or not fb or fa == fb or fa not in feats or fb not in feats:
            continue
        ba, bb = ca.get("bbox"), cb.get("bbox")
        if not (isinstance(ba, list) and isinstance(bb, list) and len(ba) == 6 and len(bb) == 6):
            continue

        def vol(b):
            return max(0.0, (b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4]))
        mover, other, bm, bo = (fa, fb, ba, bb) if vol(ba) <= vol(bb) else (fb, fa, bb, ba)
        face = feats[mover].get("face") or "front"
        lo, hi = V_AXIS.get(face, (4, 5))
        overlap = min(bm[hi], bo[hi]) - max(bm[lo], bo[lo])
        if overlap <= 0:
            continue
        sign = 1.0 if (bm[lo] + bm[hi]) >= (bo[lo] + bo[hi]) else -1.0
        vote.setdefault(mover, []).append((overlap + 2.0, sign, other, names))
    if not vote:
        return None
    mover = max(vote, key=lambda k: (len(vote[k]), max(v[0] for v in vote[k])))
    dist, sign, other, names = max(vote[mover], key=lambda v: v[0])
    at = list(feats[mover].get("at") or [0, 0])
    new_at = [at[0], round(at[1] + sign * dist, 1)]
    return {"feature": mover, "away_from": other, "from_at": at, "to_at": new_at, "moved_mm": round(dist, 1),
            "direction": "up" if sign > 0 else "down", "pair": names,
            "why": f"{' / '.join(names)} overlap by {dist - 2.0:.1f} mm along v; moved {mover} {dist:.1f} mm "
                   f"{'up' if sign > 0 else 'down'} (overlap + 2 mm)"}


def apply_repair(spec_dict: dict, plan: dict) -> dict:
    sd = json.loads(json.dumps({k: v for k, v in spec_dict.items() if k != "derived"}))
    for f in sd.get("features") or []:
        if f.get("id") == plan["feature"]:
            f["at"] = plan["to_at"]
    return sd


def _public_options(options: dict) -> dict:
    return {k: v for k, v in options.items() if not k.startswith("_") and k not in ("stage_modules",)}


# ============================================================================ log capture (inline stages)


@contextlib.contextmanager
def capture_inline(log_path: Path):
    """Python-level capture for in-process stages: stdout, stderr and the `gstudio` loggers."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(fh)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    try:
        with contextlib.redirect_stdout(fh), contextlib.redirect_stderr(fh):
            yield fh
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        fh.close()


# ============================================================================ child process entry


def _child_main(sid: str, modname: str, ctx: Any, q: Any, log_path: str) -> None:  # pragma: no cover - runs in child
    """Run one stage in a forked child. Everything it prints, and its subprocesses print, goes to the log."""
    with contextlib.suppress(Exception):
        os.setpgrp()  # own process group: the parent kills grandchildren (java, pio, prusa-slicer) on timeout
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTERM, signal.SIG_DFL)  # the parent's handler is for the scheduler, not for stage code
    with contextlib.suppress(Exception):
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)  # survive someone removing logs/ mid-build
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    sys.stdout = open(1, "w", buffering=1, closefd=False, encoding="utf-8", errors="replace")
    sys.stderr = open(2, "w", buffering=1, closefd=False, encoding="utf-8", errors="replace")
    with contextlib.suppress(Exception):
        import faulthandler

        faulthandler.enable(file=sys.stderr, all_threads=True)  # a native crash's traceback goes to the stage log
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.INFO)
    print(f"[{now_iso()}] stage {sid}: {modname}.run (pid {os.getpid()})", flush=True)

    def on_progress(stage_id, message, percent=None):
        with contextlib.suppress(Exception):
            q.put(("progress", sid, str(message), percent))

    def say(stage_id, text):
        with contextlib.suppress(Exception):
            q.put(("say", sid, str(text)))

    with contextlib.suppress(Exception):
        ctx.on_progress = on_progress
    with contextlib.suppress(Exception):
        ctx.say = say  # plain-English chat line: ctx.say(stage_id, "Routed the backplane: 0 DRC errors")
    t0 = time.monotonic()
    try:
        mod = importlib.import_module(modname)
        fn = getattr(mod, "run", None)
        if not callable(fn):
            q.put(("result", sid, {"ok": False, "missing_run": True, "error": f"{modname} has no run(ctx) yet"}))
            return
        summary = fn(ctx)
        payload = {"ok": True, "summary": jsonable(summary if summary is not None else {}),
                   "artifacts": _portable(getattr(ctx, "artifacts", {}) or {}),
                   "warnings": jsonable(getattr(ctx, "warnings", []) or [])}
        print(f"[{now_iso()}] stage {sid}: done in {time.monotonic() - t0:.1f} s", flush=True)
        q.put(("result", sid, payload))
    except BaseException as exc:  # noqa: BLE001 - report every failure, including SystemExit
        tb = traceback.format_exc()
        print(tb, flush=True)
        q.put(("result", sid, _error_payload(exc, tb, ctx)))
    finally:
        with contextlib.suppress(Exception):
            sys.stdout.flush()
            sys.stderr.flush()


def _error_payload(exc: BaseException, tb: str, ctx: Any = None) -> dict:
    msg = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    details = getattr(exc, "details", None)
    is_stage_error = type(exc).__name__ == "StageError"
    return {"ok": False, "error": msg if is_stage_error else f"{type(exc).__name__}: {msg}",
            "details": jsonable(details) if details else None, "traceback": tb[-6000:],
            "artifacts": _portable(getattr(ctx, "artifacts", {}) or {}) if ctx is not None else {}}


def _portable(d: dict) -> dict:
    """Artifacts that survive pickling to the parent (Paths and plain data); others become strings."""
    import pickle

    out = {}
    for k, v in d.items():
        try:
            pickle.dumps(v)
            out[k] = v
        except Exception:  # noqa: BLE001
            out[k] = str(v)
    return out


# ============================================================================ the scheduler


class Terminated(Exception):
    """SIGTERM received (builder timeout, Cloud Run shutdown): stop the stage processes and record an error."""


def _on_sigterm(signum, frame):  # pragma: no cover - exercised through the builder
    raise Terminated()


@dataclass
class _Running:
    proc: Any
    t0: float
    timeout: float
    dead_at: Optional[float] = None


@dataclass
class Pipeline:
    spec: Any
    out: Path
    options: dict = field(default_factory=dict)
    progress_cb: Optional[Callable[[dict], None]] = None

    def __post_init__(self):
        self.out = Path(self.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.only = parse_only(self.options.get("only"))
        if self.only is None:
            self.archive_previous()
        (self.out / "logs").mkdir(exist_ok=True)
        (self.out / STATE_DIR / "stages").mkdir(parents=True, exist_ok=True)
        self.defs = list(STAGES)
        self.status = Status(self.out, self.defs, self.progress_cb)
        self.ctx: Any = None
        self.modules = dict(self.options.get("stage_modules") or {})
        self.max_parallel = int(self.options.get("jobs") or 4)
        self.timeouts = dict(self.options.get("timeouts") or {})
        self._draft_check = 0.0
        self._draft_seen: dict = {}
        self._draft_published: dict = {}
        self._q: Any = None
        self._running: dict = {}
        self.repair: Optional[dict] = None      # MAJ-6 repair round applied in this build
        self.verdict: dict = {}

    # -------------------------------------------------------------------- helpers
    def archive_previous(self) -> None:
        """Move an earlier run's outputs (and stage records) to .gstudio/previous/ (replacing the one before)."""
        import shutil

        found = [n for n in OUTPUT_NAMES if (self.out / n).exists()]
        recs = self.out / STATE_DIR / "stages"
        if not found and not recs.exists():
            return
        prev = self.out / STATE_DIR / "previous"
        if prev.exists():
            shutil.rmtree(prev, ignore_errors=True)
        prev.mkdir(parents=True)
        for n in found:
            shutil.move(str(self.out / n), str(prev / n))
        for n in ("stages", "fragments", "summary.json", "slice", GATE_FILE):
            if (self.out / STATE_DIR / n).exists():
                shutil.move(str(self.out / STATE_DIR / n), str(prev / f".gstudio-{n}"))

    def selected(self, sid: str) -> bool:
        return self.only is None or sid in self.only or sid in ALWAYS

    def module_for(self, d: StageDef) -> str:
        return self.modules.get(d.id, d.module)

    def available(self, modname: str) -> Optional[str]:
        """None if importable as far as we can tell without importing it; else the reason."""
        try:
            spec = importlib.util.find_spec(modname)
        except (ImportError, ValueError) as exc:
            return f"{modname} is not available yet ({exc})"
        if spec is None:
            return f"stage package {modname} is not available yet"
        return None

    def log_path(self, sid: str) -> Path:
        return self.out / "logs" / f"{sid}.log"

    def printed_only(self) -> bool:
        """The normalized spec is a printed-only object (kind "printed", catalog package A): no electronics stages."""
        spec = getattr(self.ctx, "spec", None) if self.ctx is not None else None
        return spec is not None and getattr(spec, "kind", "gadget") == "printed"

    PRINTED_SKIPS = ("pcb", "firmware")  # nothing to route or compile; order, manual and checks still run (soft deps)

    def _printed_electrical(self) -> dict:
        """Printed-only: the always-run electrical stage writes an empty electrical.json (no instances, power total 0, ok)
        so its hard dependents (cad, order, manual) run; gstudio.electrical (owned by B) is never called."""
        from gstudio.printed import REASON, empty_electrical

        doc = empty_electrical(self.ctx.spec)
        write_json_atomic(self.out / "electrical.json", doc)
        self.ctx.electrical = doc
        with contextlib.suppress(Exception):
            self.ctx.artifacts["electrical.json"] = str(self.out / "electrical.json")
        return {"message": REASON, "slots_used": [], "instances": 0, "nets": 0, "power_ok": True, "total_ma_max": 0.0,
                "total_ma_max_capped": 0.0, "warnings": [], "printed_only": True}

    def save_record(self, sid: str) -> None:
        r = self.status.recs[sid]
        if r.skip_kind == "not_selected":
            return  # keep the previous run's record for stages that did not run now
        rec = {"id": sid, "state": r.state, "started": r.started, "ended": r.ended, "seconds": r.seconds,
               "message": r.message, "error": r.error, "details": r.details, "skip_kind": r.skip_kind,
               "summary": r.summary, "log": f"logs/{sid}.log", "engine_run": self.status.started}
        write_json_atomic(self.out / STATE_DIR / "stages" / f"{sid}.json", jsonable(rec))

    def blocked_by(self, d: StageDef) -> Optional[str]:
        """Reason a hard dependency prevents this stage from running, else None."""
        for dep in d.hard:
            r = self.status.recs[dep]
            if r.skip_kind == "not_selected":
                continue  # use what is on disk
            if r.state == "failed":
                return f"needs {dep}, which failed"
            if r.state == "skipped":
                return f"needs {dep}, which was skipped ({r.message})"
        return None

    def waiting_on(self, d: StageDef) -> bool:
        for dep in (*d.hard, *d.soft):
            if self.status.recs[dep].state in ("queued", "running"):
                return True
        return False

    def on_progress(self, sid: str, message: str, percent: Optional[float]) -> None:
        self.status.progress(sid, message, percent)

    # -------------------------------------------------------------------- inline stages
    def run_inline(self, d: StageDef) -> None:
        sid = d.id
        self.status.start(sid)
        t0 = time.monotonic()
        with capture_inline(self.log_path(sid)) as fh:
            print(f"[{now_iso()}] stage {sid} (inline)")
            try:
                if sid == "normalize":
                    summary = self._normalize()
                elif sid == "electrical" and self.printed_only():
                    summary = self._printed_electrical()
                else:
                    modname = self.module_for(d)
                    reason = self.available(modname)
                    if reason:
                        self.status.finish(sid, "skipped", reason, skip_kind="unavailable")
                        self.save_record(sid)
                        return
                    mod = importlib.import_module(modname)
                    entry = "finalize" if sid == "finalize" else "run"
                    fn = getattr(mod, entry, None)
                    if not callable(fn):
                        self.status.finish(sid, "skipped", f"{modname} has no {entry}(ctx) yet", skip_kind="unavailable")
                        self.save_record(sid)
                        return
                    if sid == "finalize":
                        with contextlib.suppress(Exception):
                            self.ctx.artifacts["finalize.module"] = modname  # package re-runs the same finalize (MIN-21)
                    if sid == "finalize" and not (self.out / "BUILD_GUIDE.md").exists():
                        self.status.finish(sid, "skipped", "no BUILD_GUIDE.md to finalize (the manual stage did not write one)",
                                           skip_kind="dependency")
                        self.save_record(sid)
                        return
                    summary = fn(self.ctx)
                    if sid == "electrical":
                        self._after_electrical(summary)
                summary = jsonable(summary if summary is not None else {})
                print(f"[{now_iso()}] stage {sid}: done in {time.monotonic() - t0:.1f} s", file=fh)
                self._finish_ok(sid, summary)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, KeyboardInterrupt):
                    raise
                tb = traceback.format_exc()
                print(tb, file=fh)
                p = _error_payload(exc, tb)
                self.status.finish(sid, "failed", p["error"].splitlines()[0][:300] if p["error"] else "failed",
                                   error=p["error"], details=p.get("details"))
        self.save_record(sid)

    def _finish_ok(self, sid: str, summary: Any) -> None:
        if isinstance(summary, dict) and (summary.get("state") == "skipped" or summary.get("skipped") is True):
            self.status.finish(sid, "skipped", str(summary.get("reason") or summary.get("message") or "skipped by the stage"),
                               summary=summary, skip_kind="unavailable")
        else:
            msg = summary.get("message") if isinstance(summary, dict) else None
            self.status.finish(sid, "done", str(msg or "done"), summary=summary)

    def _normalize(self) -> dict:
        factory = self.options.get("_context")  # tests only: callable(spec, out, options, on_progress) -> ctx
        try:
            self.ctx = (factory or prepare_context)(self.spec, self.out, self.options, self.on_progress)
        except ImportError as exc:
            raise RuntimeError(f"foundation modules missing, cannot normalize the spec: {exc}") from exc
        ctx = self.ctx
        with contextlib.suppress(Exception):
            ctx.say = lambda stage_id, text: self.status.say(stage_id, text)
        sd = spec_to_dict(ctx.spec, True)
        apply_option_defaults(ctx, sd)
        write_json_atomic(self.out / "spec.json", sd)
        try:
            from gstudio.version import ENGINE_VERSION, PROJECT_SCHEMA
        except ImportError:  # pragma: no cover
            ENGINE_VERSION, PROJECT_SCHEMA = "unknown", "gadget-project/1"
        o = self.options
        proj = {"schema": PROJECT_SCHEMA, "name": sd.get("name"), "slug": sd.get("slug"), "version": o.get("version"),
                "project_id": o.get("project_id"), "created": now_iso(), "engine_version": ENGINE_VERSION,
                "brief": o.get("brief"), "brief_version": o.get("brief_version"),
                "concept_id": o.get("concept_id"), "spec_hash": spec_hash(sd)}
        if self.repair:
            proj["auto_repair"] = self.repair
        write_json_atomic(self.out / "project.json", proj)
        rep = getattr(ctx, "normalize_report", None)
        d = getattr(ctx.spec, "derived", None)
        n_inst = len(getattr(d, "instances", []) or []) if d is not None else 0
        return {"message": f"spec valid; {n_inst} module instances", "spec_hash": proj["spec_hash"],
                "report": jsonable(rep) if rep is not None else None}

    def _after_electrical(self, summary: Any) -> None:
        ctx = self.ctx
        if getattr(ctx, "electrical", None) is None:
            if isinstance(summary, dict) and str(summary.get("schema", "")).startswith("gadget-electrical/"):
                ctx.electrical = summary
            elif (self.out / "electrical.json").exists():
                ctx.electrical = json.loads((self.out / "electrical.json").read_text())
        if getattr(ctx, "electrical", None) is not None and not (self.out / "electrical.json").exists():
            write_json_atomic(self.out / "electrical.json", ctx.electrical)

    # -------------------------------------------------------------------- process stages
    def launch(self, d: StageDef, q: Any, running: dict) -> None:
        sid = d.id
        if sid in self.PRINTED_SKIPS and self.printed_only():
            from gstudio.printed import REASON

            self.status.start(sid)
            self._finish_ok(sid, {"skipped": True, "reason": REASON})
            self.save_record(sid)
            return
        modname = self.module_for(d)
        reason = self.available(modname)
        if reason:
            self.status.finish(sid, "skipped", reason, skip_kind="unavailable")
            self.save_record(sid)
            return
        self.status.start(sid)
        if getattr(self.ctx, "electrical", None) is None and (self.out / "electrical.json").exists():
            with contextlib.suppress(Exception):
                self.ctx.electrical = json.loads((self.out / "electrical.json").read_text())
        mpctx = mp.get_context(self.options.get("_mp_start") or ("fork" if sys.platform.startswith("linux") else "spawn"))
        proc = mpctx.Process(target=_child_main, args=(sid, modname, self.ctx, q, str(self.log_path(sid))),
                             name=f"gstudio-{sid}", daemon=False)
        proc.start()
        running[sid] = _Running(proc, time.monotonic(), float(self.timeouts.get(sid, d.timeout)))

    def handle_result(self, sid: str, payload: dict) -> None:
        if payload.get("artifacts"):
            with contextlib.suppress(Exception):
                self.ctx.artifacts.update(payload["artifacts"])
        if sid == "cad" and payload.get("ok") and self.cad_gate():
            return  # a repair round relaunched the CAD stage; it finishes when that run reports
        if payload.get("ok"):
            for w in payload.get("warnings") or []:
                with contextlib.suppress(Exception):
                    if w not in self.ctx.warnings:
                        self.ctx.warnings.append(w)
            self._finish_ok(sid, payload.get("summary"))
        elif payload.get("missing_run"):
            self.status.finish(sid, "skipped", payload["error"], skip_kind="unavailable")
        else:
            err = payload.get("error") or "failed"
            self.status.finish(sid, "failed", err.splitlines()[0][:300], error=err, details=payload.get("details"))
        self.save_record(sid)

    def watch_drafts(self) -> None:
        """Publish cad/draft.glb in status.json once the file has stopped changing (two equal stats >= 0.4 s apart)."""
        now = time.monotonic()
        if now - self._draft_check < 0.4:
            return
        self._draft_check = now
        for rel in DRAFT_FILES:
            try:
                st = (self.out / rel).stat()
            except OSError:
                continue
            key = (st.st_size, st.st_mtime_ns)
            if st.st_size == 0 or key == self._draft_published.get(rel):
                continue
            if self._draft_seen.get(rel) == key:
                self._draft_published[rel] = key
                self.status.set_draft(rel)
            else:
                self._draft_seen[rel] = key

    # -------------------------------------------------------------------- MAJ-6 gate
    def cad_gate(self) -> bool:
        """After a successful CAD run: read its collision check. On failure try ONE repair round (move the clashing
        feature, re-normalize, re-run electrical and CAD); True when the CAD stage was relaunched. Otherwise record the
        verdict in .gstudio/gate.json, status.json and the summary."""
        g = read_collisions(self.out)
        if g["buildable"] is not False:
            self.set_verdict(g)
            return False
        plan = None
        why_not = None
        if self.repair is None and self.options.get("auto_repair", True) and self._running is not None and self._q is not None:
            try:
                comps = json.loads((self.out / "cad" / "components.json").read_text())
            except (OSError, ValueError):
                comps = {}
            sd = spec_to_dict(self.ctx.spec, True)
            plan = repair_plan(sd, g["pairs"], comps)
            if plan is None:
                why_not = "no colliding pair is between two features, so no feature can be moved automatically"
            else:
                try:
                    new_ctx = (self.options.get("_context") or prepare_context)(apply_repair(sd, plan), self.out, self.options, self.on_progress)
                except Exception as exc:  # noqa: BLE001 - an invalid repaired spec means no repair
                    why_not = f"moving {plan['feature']} to {plan['to_at']} is not a valid design ({str(exc)[:200]})"
                    plan = None
                else:
                    self.repair = dict(plan, round=1, before=g["pairs"])
                    with contextlib.suppress(Exception):  # keep what PCB / firmware already published
                        new_ctx.artifacts.update({k: v for k, v in (self.ctx.artifacts or {}).items() if not str(k).startswith("cad.")})
                        for w in getattr(self.ctx, "warnings", []) or []:
                            if w not in new_ctx.warnings:
                                new_ctx.warnings.append(w)
                    self.ctx = new_ctx
                    with contextlib.suppress(Exception):
                        self.ctx.say = lambda stage_id, text: self.status.say(stage_id, text)
                    self.status.say("cad", f"Repair round: {plan['why']}; rebuilding the CAD.")
                    nsd = spec_to_dict(self.ctx.spec, True)
                    apply_option_defaults(self.ctx, nsd)
                    write_json_atomic(self.out / "spec.json", nsd)
                    with contextlib.suppress(Exception):
                        pj = json.loads((self.out / "project.json").read_text())
                        pj.update({"spec_hash": spec_hash(nsd), "auto_repair": self.repair})
                        write_json_atomic(self.out / "project.json", pj)
                    el = next(d for d in self.defs if d.id == "electrical")
                    self.run_inline(el)
                    if self.status.recs["electrical"].state == "done":
                        self.launch(next(d for d in self.defs if d.id == "cad"), self._q, self._running)
                        return True
                    why_not = "electrical failed on the repaired spec"
        elif self.repair is not None:
            self.repair["after"] = g["pairs"]
        elif not self.options.get("auto_repair", True):
            why_not = "automatic repair is switched off (auto_repair=false)"
        self.set_verdict(g, why_not)
        return False

    def set_verdict(self, g: dict, why_not: Optional[str] = None) -> None:
        if g["buildable"] is False:
            msg = (f"Not buildable: the CAD shows {len(g['pairs'])} colliding pair(s): {pairs_text(g['pairs'])}."
                   + (f" One automatic repair ({self.repair['why']}) did not clear them." if self.repair else "")
                   + (f" No automatic repair: {why_not}." if why_not else "")
                   + " The quote is held until the design is revised.")
            v = {"buildable": False, "verdict": "not_buildable", "verdict_message": msg, "pairs": g["pairs"]}
        else:
            v = {"buildable": True if g["buildable"] else None, "verdict": "buildable" if g["buildable"] else "unknown",
                 "verdict_message": (("The CAD collision check passes" + (f" after one automatic repair: {self.repair['why']}" if self.repair else ""))
                                     if g["buildable"] else f"Collisions not checked: {g.get('detail')}"),
                 "pairs": []}
        if self.repair:
            v["repair"] = self.repair
        self.verdict = v
        self.status.verdict = v
        write_json_atomic(self.out / STATE_DIR / GATE_FILE, dict(v, engine_run=self.status.started))
        if v["buildable"] is False:
            self.status.say("cad", msg)

    @staticmethod
    def kill(proc: Any) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, sig)
            with contextlib.suppress(Exception):
                proc.join(5 if sig == signal.SIGTERM else 2)
            if not proc.is_alive():
                return

    # -------------------------------------------------------------------- main loop
    def run(self) -> dict:
        t_start = time.monotonic()
        for d in self.defs:
            if not self.selected(d.id):
                self.status.recs[d.id].state = "skipped"
                self.status.recs[d.id].message = "not selected (--only)"
                self.status.recs[d.id].skip_kind = "not_selected"
        self.status.state = "running"
        self.status.write()
        os.environ["GSTUDIO_RUN"] = self.status.started  # stages tell this run's records from an earlier run's
        pending = [d for d in self.defs if self.status.recs[d.id].state == "queued"]
        running: dict[str, _Running] = {}
        mpctx = mp.get_context(self.options.get("_mp_start") or ("fork" if sys.platform.startswith("linux") else "spawn"))
        q = mpctx.Queue()
        self._q, self._running = q, running
        results: dict[str, dict] = {}
        import threading

        old_term = None
        if threading.current_thread() is threading.main_thread():
            old_term = signal.signal(signal.SIGTERM, _on_sigterm)
        try:
            while pending or running:
                progressed = False
                for d in list(pending):
                    if self.waiting_on(d):
                        continue
                    why = self.blocked_by(d)
                    if why:
                        pending.remove(d)
                        self.status.finish(d.id, "skipped", why, skip_kind="dependency")
                        self.save_record(d.id)
                        progressed = True
                        continue
                    if d.mode == "inline":
                        pending.remove(d)
                        self.run_inline(d)
                        progressed = True
                        continue
                    if len(running) < self.max_parallel:
                        pending.remove(d)
                        self.launch(d, q, running)
                        progressed = True
                # drain messages
                try:
                    msg = q.get(timeout=0.05 if progressed else 0.25)
                    while True:
                        kind, sid = msg[0], msg[1]
                        if kind == "progress":
                            self.on_progress(sid, msg[2], msg[3])
                        elif kind == "say":
                            self.status.say(sid, msg[2])
                        elif kind == "result":
                            results[sid] = msg[2]
                        msg = q.get_nowait()
                except _queue.Empty:
                    pass
                self.watch_drafts()
                # reap finished / dead / timed-out children
                for sid, rr in list(running.items()):
                    if sid in results:
                        rr.proc.join(10)
                        if rr.proc.is_alive():
                            self.kill(rr.proc)
                        del running[sid]
                        self.handle_result(sid, results.pop(sid))
                        continue
                    if not rr.proc.is_alive():
                        rr.dead_at = rr.dead_at or time.monotonic()
                        if time.monotonic() - rr.dead_at > 2.0:  # grace for the result still in the pipe
                            del running[sid]
                            code = rr.proc.exitcode
                            how = f"signal {-code}" if code is not None and code < 0 else f"exit code {code}"
                            self.handle_result(sid, {"ok": False, "error": f"stage process died ({how}) without a result; see logs/{sid}.log"})
                        continue
                    if time.monotonic() - rr.t0 > rr.timeout:
                        self.kill(rr.proc)
                        del running[sid]
                        self.handle_result(sid, {"ok": False, "error": f"timed out after {rr.timeout:.0f} s; see logs/{sid}.log"})
        except (KeyboardInterrupt, Terminated) as exc:
            for sid, rr in running.items():
                self.kill(rr.proc)
                self.status.finish(sid, "failed", "stopped: the build was terminated", error="terminated")
            self.status.state = "error"
            self.status.error = "terminated (SIGTERM)" if isinstance(exc, Terminated) else "interrupted"
            self.status.write()
            raise
        finally:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
            with contextlib.suppress(Exception):
                q.close()

        failed = [r for r in self.status.recs.values() if r.state == "failed"]
        if failed:
            self.status.state = "error"
            self.status.error = f"{len(failed)} stage(s) failed: " + "; ".join(f"{r.id}: {(r.error or '').splitlines()[0][:160]}" for r in failed)
        else:
            self.status.state = "done"
        summary = self.summary(time.monotonic() - t_start)
        write_json_atomic(self.out / STATE_DIR / "summary.json", summary)
        self.status.write()
        return summary

    def story_audio(self) -> Optional[dict]:
        opts = getattr(self.ctx, "options", None) if self.ctx is not None else None
        rec = stage_records(self.out).get("firmware") or {}
        return _story_audio(self.out, opts if isinstance(opts, dict) else dict(self.options), rec)

    def summary(self, seconds: float) -> dict:
        try:
            from gstudio.version import ENGINE_VERSION
        except ImportError:  # pragma: no cover
            ENGINE_VERSION = "unknown"
        return {"schema": "gadget-build-summary/1", "state": self.status.state, "error": self.status.error,
                "out": str(self.out), "engine_version": ENGINE_VERSION, "seconds": round(seconds, 2),
                "only": self.only, "fast": bool(self.options.get("fast")),
                "buildable": self.verdict.get("buildable"), "verdict": self.verdict.get("verdict"),
                "verdict_message": self.verdict.get("verdict_message"), "auto_repair": self.repair,
                "story_audio": self.story_audio(),
                "stages": {sid: {"label": r.label, "state": r.state, "message": r.message, "seconds": r.seconds,
                                 "error": r.error, "details": r.details, "summary": r.summary, "log": f"logs/{sid}.log"}
                           for sid, r in self.status.recs.items()},
                "warnings": jsonable(getattr(self.ctx, "warnings", []) or []) if self.ctx is not None else []}


def _story_audio(out: Path, options: dict, rec: dict) -> Optional[dict]:
    s = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
    sp = s.get("story_pack") if isinstance(s.get("story_pack"), dict) else None
    if sp is None and options.get("stories_audio") is None:
        return None
    tm = (s.get("timings") or {}).get("story_pack_s")
    warn = next((w for w in s.get("warnings") or [] if "TTS" in str(w) or "story audio" in str(w).lower()), None)
    return {"requested": options.get("stories_audio"), "basis": options.get("stories_audio_basis"),
            "audio": (sp or {}).get("audio"), "files": (sp or {}).get("file_count"), "missing": len((sp or {}).get("missing") or []),
            "seconds": tm, "seconds_basis": "firmware stage timings.story_pack_s (TTS synthesis and card writing)",
            "warning": warn}


def build(spec: Any, out_dir: str | Path, options: Optional[dict] = None,
          progress_cb: Optional[Callable[[dict], None]] = None) -> dict:
    """Run the whole pipeline (or `options['only']`) for one spec into out_dir. Never raises for stage failures."""
    return Pipeline(spec, Path(out_dir), dict(options or {}), progress_cb).run()


def _normalized(spec: Any):
    """(Spec, Library, report) for a spec input; raises SpecError."""
    from gstudio.library import Library
    from gstudio.normalize import normalize
    from gstudio.spec import Spec, load_spec

    lib = Library()
    data = load_spec_input(spec)
    sp = data if isinstance(data, Spec) else load_spec(data)
    sp, report = normalize(sp, lib, raise_on_error=True)
    return sp, lib, report


def validate(spec: Any) -> dict:
    """{ok, errors: [str], error_items: [{path,msg,fix}], normalized, warnings} for the agent's repair loop."""
    from gstudio.errors import SpecError

    try:
        sp, _lib, report = _normalized(spec)
    except SpecError as exc:
        return {"ok": False, "errors": exc.messages(), "error_items": exc.errors, "normalized": None, "warnings": []}
    except (ValueError, TypeError) as exc:  # unreadable JSON etc.
        return {"ok": False, "errors": [f"spec: {exc}"], "error_items": [{"path": "", "msg": str(exc)}],
                "normalized": None, "warnings": []}
    d = getattr(sp, "derived", None)
    warnings = list(getattr(d, "warnings", []) or []) if d is not None else []
    if isinstance(report, dict):
        warnings += [w for w in report.get("warnings", []) if w not in warnings]
    return {"ok": True, "errors": [], "error_items": [], "normalized": spec_to_dict(sp, True), "warnings": jsonable(warnings)}


def preview(spec: Any, out: str | Path) -> dict:
    """Shell-only preview render: {ok, png, glb, warnings, errors}. Raises nothing for spec errors."""
    from gstudio.errors import SpecError

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        sp, lib, _ = _normalized(spec)
    except SpecError as exc:
        return {"ok": False, "png": None, "glb": None, "warnings": [], "errors": exc.messages()}
    if importlib.util.find_spec("gstudio.cad") is None:
        return {"ok": False, "png": None, "glb": None, "warnings": [], "errors": ["preview: gstudio.cad is not available yet"]}
    cad = importlib.import_module("gstudio.cad")
    fn = getattr(cad, "preview", None)
    if not callable(fn):
        return {"ok": False, "png": None, "glb": None, "warnings": [], "errors": ["preview: gstudio.cad.preview is not available yet"]}
    r = fn(sp, lib, out) or {}
    png, glb = r.get("png"), r.get("glb")
    ok = bool(png and Path(png).exists())
    return {"ok": ok, "png": str(png) if png else None, "glb": str(glb) if glb else None,
            "warnings": jsonable(r.get("warnings", [])), "errors": jsonable(r.get("errors", [])) if ok else
            (jsonable(r.get("errors")) or ["preview produced no PNG"])}


def stage_records(out: str | Path) -> dict:
    """Saved records of every stage that ran into `out` (this run or an earlier one)."""
    d = Path(out) / STATE_DIR / "stages"
    recs = {}
    for sid in STAGE_IDS:
        p = d / f"{sid}.json"
        if p.exists():
            with contextlib.suppress(Exception):
                recs[sid] = json.loads(p.read_text())
    return recs


__all__ = ["build", "Pipeline", "STAGES", "STAGE_IDS", "parse_only", "stage_records", "spec_hash", "write_json_atomic",
           "now_iso", "jsonable"]
