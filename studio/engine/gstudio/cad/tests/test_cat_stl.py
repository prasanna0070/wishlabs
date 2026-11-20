"""STL export repairs (cad/export.py): a tessellation pinhole under 1 mm across is closed (a sculpted shark's tail blend
left one 0.2 x 0.3 x 0.2 mm loop of 32 open edges, so print_watertight failed); a real opening is never closed."""

from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from gstudio.cad.export import PINHOLE_MM, fill_pinholes  # noqa: E402


def _without_fan(m, center):
    """The mesh minus every face that touches the vertex nearest to `center` (one small hole around it)."""
    vi = int(np.argmin(np.linalg.norm(m.vertices - np.asarray(center), axis=1)))
    keep = ~(m.faces == vi).any(axis=1)
    out = trimesh.Trimesh(vertices=m.vertices, faces=m.faces[keep], process=False)
    out.remove_unreferenced_vertices()
    return out


def test_pinhole_is_closed_and_the_volume_kept():
    s = trimesh.creation.icosphere(subdivisions=4, radius=5.0)  # edges about 0.3 mm
    holed = _without_fan(s, [0, 0, 5])
    assert not holed.is_watertight
    w, n = fill_pinholes(holed)
    assert n == 1 and w is not None and w.is_watertight and w.is_winding_consistent
    assert abs(w.volume - s.volume) < 0.01 * s.volume


def test_real_opening_is_not_closed():
    s = trimesh.creation.icosphere(subdivisions=2, radius=20.0)  # edges about 5 mm: the hole is far over 1 mm
    holed = _without_fan(s, [0, 0, 20])
    w, n = fill_pinholes(holed)
    assert w is None and n == 0
    assert PINHOLE_MM == 1.0


def test_watertight_mesh_needs_nothing():
    assert fill_pinholes(trimesh.creation.box(extents=(10, 10, 10))) == (None, 0)


def test_two_pinholes_both_closed():
    s = trimesh.creation.icosphere(subdivisions=4, radius=5.0)
    holed = _without_fan(_without_fan(s, [0, 0, 5]), [0, 0, -5])
    w, n = fill_pinholes(holed)
    assert n == 2 and w.is_watertight


def test_stl_writer_reports_no_repair_for_a_clean_solid(tmp_path):
    cq = pytest.importorskip("cadquery")
    from gstudio.cad.export import _stl

    f = tmp_path / "box.stl"
    assert _stl(cq.Workplane().box(20, 20, 10).edges("|Z").fillet(3).val(), f) == []
    assert trimesh.load_mesh(f).is_watertight
