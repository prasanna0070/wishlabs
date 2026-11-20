"""Pack freeform, contract section 8 request 3: a display shows what its firmware app shows: the idle face only for
desk_companion and story_player, a plain readout for the other apps (renders and GLB screen texture); with the pack
off every display keeps the idle face."""
from __future__ import annotations

import struct

import pytest

from gstudio.cad import screens as SC

SCREEN = {"c": [0, -60, 80], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0], "w": 43.2, "h": 32.4, "kind": "lcd"}
SENSOR = {"template": "sensor_display", "decor": []}
COMPANION = {"template": "desk_companion", "decor": []}


@pytest.fixture
def packs(monkeypatch):
    def set_(names: str):
        monkeypatch.delenv("GS_PACKS_OFF", raising=False)
        monkeypatch.setenv("GS_PACKS_ENABLED", names)
    return set_


def test_face_free_follows_the_firmware_app_and_the_pack(packs):
    packs("sensing,schedule")
    assert not SC.face_free(SENSOR)
    packs("sensing,schedule,freeform")
    assert SC.face_free(SENSOR) and SC.face_free({"behaviour": {"template": "notifier_clock"}})
    assert not SC.face_free(COMPANION) and not SC.face_free({"behaviour": {"template": "story_player"}})


def test_mark_copies_only_when_the_app_draws_no_face(packs):
    packs("freeform")
    out = SC.mark([SCREEN], SENSOR)
    assert out[0]["face"] is False and "face" not in SCREEN and out[0]["w"] == SCREEN["w"]
    assert SC.mark([SCREEN], COMPANION)[0] is SCREEN
    packs("")
    assert SC.mark([SCREEN], SENSOR)[0] is SCREEN and SC.mark(None, {}) == []


def test_readout_png_and_shapes():
    png = SC.readout_png("lcd", (320, 240), scale=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (640, 480)
    shapes = SC.readout_shapes(43.2, 32.4)
    assert len(shapes) == 4
    for poly, col, dz in shapes:
        x0, y0, x1, y1 = poly.bounds
        assert -21.6 <= x0 < x1 <= 21.6 and -16.2 <= y0 < y1 <= 16.2 and dz > 0


def test_screen_png_picks_the_readout(packs):
    from gstudio.cad.export import _screen_png, idle_face_png

    assert _screen_png(dict(SCREEN), (320, 240)) == idle_face_png("lcd", (320, 240))
    assert _screen_png(dict(SCREEN, face=False), (320, 240)) == SC.readout_png("lcd", (320, 240))
