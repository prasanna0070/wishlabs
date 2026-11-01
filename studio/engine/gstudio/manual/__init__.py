"""Build manual stage (ARCHITECTURE §2, §5, §9).

`run(ctx)` writes build.json `steps`/`wiring`/`tests`, manual/*.png (+ wiring.svg), BUILD_GUIDE.md,
manual/manual-summary.json and the regenerable figure bundle (manual/figures.json + manual/render_manual.py), and
returns the summary. `finalize(ctx)` runs after the checks stage and re-renders the guide's do-not-print banner,
known-issue lines, stop rules, final checks and manual-summary from checks/digital-checks.json (BLK-7, MAJ-19).
Importing this package is cheap: VTK, OCP and scipy load only inside run().
"""

from __future__ import annotations

from typing import Any


def run(ctx: Any) -> dict:
    from .stage import run as _run

    return _run(ctx)


def finalize(ctx: Any) -> dict:
    """finalize(ctx) -> dict: after gstudio.checks.run, before gstudio.package.run."""
    from .stage import finalize as _finalize

    return _finalize(ctx)


__all__ = ["run", "finalize"]
