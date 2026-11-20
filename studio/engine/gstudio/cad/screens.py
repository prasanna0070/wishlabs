"""What a display shows in renders and in the GLB (pack `freeform`, contract section 8 request 3).

Every render used to draw the firmware's idle FACE on each display (render._decals, export.idle_face_png), so an
appliance concept looked like a character even when its firmware app never draws a face. With pack `freeform` on, a
screen shows what the design's firmware app shows: the idle face only for the apps that draw one (desk_companion and
story_player call gsdisp::draw_face); for the others (sensor_display, notifier_clock, ...) a plain readout: dark glass
with a large value bar and two short status lines (illustrative, like the idle face). Pack off: nothing changes.

    from gstudio.cad import screens as SC
    screens = SC.mark(screens, params_or_spec)   # copies with "face": False when the app draws no face
    SC.readout_png(kind, px)                     # the GLB / manual screen texture for such a screen
    SC.readout_shapes(w, h)                      # [(shapely polygon, rgb, dz)] for render._decals
"""
from __future__ import annotations

from typing import Any, Iterable

FACE_APPS = ("desk_companion", "story_player")   # firmware apps that draw the idle face (gsdisp::draw_face)
READOUT_COLOR = (0.62, 0.86, 1.0)


def _template(design: Any) -> str:
    """The behaviour template of a CAD params dict ("template"), a spec dict or a spec model; "" when none."""
    if isinstance(design, dict):
        if "template" in design:
            return str(design.get("template") or "")
        return str((design.get("behaviour") or {}).get("template") or "")
    beh = getattr(design, "behaviour", None)
    return str(getattr(beh, "template", "") or "")


def face_free(design: Any) -> bool:
    """True when pack `freeform` is on and the design's firmware app draws no face on its display."""
    try:
        from .. import packs

        if not packs.enabled("freeform"):
            return False
    except Exception:  # noqa: BLE001 - no pack switch: the live behaviour
        return False
    return _template(design) not in FACE_APPS


def mark(screens: Iterable[dict] | None, design: Any) -> list:
    """The screens list, each a copy with "face": False when face_free(design); the same dicts otherwise."""
    screens = list(screens or [])
    if not screens or not face_free(design):
        return screens
    return [dict(sc, face=False, frame="plain readout (pack freeform: this firmware app draws no face); illustrative")
            for sc in screens]


def readout_png(kind: str = "oled", px=(128, 64), scale: int = 4) -> bytes:
    """A plain readout on black: one large value bar and two short status lines (no eyes, no mouth)."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    w, h = px
    col = tuple(int(255 * c) for c in READOUT_COLOR)
    img = Image.new("RGB", (w * scale, h * scale), (0, 0, 0))
    d = ImageDraw.Draw(img)
    s = scale

    def rect(x0, y0, x1, y1):
        r = max(1, int(min(x1 - x0, y1 - y0) * s / 4))
        d.rounded_rectangle([int(x0 * s), int(y0 * s), int(x1 * s) - 1, int(y1 * s) - 1], radius=r, fill=col)

    rect(0.14 * w, 0.22 * h, 0.70 * w, 0.46 * h)          # the main value
    rect(0.76 * w, 0.22 * h, 0.86 * w, 0.32 * h)          # its unit
    rect(0.14 * w, 0.60 * h, 0.52 * w, 0.68 * h)          # status line 1
    rect(0.14 * w, 0.74 * h, 0.40 * w, 0.82 * h)          # status line 2
    b = BytesIO()
    img.save(b, format="PNG", optimize=True)
    return b.getvalue()


def readout_shapes(w: float, h: float) -> list:
    """Shapes for render._decals in screen mm (centre origin, +v up): the same layout as readout_png."""
    from shapely.geometry import box

    def r(x0, y0, x1, y1):            # fractions of the screen, y measured from the top like the PNG
        return box((x0 - 0.5) * w, (0.5 - y1) * h, (x1 - 0.5) * w, (0.5 - y0) * h)

    return [(r(0.14, 0.22, 0.70, 0.46), READOUT_COLOR, 0.02), (r(0.76, 0.22, 0.86, 0.32), READOUT_COLOR, 0.02),
            (r(0.14, 0.60, 0.52, 0.68), READOUT_COLOR, 0.02), (r(0.14, 0.74, 0.40, 0.82), READOUT_COLOR, 0.02)]


__all__ = ["face_free", "mark", "readout_png", "readout_shapes", "FACE_APPS"]
