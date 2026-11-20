"""Behaviour settings are checked where the design agent can repair them (2026-11-21).

The jurors' wish ("glows orange when the air gets too dry") came back with every concept unrendered: Gemini wrote
behaviour.settings.humidity_dry_percent and humidity_low_warning for sensor_display, whose setting is humidity_low.
The web image shipped no firmware package, so normalize's settings hook was skipped there, the agent was never told,
and the builder refused each concept render (422). Now the web image ships firmware/__init__.py and settings.py, and
an unknown setting comes with one line naming the template's settings and their ranges."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import good  # noqa: E402
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import _settings_hint, normalize  # noqa: E402

STUDIO = Path(__file__).resolve().parents[2]
JURORS = {"humidity_low_warning": True, "humidity_dry_percent": 40}


@pytest.fixture(scope="module")
def lib():
    return Library()


def _msgs(rep: dict) -> list[str]:
    return [str(e.get("msg", "")) for e in rep["errors"]]


def test_unknown_setting_comes_with_the_template_settings(lib):
    d = good()
    d["behaviour"]["settings"]["volum"] = 30
    _, rep = normalize(d, lib)
    msgs = _msgs(rep)
    assert any(e.get("path") == "behaviour.settings.volum" and "unknown setting" in str(e.get("msg"))
               for e in rep["errors"]), rep["errors"]
    hint = next(m for m in msgs if m.startswith("story_player takes only these settings"))
    assert "capture_mode (child_voice_clip|parent_web|none)" in hint and "volume_max (10 to 100)" in hint


def test_known_settings_get_no_hint(lib):
    _, rep = normalize(good(), lib)
    assert not any("takes only these settings" in m for m in _msgs(rep)), rep["errors"]


def test_sensor_display_hint_names_humidity_low():
    from gstudio import firmware

    h = _settings_hint(firmware, "sensor_display")
    assert "humidity_low (10 to 50)" in h and "led_warning (true|false)" in h and "temperature_unit (C|F)" in h
    assert "soil_dry_percent (5 to 80) [only with a soil probe]" in h
    assert _settings_hint(firmware, "no_such_template") == ""


def test_the_jurors_concept_settings_get_the_hint(monkeypatch, lib):
    monkeypatch.setenv("GS_PACKS_ENABLED", "sensing,schedule")
    spec = json.loads((STUDIO / "library/packs/examples/sensing/plant-monitor/spec.json").read_text())
    spec["behaviour"]["settings"] = dict(JURORS)
    _, rep = normalize(spec, lib)
    msgs = _msgs(rep)
    for k in JURORS:
        assert any(e.get("path") == f"behaviour.settings.{k}" for e in rep["errors"]), rep["errors"]
    assert any(m.startswith("sensor_display takes only these settings") and "humidity_low (10 to 50)" in m for m in msgs)


def test_web_image_layout_checks_settings(tmp_path):
    """The web image's engine, built like web/server/Dockerfile stage 2 (top-level modules, data, library, cad, and only
    firmware/__init__.py + settings.py), with CadQuery, manual, order and pcb absent: the settings check still runs."""
    src = STUDIO / "engine" / "gstudio"
    dst = tmp_path / "engine" / "gstudio"
    dst.mkdir(parents=True)
    for p in src.glob("*.py"):
        shutil.copy2(p, dst / p.name)
    for d in ("data", "library", "cad"):
        if (src / d).is_dir():
            shutil.copytree(src / d, dst / d, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (dst / "firmware").mkdir()
    for f in ("__init__.py", "settings.py"):
        shutil.copy2(src / "firmware" / f, dst / "firmware" / f)
    d = good()
    d["behaviour"]["settings"]["volum"] = 30
    (tmp_path / "spec.json").write_text(json.dumps(d))
    code = ("import json, sys\n"
            "for m in ('cadquery', 'OCP', 'gstudio.manual', 'gstudio.order', 'gstudio.pcb'):\n"
            "    sys.modules[m] = None\n"
            "from gstudio.library import Library\n"
            "from gstudio.normalize import normalize\n"
            "import gstudio.firmware as fw\n"
            "assert fw.__file__.startswith(sys.argv[2]), fw.__file__\n"
            "_, rep = normalize(json.load(open(sys.argv[1])), Library())\n"
            "print(json.dumps([e.get('msg', '') for e in rep['errors']]))\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "engine"), "GS_LIBRARY": str(STUDIO / "library")}
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path / "spec.json"), str(tmp_path)],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    msgs = json.loads(r.stdout.strip().splitlines()[-1])
    assert any("unknown setting" in m for m in msgs), msgs
    assert any(m.startswith("story_player takes only these settings") for m in msgs), msgs
