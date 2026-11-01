"""The full envelope normalize checks against the printer (live failure 2026-11-02, "Storybot Pal").

The CAD made that shell 167.6 x 97.6 x 212.7 mm (print/PRINT_Shell.stl of _out/livefail/r1 on the dev VM) while
normalize estimated 148 x 105 x 195 mm, so a design too tall for a 210 mm Z printer passed validation.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gstudio import envelope  # noqa: E402
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import normalize  # noqa: E402

STUDIO = Path(__file__).resolve().parents[2]
LIVE = STUDIO / "tools" / "livefail" / "toaster-bot-spec.json"


@pytest.fixture(scope="module")
def lib():
    return Library()


def live_spec() -> dict:
    d = json.loads(LIVE.read_text())
    d.pop("derived", None)
    return d


def scaled(d: dict, k: float) -> dict:
    d = json.loads(json.dumps(d))
    b = d["body"]
    b["outline"] = [[x * k, z * k] for x, z in b["outline"]]
    b["depth"] = round(b["depth"] * k, 1)
    for x in d["decor"]:
        x["at"] = [v * k for v in x["at"]]
        if "size" in x:
            x["size"] = [v * k for v in x["size"]]
    for f in d["features"]:
        f["at"] = [v * k for v in f["at"]]
    return d


def test_cad_spline_matches_opencascade():
    """cad_spline reproduces cadquery's Edge.makeSpline (GeomAPI_Interpolate) bounds within 0.05 mm."""
    cq = pytest.importorskip("cadquery")
    from gstudio.cad.body import outline_face

    outlines = [live_spec()["body"]["outline"]]
    for ex in ("bedtime-robot",):
        outlines.append(json.loads((STUDIO / "examples" / ex / "spec.json").read_text())["body"]["outline"])
    outlines += [[[-50, 0], [50, 0], [70, 60], [40, 120], [0, 130], [-40, 120], [-70, 60]],
                 [[-40, 0], [40, 0], [55, 90], [0, 150], [-55, 90]],
                 [[-30, 0], [30, 0], [0, 80]]]
    for o in outlines:
        bb = outline_face(o).BoundingBox()
        pts = envelope.cad_outline_polygon(o, 60)
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        assert min(xs) == pytest.approx(bb.xmin, abs=0.05) and max(xs) == pytest.approx(bb.xmax, abs=0.05), o
        assert max(zs) == pytest.approx(bb.zmax, abs=0.05), o
    assert cq is not None


def test_live_spec_envelope_matches_what_the_cad_built(lib):
    sp, rep = normalize(live_spec(), lib)
    assert rep["ok"], rep["errors"]
    x, y, z = sp.derived.size_mm
    # CAD r1 shell 167.6 wide and 212.7 tall; depth is the full body (shell 97.6 + rear cover)
    assert x == pytest.approx(167.6, abs=1.0) and z == pytest.approx(212.7, abs=1.0) and y == pytest.approx(105.0, abs=0.1)
    ears = rep["envelope"]["ears"]
    assert len(ears) == 2 and all(e["style"] == "antenna" for e in ears)
    # CAD r1 moved the antennae from x=45 to x=25 (base z 192.2, top 212.66)
    assert all(abs(abs(e["x"]) - 25.0) <= 1.5 and e["bounds"][3] == pytest.approx(212.66, abs=1.0) for e in ears), ears
    assert rep["envelope"]["fits_upright"] is False and rep["envelope"]["fits_any_orientation"] is True
    w = next(w for w in rep["warnings"] if w.startswith("bed:"))
    assert "prints on its side with supports" in w and "antennae reach z 212" in w
    assert "make the ears/antennae at least 5 mm shorter (decor size[1]) so the top stays at or below 208 mm" in w


def test_following_the_warning_prints_upright(lib):
    """Antennae 5 mm shorter: estimate 207.3 mm tall; the CAD preview of that spec on the dev VM measured 206.9 mm."""
    d = live_spec()
    for x in d["decor"]:
        if x["type"] == "ear":
            x["size"][1] -= 5
    sp, rep = normalize(d, lib)
    assert rep["ok"] and rep["envelope"]["fits_upright"] and not [w for w in rep["warnings"] if w.startswith("bed:")]
    assert sp.derived.size_mm[2] == pytest.approx(206.9, abs=1.0)


def test_envelope_too_big_for_any_orientation_is_rejected_with_a_scale_hint(lib):
    _, rep = normalize(scaled(live_spec(), 1.25), lib)
    e = next(e for e in rep["errors"] if e.get("code") == "bed")
    assert "does not fit the 250 x 210 x 210 mm printer in any orientation" in e["msg"] and "antennae reach" in e["msg"]
    k = float(e["fix"].split("scale the whole design by ")[1].split()[0])
    assert 0.5 < k < 1.0
    # following the hint gives a design that fits
    _, rep2 = normalize(scaled(scaled(live_spec(), 1.25), k), lib)
    assert not [x for x in rep2["errors"] if x.get("code") == "bed"], rep2["errors"]


def test_example_that_fits_upright_has_no_bed_warning(lib):
    d = json.loads((STUDIO / "examples" / "bedtime-robot" / "spec.json").read_text())
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    assert rep["envelope"]["fits_upright"] is True and not [w for w in rep["warnings"] if w.startswith("bed:")]
    # CAD preview of this example on the dev VM (2026-11-02): 150.3 x 118.0 x 202.6 mm
    assert list(sp.derived.size_mm) == pytest.approx([150.3, 118.0, 202.6], abs=1.0)


def test_fit_orientations():
    bed = (250, 210, 210)
    assert envelope.fit_orientations((167.6, 105, 212.7), bed)["upright"] is False
    assert envelope.fit_orientations((167.6, 105, 212.7), bed)["any"] is True
    assert envelope.fit_orientations((100, 240, 50), bed)["upright"] is True
    f = envelope.fit_orientations((300, 220, 50), bed)
    assert f["any"] is False and f["scale_to_fit"] == pytest.approx(250 / 300)
