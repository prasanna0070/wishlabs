"""The collision check's mesh -> manifold3d conversion (cad/checks.py Manifolder): degenerate faces are dropped, a
watertight mesh converts in this process, anything else in a worker process whose crash loses only that shape.

BENCH tomato-station (hack/catalog, 20 Nov): the CAD stage died with signal 11 in geom.to_manifold's piece-by-piece
fallback, fed one- and two-triangle pieces with equal corners from a swept cable route (cat-a.md OVERNIGHT)."""

from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
mf = pytest.importorskip("manifold3d")

from gstudio.cad import checks as C  # noqa: E402

BOX = trimesh.creation.box(extents=(10.0, 10.0, 10.0))


def _with_slivers(m, n: int = 6):
    """The mesh plus n isolated degenerate triangles (corners repeated), like the cable route's merge leftovers."""
    v = np.asarray(m.vertices, float)
    f = np.asarray(m.faces)
    extra_v = np.array([[30.0 + i, 0.0, 0.0] for i in range(n)] + [[30.0 + i, 1.0, 0.0] for i in range(n)])
    base = len(v)
    extra_f = np.array([[base + i, base + i, base + n + i] for i in range(n)])
    return trimesh.Trimesh(vertices=np.vstack([v, extra_v]), faces=np.vstack([f, extra_f]), process=False)


def _open_box():
    return trimesh.Trimesh(vertices=BOX.vertices, faces=BOX.faces[:-2], process=False)  # one side open


def test_clean_mesh_drops_degenerate_faces_and_their_vertices():
    m = C.clean_mesh(_with_slivers(BOX))
    assert len(m.faces) == len(BOX.faces) and len(m.vertices) == len(BOX.vertices)
    assert m.is_watertight


def test_watertight_mesh_converts_in_process():
    conv = C.Manifolder()
    try:
        man = conv.convert_mesh(_with_slivers(BOX), "box")
        assert man is not None and abs(man.volume() - 1000.0) < 1e-3
        assert conv.worker_calls == 0 and conv.p is None
    finally:
        conv.close()


def test_open_mesh_converts_in_the_worker_like_the_old_fallback():
    conv = C.Manifolder()
    try:
        man = conv.convert_mesh(_with_slivers(_open_box()), "open")
        assert conv.worker_calls == 1 and not conv.failed and not conv.worker_deaths
        assert man is not None and abs(man.volume() - 1000.0) < 1e-3  # the hull of the open box, as geom.to_manifold gives
    finally:
        conv.close()
    assert conv.p is None


def test_only_slivers_give_nothing():
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5], [6, 5, 5]])
    f = np.array([[0, 1, 2], [3, 3, 4], [3, 4, 4], [0, 0, 1], [1, 2, 2]])
    conv = C.Manifolder()
    try:
        assert conv.convert_mesh(trimesh.Trimesh(vertices=v, faces=f, process=False), "slivers") is None
    finally:
        conv.close()


def test_a_crashing_worker_loses_only_that_shape(monkeypatch):
    monkeypatch.setenv("GS_MANIFOLD_WORKER_TEST_CRASH", "1")
    conv = C.Manifolder(timeout=60)
    try:
        assert conv.convert_mesh(_open_box(), "PRINT_Shell") is None
        assert conv.failed == ["PRINT_Shell"] and len(conv.worker_deaths) == 2
        assert all("signal 11" in d for d in conv.worker_deaths), conv.worker_deaths
        man = conv.convert_mesh(BOX, "box")  # this process is fine and watertight shapes still convert here
        assert man is not None and abs(man.volume() - 1000.0) < 1e-3
    finally:
        conv.close()


def test_inprocess_switch_keeps_the_old_path(monkeypatch):
    cq = pytest.importorskip("cadquery")
    monkeypatch.setenv("GS_MANIFOLD_INPROCESS", "1")
    conv = C.Manifolder()
    man = conv.convert(cq.Workplane().box(10, 10, 10).val(), 0.08, 0.3, "box")
    assert man is not None and abs(man.volume() - 1000.0) < 1e-2 and conv.worker_calls == 0


def test_shape_path_uses_the_worker_for_an_open_shell():
    cq = pytest.importorskip("cadquery")
    box = cq.Workplane().box(10, 10, 10).val()
    shell = cq.Shell.makeShell(box.Faces()[:5])
    conv = C.Manifolder()
    try:
        man = conv.convert(shell, 0.08, 0.3, "REFERENCE_CableX")
        assert conv.worker_calls == 1 and man is not None and abs(man.volume() - 1000.0) < 1e-2
    finally:
        conv.close()
