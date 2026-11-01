"""REVIEW-1 integration (integrator, 2026-11-01): the CAD recolour fast path (MIN-37) and the purchased insert/feet (MAJ-12)."""

from __future__ import annotations

import json
import struct

from gstudio.cad import _recolor_glb, _recolor_map
from gstudio.cad.export import _srgb_to_linear
from gstudio.cad.modules import PURCHASED_FEET, PURCHASED_INSERT, fasteners
from gstudio.cad.params import LibAccess


def _glb(path, mats):
    doc = {"asset": {"version": "2.0"}, "materials": mats}
    js = json.dumps(doc).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    binc = b"\0" * 8
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binc)) + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(binc), 0x004E4942) + binc)


def _read(path):
    b = path.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n]), struct.unpack("<I", b[8:12])[0], len(b)


def test_min37_recolor_map_rejects_ambiguous_roles():
    old = {"body": [0.5, 0.5, 0.5], "accent": [0.5, 0.5, 0.5]}
    assert _recolor_map(old, {"body": [0.1, 0.2, 0.3], "accent": [0.1, 0.2, 0.3]}) is not None
    assert _recolor_map(old, {"body": [0.1, 0.2, 0.3], "accent": [0.9, 0.2, 0.3]}) is None


def test_min37_recolor_glb_rewrites_only_palette_materials(tmp_path):
    body_old, body_new = (0.725, 0.702, 0.910), (0.80, 0.93, 0.91)
    lin = lambda c: [_srgb_to_linear(x) for x in c] + [1.0]  # noqa: E731
    p = tmp_path / "a.glb"
    _glb(p, [{"name": "PRINT_Shell/Shell", "pbrMetallicRoughness": {"baseColorFactor": lin(body_old)}},
             {"name": "PURCHASE_Display/Pcb", "pbrMetallicRoughness": {"baseColorFactor": [0.007, 0.047, 0.263, 1.0]}}])
    n = _recolor_glb(p, _recolor_map({"body": list(body_old)}, {"body": list(body_new)}))
    doc, total, size = _read(p)
    assert n == 1 and total == size
    assert all(abs(a - b) < 1e-5 for a, b in zip(doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"], lin(body_new)))
    assert doc["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"] == [0.007, 0.047, 0.263, 1.0]


def test_maj12_cad_models_the_purchased_insert_and_feet():
    L = LibAccess(None)
    fz = fasteners(L.get, {"HeatSetInserts": PURCHASED_INSERT, "Feet": PURCHASED_FEET})
    assert fz["insert"]["module"] == PURCHASED_INSERT and fz["insert"]["od"] == 3.5 and fz["insert"]["length"] == 4.0
    assert fz["insert"]["hole_d"] == 3.2 and "estimate" in fz["sources"]["insert"]
    assert fz["foot"]["module"] == PURCHASED_FEET and fz["foot"]["d"] == 8.0 and fz["foot"]["h"] == 2.5
    assert fz["hw_modules"]["HeatSetInserts"] == PURCHASED_INSERT
