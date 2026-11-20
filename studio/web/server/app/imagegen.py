"""Concept photos: Nano Banana Pro (gemini-3-pro-image, global) turns the CAD preview render into a product
photograph of exactly that object. The render is the reference image. responseModalities must be TEXT+IMAGE:
image-only requests hang, so every call also has a hard timeout."""
from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .genai_client import get_client

log = logging.getLogger("gstudio.imagegen")

PROMPT = (
    "Studio product photograph of exactly this object as a finished matte 3D-printed PETG product, "
    "body colour {body} ({body_hex}), details in {detail_hex}. Soft daylight from the upper left, seamless warm "
    "off-white backdrop, gentle contact shadow on a light wooden bedside table, 50 mm lens, front three-quarter "
    "view matching the reference camera. Keep the exact silhouette, proportions, face, holes, buttons and speaker "
    "grille of the reference render; do not add or remove features, do not add text, logos, cables, people, "
    "hands or props. Fine visible print layer lines are fine. This is {title}: {blurb}"
)


# The user's own product photo or sketch (image brief kind product_photo or sketch) may set the photo's mood. It never
# changes the object: the render stays the only source of shape.
STYLE_NOTE = ("[STYLE REFERENCE: the user's own {what}. Borrow only its colour mood, materials and finish where they "
              "suit a matte 3D-printed PETG product. Do NOT copy its shape, parts, text, logos, people or background: "
              "the object is exactly the reference render above.]")


class ImageGen:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def photo_from_render(self, render_png: bytes, spec: dict, title: str, blurb: str,
                                on_usage=None, style_ref: tuple[bytes, str] | None = None) -> tuple[bytes | None, str]:
        """Returns (image bytes or None, note). Never raises. style_ref: (JPEG, kind) of the user's own product photo
        or sketch, used for colour mood and finish only (app/agent/image_rules.style_reference)."""
        if not self.settings.photos:
            return None, "Photos are switched off; showing the CAD render."
        colors = spec.get("colors") or {}
        prompt = PROMPT.format(body=colors.get("body_name") or "the body colour", body_hex=colors.get("body", ""),
                               detail_hex=colors.get("detail", "dark grey"), title=title, blurb=blurb)
        try:
            return await asyncio.wait_for(self._call(render_png, prompt, on_usage, style_ref),
                                          self.settings.image_timeout_s)
        except asyncio.TimeoutError:
            log.warning("image model timed out after %ss", self.settings.image_timeout_s)
            return None, "Photo generation timed out; showing the CAD render."
        except Exception as exc:  # noqa: BLE001
            log.warning("image model failed: %s", exc)
            return None, "Photo generation failed; showing the CAD render."

    async def _call(self, render_png: bytes, prompt: str, on_usage=None,
                    style_ref: tuple[bytes, str] | None = None) -> tuple[bytes | None, str]:
        import time

        from google.genai import types

        from .usage import record_from_response

        client = get_client(self.settings)
        cfg = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            temperature=1.0,
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size="1K"),
            http_options=types.HttpOptions(timeout=int(self.settings.image_timeout_s * 1000)),
        )
        style = [types.Part.from_text(text=STYLE_NOTE.format(what=style_ref[1].replace("_", " "))),
                 types.Part.from_bytes(data=style_ref[0], mime_type="image/jpeg")] if style_ref else []
        contents = [types.Content(role="user", parts=[
            types.Part.from_text(text="[REFERENCE RENDER: the exact object to photograph]"),
            types.Part.from_bytes(data=render_png, mime_type="image/png"),
            *style,
            types.Part.from_text(text=prompt),
        ])]
        t0 = time.time()
        resp = await client.aio.models.generate_content(model=self.settings.image_model, contents=contents,
                                                        config=cfg)
        # Nano Banana Pro may also return draft "thought" images; only the final (non-thought) image counts.
        finals = [pt.inline_data.data for c in resp.candidates or []
                  for pt in ((c.content.parts if c.content else None) or [])
                  if pt.inline_data and pt.inline_data.data and not pt.thought]
        if on_usage:
            on_usage(record_from_response(self.settings.image_model, resp, "image", time.time() - t0, len(finals)))
        if finals:
            return finals[-1], ""
        finish = (resp.candidates[0].finish_reason if resp.candidates else None)
        return None, f"The image model returned no image ({finish}); showing the CAD render."
