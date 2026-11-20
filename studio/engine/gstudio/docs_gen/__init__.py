"""Docs stage: PROJECT.md, PARTS_EVIDENCE.md and SOFTWARE_SETUP.md, assembled from files and stage records only.

    run(ctx) -> summary

BUILD_GUIDE.md belongs to the manual stage. The firmware stage may put design-specific setup Markdown in its
summary under `software_setup_md`; it is included verbatim in SOFTWARE_SETUP.md.
"""
from __future__ import annotations

from pathlib import Path

from . import parts, project, software
from .util import Bundle

STAGE = "docs"


def bundle(ctx) -> Bundle:
    from gstudio.pipeline import stage_records

    out = Path(ctx.out)
    spec = ctx.spec.to_json_dict(True) if hasattr(ctx.spec, "to_json_dict") else dict(ctx.spec)
    return Bundle(out, spec, stage_records(out))


def run(ctx) -> dict:
    b = bundle(ctx)
    files = {}
    # MAJ-21: PROJECT.md links the other documents, so it is written last (after the files it names exist)
    docs = [("PARTS_EVIDENCE.md", lambda: parts.render(b, ctx.lib)), ("SOFTWARE_SETUP.md", lambda: software.render(b)),
            ("PROJECT.md", lambda: project.render(b))]
    if b.spec.get("kind") == "printed":  # catalog package A: no firmware, so no software setup document
        docs = [d for d in docs if d[0] != "SOFTWARE_SETUP.md"]
        (b.out / "SOFTWARE_SETUP.md").unlink(missing_ok=True)
    for name, fn in docs:
        ctx.progress(STAGE, f"writing {name}", None)
        text = fn()
        (b.out / name).write_text(text, encoding="utf-8")
        files[name] = len(text.encode())
    return {"message": ", ".join(f"{k} {v:,} B" for k, v in files.items()), "files": files}
