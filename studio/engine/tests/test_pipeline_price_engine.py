"""The vendored price engine must still pass the sim's tests: blueprint worked examples, algebra, simulation, rules.

Ported from ../sim/tests/test_price_engine.py, pointed at gstudio/order/price_engine.py and its vendored data.
"""
import json
import random
import time

import pytest

from gstudio.order import price_engine as pe

DATA = pe.DATA
J = lambda name: json.loads((DATA / name).read_text())  # noqa: E731
SLICE = {"parts": {"shell_print": {"filament_g": 116.56, "print_h": 8.591}, "plate": {"filament_g": 10.86, "print_h": 1.214}}}


def engine(spec=None, ass=None, sl=None):
    return pe.Engine(J("sim-catalog.json"), spec or J("sim-bedtime-robot.json"), ass or J("sim-assumptions.json"), sl or SLICE)


# ---------------------------------------------------------------- blueprint worked examples

def test_margin_is_not_markup():
    # §8.2: "30% gross margin on 4,000 COGS requires ~5,714 pretax, not 5,200"
    assert pe.price_for_margin(4000, 0.30) == pytest.approx(5714.29, abs=0.005)


def test_archetype_table_voice_companion():
    # §8.7: voice + display desk companion, COGS 4,300 -> 6,143 at 30% GM
    assert round(pe.price_for_margin(4300, 0.30)) == 6143
    for cogs, price in [(1850, 2643), (2900, 4143), (5950, 8500)]:
        assert round(pe.price_for_margin(cogs, 0.30)) == price


def test_illustrative_print_cost():
    # §8.3: 125 g at 1.90/g + 3.4 h at 28/h + 130 setup + 75 finish + 60 reprint = 598
    assert round(pe.print_cost(125, 3.4, 1.90, 28.0, 130, 75, 60)) == 598


def test_setup_amortisation_table():
    for n, per_unit in [(1, 3000), (5, 600), (20, 150), (100, 30)]:
        assert 3000 / n == per_unit


# ---------------------------------------------------------------- algebra

@pytest.mark.parametrize("cost,m,f,gst", [(5000, 0.30, 0.0236, 0.18), (1200, 0.25, 0.03, 0.0), (9000, 0.10, 0.02, 0.12)])
def test_fee_price_leaves_exact_margin(cost, m, f, gst):
    p = pe.price_with_fee(cost, m, f, gst)
    assert pe.margin_at_price(p, cost, f, gst) == pytest.approx(m, abs=1e-9)


def test_fee_price_without_fee_equals_plain_margin():
    assert pe.price_with_fee(4000, 0.3, 0.0) == pytest.approx(pe.price_for_margin(4000, 0.3))


def test_impossible_margin_rejected():
    with pytest.raises(ValueError):
        pe.price_with_fee(1000, 0.98, 0.03, 0.18)
    with pytest.raises(ValueError):
        pe.price_for_margin(1000, 1.0)


def test_round_up_99():
    assert pe.round_up_99(7312) == 7399
    assert pe.round_up_99(7399) == 7399
    assert pe.round_up_99(7400) == 7499
    assert pe.round_up_99(9697.3) >= 9697.3


def test_billable_weight_and_courier_slabs():
    assert pe.billable_weight(0.65, [22, 18, 20], 5000) == pytest.approx(1.584)
    assert pe.billable_weight(3.0, [10, 10, 10], 5000) == 3.0
    assert pe.courier_cost(1.584, 50, 35) == 35 + 4 * 50
    assert pe.courier_cost(0.5, 50, 35) == 35 + 50


def test_stl_volume_of_unit_cube(tmp_path):
    v = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    txt = "solid c\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {v[i][0]} {v[i][1]} {v[i][2]}\n" for i in f)
                                + "endloop\nendfacet\n" for f in faces) + "endsolid c\n"
    p = tmp_path / "cube.stl"
    p.write_text(txt)
    assert pe.stl_volume_cm3(p) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------- simulation

SC = pe.SCENARIOS[2]


def test_reproducible_with_seed():
    a = engine().quote("B_parent_run", SC, n=2000, seed=3)
    b = engine().quote("B_parent_run", SC, n=2000, seed=3)
    assert (a.p5, a.p50, a.p95) == (b.p5, b.p50, b.p95)


def test_percentiles_ordered_and_price_covers_bad_case():
    q = engine().quote("B_parent_run", SC, n=4000)
    assert q.p5 < q.p50 < q.p95
    assert q.margin_at_list_p50 >= 0.30 - 1e-9
    assert q.margin_at_list_p95 >= 0.10 - 1e-9


def test_parts_draw_stays_inside_catalog_ranges():
    eng = engine()
    rng = random.Random(1)
    v = eng.spec["variants"]["B_parent_run"]
    lo = sum(eng.cat[m["catalog_id"]]["low"] * m["qty"] for m in v["modules"])
    hi = sum(eng.cat[m["catalog_id"]]["high"] * m["qty"] for m in v["modules"])
    for _ in range(500):
        assert lo <= eng.draw("B_parent_run", SC, rng)["parts"] <= hi


def test_breakdown_adds_up():
    b = engine().quote("B_parent_run", SC, n=3000).breakdown_mean
    parts = sum(b[k] for k in ("parts", "print", "assembly_test", "setup_nre", "courier", "warranty_reserve", "returns"))
    assert parts == pytest.approx(b["total_excl_fee"], abs=1.0)


def test_one_off_costs_more_than_steady_state():
    one = engine().quote("B_parent_run", pe.SCENARIOS[0], n=3000)
    steady = engine().quote("B_parent_run", pe.SCENARIOS[2], n=3000)
    assert one.p50 > steady.p50 + 2500


def test_toy_returns_raise_cost():
    steady = engine().quote("B_parent_run", pe.SCENARIOS[2], n=3000)
    toy = engine().quote("B_parent_run", pe.SCENARIOS[3], n=3000)
    assert toy.p50 > steady.p50 * 1.2


# ---------------------------------------------------------------- quote rules (blueprint §1.3)

def test_never_binding_today():
    eng = engine()
    for v in ("A_as_asked", "B_parent_run"):
        for sc in pe.SCENARIOS:
            assert eng.quote(v, sc, n=300).status != "BINDING"


def test_child_mic_cloud_is_unsupported():
    q = engine().quote("A_as_asked", SC, n=300)
    assert q.status == "UNSUPPORTED_AT_LAUNCH"
    assert any("microphone" in r for r in q.reasons)


def test_sim_variant_without_key_stays_child_directed():
    q = engine().quote("B_parent_run", SC, n=300)
    assert q.status == "ESTIMATE_REVIEW_REQUIRED"
    assert q.cloud_month  # the sim's story-generation cloud cost is still reported


def test_gstudio_extensions_adult_and_no_cloud():
    spec = J("sim-bedtime-robot.json")
    v = spec["variants"]["B_parent_run"]
    v.update(child_directed=False, cloud=None, extra_reasons=["extra reason"])
    q = engine(spec=spec).quote("B_parent_run", SC, n=300)
    assert q.status == "ESTIMATE"
    assert q.cloud_month == {}
    assert q.reasons[-1] == "extra reason"


def test_every_module_is_in_the_catalog():
    cat = J("sim-catalog.json")["items"]
    for v in J("sim-bedtime-robot.json")["variants"].values():
        for m in v["modules"]:
            assert m["catalog_id"] in cat


def test_unknown_destination_is_stated():
    q = engine().quote("B_parent_run", SC, n=300)
    assert any("pincode" in r for r in q.reasons)


def test_local_quote_well_inside_60_seconds():
    t = time.perf_counter()
    engine().quote("B_parent_run", SC, n=20000)
    assert time.perf_counter() - t < 5.0


def test_load_all_defaults_to_vendored_data():
    eng = pe.load_all()
    assert "B_parent_run" in eng.spec["variants"]
