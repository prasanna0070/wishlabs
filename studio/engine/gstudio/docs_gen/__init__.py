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
    for name, fn in (("PROJECT.md", lambda: project.render(b)), ("PARTS_EVIDENCE.md", lambda: parts.render(b, ctx.lib)),
                     ("SOFTWARE_SETUP.md", lambda: software.render(b))):
        ctx.progress(STAGE, f"writing {name}", None)
        text = fn()
        (b.out / name).write_text(text, encoding="utf-8")
        files[name] = len(text.encode())
    return {"message": ", ".join(f"{k} {v:,} B" for k, v in files.items()), "files": files}
