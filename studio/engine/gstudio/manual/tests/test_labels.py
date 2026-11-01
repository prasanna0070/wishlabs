"""Label placement: pure geometry, no rendering."""

import random

import numpy as np

from gstudio.manual import labels as L


def test_segments_cross_basic():
    assert L.segments_cross((0, 0), (10, 10), (0, 10), (10, 0))
    assert not L.segments_cross((0, 0), (10, 0), (0, 5), (10, 5))
    assert not L.segments_cross((0, 0), (5, 5), (5, 5), (9, 1))  # shared endpoint is allowed


def test_box_and_segment():
    b = L.Box(10, 10, 20, 20)
    assert L.segment_hits_box((0, 15), (30, 15), b)
    assert not L.segment_hits_box((0, 0), (30, 5), b)
    assert L.attach_point(b, (40, 15)) == (20, 15)


def _labels(rng, n, w=1000, h=900):
    return [L.LabelIn(anchor=(rng.uniform(250, w - 250), rng.uniform(150, h - 150)), w=rng.uniform(110, 260),
                      h=rng.choice([34, 58]), priority=rng.randint(0, 3)) for _ in range(n)]


def test_random_layouts_have_no_overlaps():
    rng = random.Random(7)
    panel = L.Box(0, 0, 1000, 900)
    for trial in range(25):
        labs = _labels(rng, rng.randint(2, 8))
        lay = L.place(labs, panel)
        assert len(lay.placed) == len(labs)
        assert L.audit(lay.placed, panel) == [], (trial, L.audit(lay.placed, panel))


def test_clustered_anchors_still_clean():
    panel = L.Box(0, 0, 1000, 1000)
    labs = [L.LabelIn(anchor=(500 + 6 * i, 500 + 4 * (i % 3)), w=200, h=58) for i in range(7)]
    lay = L.place(labs, panel)
    assert L.audit(lay.placed, panel) == []


def test_mask_pushes_labels_off_the_part():
    panel = L.Box(0, 0, 1000, 800)
    mask = np.zeros((800, 1000), bool)
    mask[200:600, 300:700] = True  # the highlighted part
    labs = [L.LabelIn(anchor=(500, 400), w=180, h=34)]
    lay = L.place(labs, panel, mask=mask)
    b = lay.placed[0].box
    cover = mask[int(b.y0):int(b.y1), int(b.x0):int(b.x1)].mean()
    assert cover < 0.05


def test_audit_detects_problems():
    a = L.Placed(0, L.Box(0, 0, 100, 40), (300, 300), (100, 40))
    b = L.Placed(1, L.Box(50, 20, 150, 60), (10, 300), (50, 60))
    v = L.audit([a, b])
    assert any("overlap" in x for x in v)
