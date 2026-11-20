"""Concept rendering: builder preview (CAD render PNG + shell GLB), then a photo of exactly that render."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .util import new_id

log = logging.getLogger("gstudio.concepts")

Progress = Callable[[dict], Awaitable[None]]


def _img_ext(b: bytes) -> str:
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return "png"


def concept_base(pid: str, cid: str) -> str:
    return f"/api/projects/{pid}/concepts/{cid}"


def plain_photo(svc, spec: dict) -> bool:
    """Pack `freeform`: a concept with no face decor (eyes, mouth, blush) is an appliance or plain product, so its
    photo is plain product photography with no added face. Always False with the pack off."""
    fn = getattr(getattr(svc, "bridge", None), "packs_on", None)
    try:
        on = fn() if callable(fn) else ()
    except Exception:  # noqa: BLE001
        on = ()
    if "freeform" not in set(on or ()):
        return False
    return not any(isinstance(d, dict) and d.get("type") in ("eye", "mouth", "blush") for d in spec.get("decor") or [])


async def render_concept(svc, pid: str, index: int, title: str, blurb: str, spec: dict, warnings: list[str],
                         progress: Progress, tier: str | None = None, on_usage=None, style_ref=None,
                         keep_errors: bool = False) -> dict:
    """keep_errors (pack `freeform` repair loop): a failed render also carries its raw errors in c["render_errors"]
    (the turn reads and removes them before the concept is stored)."""
    cid = new_id("c")
    key = f"projects/{pid}/concepts/{cid}"
    base = concept_base(pid, cid)
    c = {"id": cid, "title": title, "tier": tier, "blurb": blurb, "spec": spec, "render_url": None, "photo_url": None,
         "glb_url": None, "state": "rendering", "warnings": list(warnings), "photo_note": None}
    await asyncio.to_thread(svc.store.write_json, f"{key}/spec.json", spec)
    await progress({"concept_id": cid, "index": index, "title": title, "tier": tier, "state": "rendering"})
    try:
        prev = await svc.builder.preview(spec)
    except Exception as exc:  # noqa: BLE001
        log.warning("preview failed for %s: %s", title, exc)
        prev = {"ok": False, "errors": [str(exc)[:300]], "png": None, "glb": None, "warnings": []}
    c["warnings"] += prev.get("warnings") or []
    png = prev.get("png")
    if not prev.get("ok") or not png:
        c["state"] = "failed"
        c["photo_note"] = "The shape could not be rendered: " + "; ".join(prev.get("errors") or ["unknown error"])[:300]
        if keep_errors:
            c["render_errors"] = [str(e) for e in prev.get("errors") or ["unknown error"]]
        await progress({"concept_id": cid, "index": index, "title": title, "state": "failed",
                        "note": c["photo_note"]})
        return c
    await asyncio.to_thread(svc.store.write_bytes, f"{key}/render.png", png, "image/png")
    c["render_url"] = f"{base}/render.png"
    if prev.get("glb"):
        await asyncio.to_thread(svc.store.write_bytes, f"{key}/preview.glb", prev["glb"], "model/gltf-binary")
        c["glb_url"] = f"{base}/preview.glb"
    await progress({"concept_id": cid, "index": index, "title": title, "state": "photo"})
    photo, note = (None, "Photos are off in this mode; showing the CAD render.")
    if svc.imagegen is not None:
        kw = {"style_ref": style_ref} if style_ref else {}      # the user's product photo or sketch (image input)
        if plain_photo(svc, spec):                              # pack freeform: no face is added to a plain product
            kw["plain"] = True
        photo, note = await svc.imagegen.photo_from_render(png, spec, title, blurb, on_usage=on_usage, **kw)
    if photo:
        ext = _img_ext(photo)
        await asyncio.to_thread(svc.store.write_bytes, f"{key}/photo.{ext}", photo, f"image/{'jpeg' if ext == 'jpg' else ext}")
        c["photo_url"] = f"{base}/photo.{ext}"
    else:
        c["photo_note"] = note
    c["state"] = "ready"
    await progress({"concept_id": cid, "index": index, "title": title, "state": "ready", "note": c["photo_note"]})
    return c


async def render_concepts(svc, pid: str, items: list[dict], progress: Progress, on_usage=None,
                          style_ref=None) -> list[dict]:
    """items: [{title, blurb, tier, spec, warnings}] -> Concept dicts (in order), all rendered in parallel."""
    tasks = [render_concept(svc, pid, i, it["title"], it.get("blurb", ""), it["spec"], it.get("warnings") or [],
                            progress, tier=it.get("tier"), on_usage=on_usage, style_ref=style_ref)
             for i, it in enumerate(items)]
    return list(await asyncio.gather(*tasks))
