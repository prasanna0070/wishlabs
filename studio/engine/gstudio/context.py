"""BuildContext: the object every stage's `run(ctx)` receives (ARCHITECTURE §2). Owned by foundation.

Fields
  spec        normalized gadget-spec/1 model (gstudio.spec.Spec)
  lib         gstudio.library.Library
  out         version output root (Path)
  electrical  dict filled by gstudio.electrical.run (gadget-electrical/1), else None
  artifacts   paths/values stages publish for later stages, e.g. ctx.artifacts["cad.components"]
  options     e.g. {"stories_audio": False, "fast": False}
  on_progress optional callback(stage_id, message, percent) set by the pipeline
  warnings    list of {"stage", "msg"} collected by ctx.warn()

Methods
  ctx.progress(stage_id, message, percent=None)   report progress (never raises)
  ctx.say(stage_id, text)                         one chat line for the UI via on_say (never raises)
  ctx.path(rel) -> Path                           out/rel with parent dirs created
  ctx.warn(stage_id, msg)                         record a non-fatal warning
  BuildContext.prepare(spec, out, lib=None, options=None)  load + normalize a spec into a context
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # avoid importing pydantic models at import time of this module
    from .library import Library
    from .spec import Spec

log = logging.getLogger("gstudio")

ProgressFn = Callable[[str, str, Optional[float]], None]


@dataclass
class BuildContext:
    spec: "Spec"
    lib: "Library"
    out: Path
    electrical: Optional[dict] = None
    artifacts: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    on_progress: Optional[ProgressFn] = None
    warnings: list = field(default_factory=list)
    on_say: Optional[Callable[[str, str], None]] = None
    normalize_report: Optional[dict] = None

    def __post_init__(self) -> None:
        self.out = Path(self.out)
        self.out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ progress
    def progress(self, stage_id: str, message: str, percent: Optional[float] = None) -> None:
        """Report stage progress. Never raises: a broken callback must not kill a build."""
        if percent is not None:
            try:
                percent = max(0.0, min(100.0, float(percent)))
            except (TypeError, ValueError):
                percent = None
        if self.on_progress is not None:
            try:
                self.on_progress(stage_id, message, percent)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("progress callback failed: %s", exc)
        else:
            pct = "" if percent is None else f" {percent:5.1f}%"
            print(f"[{time.strftime('%H:%M:%S')}] {stage_id}{pct} {message}", file=sys.stderr, flush=True)

    def say(self, stage_id: str, text: str) -> None:
        """A short human chat line for the UI (e.g. "Routed the backplane: 0 DRC errors"). Never raises."""
        if self.on_say is not None:
            try:
                self.on_say(stage_id, text)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("say callback failed: %s", exc)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] {stage_id}: {text}", file=sys.stderr, flush=True)

    def warn(self, stage_id: str, msg: str) -> None:
        self.warnings.append({"stage": stage_id, "msg": msg})
        log.warning("%s: %s", stage_id, msg)

    # ------------------------------------------------------------------ paths
    def path(self, rel: str | Path) -> Path:
        """Return out/rel, creating parent directories. `rel` must stay inside out/."""
        p = (self.out / rel).resolve()
        root = self.out.resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"path escapes the output root: {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    # ------------------------------------------------------------------ construction
    @classmethod
    def prepare(
        cls,
        spec: Any,
        out: str | Path,
        lib: "Library | None" = None,
        options: Optional[dict] = None,
        on_progress: Optional[ProgressFn] = None,
        normalize: bool = True,
    ) -> "BuildContext":
        """Load (dict | path | Spec), normalize, and return a ready context. Raises SpecError."""
        from .library import Library
        from .spec import Spec, load_spec

        lib = lib or Library()
        sp = spec if isinstance(spec, Spec) else load_spec(spec)
        report = None
        if normalize:
            from .normalize import normalize as _normalize

            sp, report = _normalize(sp, lib, raise_on_error=True)
        ctx = cls(spec=sp, lib=lib, out=Path(out), options=dict(options or {}), on_progress=on_progress)
        ctx.normalize_report = report
        return ctx


__all__ = ["BuildContext", "ProgressFn"]
