"""Shared fixtures for the manual stage tests (run on the VM: cadquery, VTK and EGL are needed)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIX))


class FixtureLib:
    """Library stand-in reading the fixture modules.json and backplane.json."""

    def __init__(self):
        self.mods = {m["id"]: m for m in json.loads((FIX / "minibot" / "modules.json").read_text())}
        self.backplane = json.loads((FIX / "minibot" / "backplane.json").read_text())

    def get(self, mid):
        return self.mods.get(mid)


def make_ctx(out: Path, **opts):
    msgs = []
    return SimpleNamespace(spec=json.loads((out / "spec.json").read_text()), lib=FixtureLib(), out=out,
                           electrical=json.loads((out / "electrical.json").read_text()), artifacts={},
                           options=dict(opts), progress=lambda s, m, p=None: msgs.append((s, m, p)),
                           warn=lambda s, m: msgs.append((s, "WARN " + m, None)), msgs=msgs)


@pytest.fixture(scope="session")
def minibot_cad(tmp_path_factory) -> Path:
    """CAD-stage-shaped outputs for the fixture gadget (geometry built once per session)."""
    import make_minibot

    out = tmp_path_factory.mktemp("minibot")
    make_minibot.build(out)
    return out


@pytest.fixture(scope="session")
def minibot_run(minibot_cad, tmp_path_factory):
    """One full manual stage run with rendering; returns (out, summary, ctx)."""
    import time

    from gstudio.manual import run

    out = tmp_path_factory.mktemp("minibot_run")
    shutil.copytree(minibot_cad, out, dirs_exist_ok=True)
    ctx = make_ctx(out, mesh_cache=str(tmp_path_factory.mktemp("meshcache")))
    t = time.time()
    summary = run(ctx)
    summary["_wall"] = time.time() - t
    return out, summary, ctx


@pytest.fixture()
def minibot_ctx(minibot_cad, tmp_path):
    shutil.copytree(minibot_cad, tmp_path, dirs_exist_ok=True)
    return make_ctx(tmp_path)
