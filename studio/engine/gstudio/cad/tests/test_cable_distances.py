"""Cable router signed distances in a worker process (VTK's implicit distance segfaulted inside the CAD stage process,
at random, on the VM 2026-11-20; a segfault there killed the whole CAD stage). Needs pyvista: run on the VM."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import numpy as np
import pytest

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))

from gstudio.cad.cables import Distances  # noqa: E402

# a 20 mm cube centred on the origin, as legacy VTK faces (two triangles per side, outward winding)
V = np.array([[x, y, z] for x in (-10.0, 10.0) for y in (-10.0, 10.0) for z in (-10.0, 10.0)])
QUADS = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
F = np.array([[3, a, b, c] for q in QUADS for a, b, c in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))], dtype=np.int64).ravel()
PTS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 15.0], [7.0, 0.0, 0.0], [0.0, 30.0, 0.0]])
WANT = np.array([-10.0, 5.0, -3.0, 20.0])  # negative inside


def test_worker_matches_in_process(monkeypatch):
    sd = Distances()
    try:
        assert sd.p is not None and sd.p.poll() is None  # a separate process
        k = sd.mesh((V, F))
        got = sd.signed(k, PTS)
        np.testing.assert_allclose(got, WANT, atol=1e-6)
        np.testing.assert_allclose(sd.signed(k, PTS[2]), WANT[2:3], atol=1e-6)  # single point, F-ordered input is fine too
        np.testing.assert_allclose(sd.signed(k, np.asfortranarray(PTS)), WANT, atol=1e-6)
    finally:
        sd.close()
    assert sd.p is None
    monkeypatch.setenv("GS_CABLE_SD_INPROCESS", "1")
    local = Distances()
    assert local.p is None
    np.testing.assert_allclose(local.signed(local.mesh((V, F)), PTS), WANT, atol=1e-6)


def test_a_dead_worker_raises_instead_of_killing_the_caller():
    sd = Distances()
    k = sd.mesh((V, F))
    os.kill(sd.p.pid, signal.SIGSEGV)  # what VTK did inside the CAD stage
    sd.p.wait(10)
    with pytest.raises(RuntimeError, match="worker died"):
        sd.signed(k, PTS)
    sd.close()
