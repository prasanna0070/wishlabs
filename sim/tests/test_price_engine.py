"""Tests for the price engine: blueprint worked examples, pricing algebra, simulation and quote rules.

  python3 -I -m unittest discover -s sim/tests -v      (from the project root)
"""
import copy
import json
import random
import sys
import unittest
from pathlib import Path

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))
import price_engine as pe  # noqa: E402

J = lambda p: json.loads((SIM / p).read_text())
SLICE = {"parts": {"shell_print": {"filament_g": 116.56, "print_h": 8.591}, "plate": {"filament_g": 10.86, "print_h": 1.214}}}


def engine(spec=None, ass=None, sl=None):
    return pe.Engine(J("spec/catalog.json"), spec or J("spec/bedtime-robot.json"), ass or J("spec/assumptions.json"), sl or SLICE)


class BlueprintExamples(unittest.TestCase):
    """The engine must reproduce the numbers the blueprint works through by hand."""

    def test_margin_is_not_markup(self):
        # §8.2: "30% gross margin on 4,000 COGS requires ~5,714 pretax, not 5,200"
        self.assertAlmostEqual(pe.price_for_margin(4000, 0.30), 5714.29, places=2)

    def test_archetype_table_voice_companion(self):
        # §8.7: voice + display desk companion, COGS 4,300 -> 6,143 at 30% GM
        self.assertEqual(round(pe.price_for_margin(4300, 0.30)), 6143)
        for cogs, price in [(1850, 2643), (2900, 4143), (5950, 8500)]:
            self.assertEqual(round(pe.price_for_margin(cogs, 0.30)), price)

    def test_illustrative_print_cost(self):
        # §8.3: 125 g at 1.90/g + 3.4 h at 28/h + 130 setup + 75 finish + 60 reprint = 598
        self.assertEqual(round(pe.print_cost(125, 3.4, 1.90, 28.0, 130, 75, 60)), 598)

    def test_setup_amortisation_table(self):
        # §8.7 sensitivity: 3,000 over N builds
        for n, per_unit in [(1, 3000), (5, 600), (20, 150), (100, 30)]:
            self.assertEqual(3000 / n, per_unit)


class PricingAlgebra(unittest.TestCase):
    def test_fee_price_leaves_exact_margin(self):
        for cost, m, f, gst in [(5000, 0.30, 0.0236, 0.18), (1200, 0.25, 0.03, 0.0), (9000, 0.10, 0.02, 0.12)]:
            p = pe.price_with_fee(cost, m, f, gst)
            self.assertAlmostEqual(pe.margin_at_price(p, cost, f, gst), m, places=9)

    def test_fee_price_without_fee_equals_plain_margin(self):
        self.assertAlmostEqual(pe.price_with_fee(4000, 0.3, 0.0), pe.price_for_margin(4000, 0.3))

    def test_impossible_margin_rejected(self):
        with self.assertRaises(ValueError):
            pe.price_with_fee(1000, 0.98, 0.03, 0.18)
        with self.assertRaises(ValueError):
            pe.price_for_margin(1000, 1.0)

    def test_round_up_99(self):
        self.assertEqual(pe.round_up_99(7312), 7399)
        self.assertEqual(pe.round_up_99(7399), 7399)
        self.assertEqual(pe.round_up_99(7400), 7499)
        self.assertGreaterEqual(pe.round_up_99(9697.3), 9697.3)


class Logistics(unittest.TestCase):
    def test_billable_weight_is_max_of_actual_and_volumetric(self):
        self.assertAlmostEqual(pe.billable_weight(0.65, [22, 18, 20], 5000), 1.584)
        self.assertEqual(pe.billable_weight(3.0, [10, 10, 10], 5000), 3.0)

    def test_courier_slabs(self):
        self.assertEqual(pe.courier_cost(1.584, 50, 35), 35 + 4 * 50)
        self.assertEqual(pe.courier_cost(0.5, 50, 35), 35 + 50)


class Geometry(unittest.TestCase):
    def test_stl_volume_of_unit_cube(self):
        # 10 mm cube = 1 cm3, as an ASCII STL
        v = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
        faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
        txt = "solid c\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {v[i][0]} {v[i][1]} {v[i][2]}\n" for i in f) + "endloop\nendfacet\n" for f in faces) + "endsolid c\n"
        p = SIM / "tests" / "_cube.stl"
        p.write_text(txt)
        try:
            self.assertAlmostEqual(pe.stl_volume_cm3(p), 1.0, places=6)
        finally:
            p.unlink()

    def test_real_shell_volume_matches_slicer_material(self):
        # the slicer's plastic (91.8 cm3 shell + supports) should be close to the model volume
        out = SIM / "out" / "shell.stl"
        if not out.exists():
            self.skipTest("run the slicer first")
        self.assertTrue(80 < pe.stl_volume_cm3(out) < 100)


class Simulation(unittest.TestCase):
    def setUp(self):
        self.eng = engine()
        self.sc = pe.SCENARIOS[2]

    def test_reproducible_with_seed(self):
        a = self.eng.quote("B_parent_run", self.sc, n=2000, seed=3)
        b = self.eng.quote("B_parent_run", self.sc, n=2000, seed=3)
        self.assertEqual((a.p5, a.p50, a.p95), (b.p5, b.p50, b.p95))

    def test_percentiles_ordered_and_price_covers_bad_case(self):
        q = self.eng.quote("B_parent_run", self.sc, n=4000)
        self.assertLess(q.p5, q.p50)
        self.assertLess(q.p50, q.p95)
        self.assertGreaterEqual(q.margin_at_list_p50, 0.30 - 1e-9)
        self.assertGreaterEqual(q.margin_at_list_p95, 0.10 - 1e-9)

    def test_parts_draw_stays_inside_catalog_ranges(self):
        rng = random.Random(1)
        v = self.eng.spec["variants"]["B_parent_run"]
        lo = sum(self.eng.cat[m["catalog_id"]]["low"] * m["qty"] for m in v["modules"])
        hi = sum(self.eng.cat[m["catalog_id"]]["high"] * m["qty"] for m in v["modules"])
        for _ in range(500):
            parts = self.eng.draw("B_parent_run", self.sc, rng)["parts"]
            self.assertTrue(lo <= parts <= hi)

    def test_breakdown_adds_up(self):
        q = self.eng.quote("B_parent_run", self.sc, n=3000)
        b = q.breakdown_mean
        parts = sum(b[k] for k in ("parts", "print", "assembly_test", "setup_nre", "courier", "warranty_reserve", "returns"))
        self.assertAlmostEqual(parts, b["total_excl_fee"], delta=1.0)

    def test_one_off_costs_more_than_steady_state(self):
        one = self.eng.quote("B_parent_run", pe.SCENARIOS[0], n=3000)
        steady = self.eng.quote("B_parent_run", pe.SCENARIOS[2], n=3000)
        self.assertGreater(one.p50, steady.p50 + 2500)   # the 3,000 setup alone

    def test_toy_returns_raise_cost(self):
        steady = self.eng.quote("B_parent_run", pe.SCENARIOS[2], n=3000)
        toy = self.eng.quote("B_parent_run", pe.SCENARIOS[3], n=3000)
        self.assertGreater(toy.p50, steady.p50 * 1.2)


class QuoteRules(unittest.TestCase):
    """Blueprint §1.3: only call a price guaranteed when everything is contracted and safe."""

    def test_never_binding_today(self):
        eng = engine()
        for v in ("A_as_asked", "B_parent_run"):
            for sc in pe.SCENARIOS:
                self.assertNotEqual(eng.quote(v, sc, n=300).status, "BINDING")

    def test_child_mic_cloud_is_unsupported(self):
        q = engine().quote("A_as_asked", pe.SCENARIOS[2], n=300)
        self.assertEqual(q.status, "UNSUPPORTED_AT_LAUNCH")
        self.assertTrue(any("microphone" in r for r in q.reasons))

    def test_parent_run_has_no_microphone(self):
        mods = {m["catalog_id"] for m in J("spec/bedtime-robot.json")["variants"]["B_parent_run"]["modules"]}
        self.assertNotIn("MIC_I2S_A", mods)

    def test_every_module_is_in_the_catalog(self):
        cat = J("spec/catalog.json")["items"]
        for v in J("spec/bedtime-robot.json")["variants"].values():
            for m in v["modules"]:
                self.assertIn(m["catalog_id"], cat)

    def test_unknown_destination_is_stated(self):
        q = engine().quote("B_parent_run", pe.SCENARIOS[2], n=300)
        self.assertTrue(any("pincode" in r for r in q.reasons))


class Speed(unittest.TestCase):
    def test_local_quote_well_inside_60_seconds(self):
        import time
        t = time.perf_counter()
        engine().quote("B_parent_run", pe.SCENARIOS[2], n=20000)
        self.assertLess(time.perf_counter() - t, 5.0)


if __name__ == "__main__":
    unittest.main()
