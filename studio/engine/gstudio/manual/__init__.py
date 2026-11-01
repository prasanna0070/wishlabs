"""Build manual stage (ARCHITECTURE §2, §5, §9).

`run(ctx)` writes build.json `steps`/`wiring`/`tests`, manual/*.png (+ wiring.svg), BUILD_GUIDE.md and
manual/manual-summary.json, and returns the summary. Importing this package is cheap: VTK, OCP and
scipy load only inside run().
"""

from __future__ import annotations

from typing import Any


def run(ctx: Any) -> dict:
    from .stage import run as _run

    return _run(ctx)


__all__ = ["run"]
