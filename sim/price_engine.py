"""Price engine for one product spec.

Implements the blueprint equations (docs/blueprint-v1.0.md):
  §8.2 landed variable cost and margin-correct pricing with an ad-valorem payment fee
  §8.3 printed-shell cost from slicer output
  §8.4 operation-based assembly cost
  §8.5 billable weight and courier cost
  §8.8 Monte Carlo p5 / p50 / p95 landed cost, and a binding-price rule
  §1.3 quote status: never "BINDING" without contracts, a known destination and a clean safety check

Standard library only, so it runs anywhere with Python 3.10+.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- pricing algebra (§8.2)

def price_for_margin(cost: float, margin: float) -> float:
    """Pretax price that leaves `margin` of revenue after `cost` (margin, not markup)."""
    if not 0 <= margin < 1:
        raise ValueError("margin must be in [0, 1)")
    return cost / (1 - margin)


def price_with_fee(cost_excl_fee: float, margin: float, fee_rate: float, gst: float = 0.0, fixed_fee: float = 0.0) -> float:
    """Pretax price P when the gateway takes fee_rate of the GST-inclusive amount.

    P - cost - fixed - fee_rate * P * (1 + gst) = margin * P
    => P = (cost + fixed) / (1 - margin - fee_rate * (1 + gst))
    """
    denom = 1 - margin - fee_rate * (1 + gst)
    if denom <= 0:
        raise ValueError("margin + fees leave nothing to cover cost")
    return (cost_excl_fee + fixed_fee) / denom


def margin_at_price(price: float, cost_excl_fee: float, fee_rate: float, gst: float = 0.0, fixed_fee: float = 0.0) -> float:
    return (price - cost_excl_fee - fixed_fee - fee_rate * price * (1 + gst)) / price


def round_up_99(x: float) -> int:
    """Round up to the next price ending in 99 (e.g. 7,312 -> 7,399)."""
    return int(math.ceil((x + 1) / 100.0) * 100 - 1)

# ---------------------------------------------------------------- cost blocks

def print_cost(material_g: float, machine_h: float, rate_g: float, rate_h: float,
               setup: float, finish: float, reprint_reserve: float) -> float:
    """§8.3: material + machine time + setup/support removal + finish + expected reprint."""
    return material_g * rate_g + machine_h * rate_h + setup + finish + reprint_reserve


def billable_weight(actual_kg: float, box_cm: list[float], divisor: float) -> float:
    vol = box_cm[0] * box_cm[1] * box_cm[2] / divisor
    return max(actual_kg, vol)


def courier_cost(billable_kg: float, per_half_kg: float, fixed: float) -> float:
    return fixed + math.ceil(billable_kg / 0.5 - 1e-9) * per_half_kg


def stl_volume_cm3(path: str | Path) -> float:
    """Signed-tetrahedron volume of an ASCII or binary STL, in cm3."""
    data = Path(path).read_bytes()
    tris = []
    if data[:5] == b"solid" and b"facet" in data[:1000]:
        verts = []
        for line in data.decode("ascii", "ignore").splitlines():
            s = line.strip()
            if s.startswith("vertex"):
                verts.append(tuple(float(v) for v in s.split()[1:4]))
                if len(verts) == 3:
                    tris.append(verts)
                    verts = []
    else:
        import struct
        n = struct.unpack_from("<I", data, 80)[0]
        for i in range(n):
            off = 84 + i * 50 + 12
            v = struct.unpack_from("<9f", data, off)
            tris.append([v[0:3], v[3:6], v[6:9]])
    vol = 0.0
    for a, b, c in tris:
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(vol) / 1000.0

# ---------------------------------------------------------------- the engine

@dataclass
class Scenario:
    name: str
    qty_key: str            # "1", "10" or "100": where parts sit in their planning range
    first_article: bool     # one-off of a never-built design
    nre_units: int          # units the setup NRE is spread over (0 = no NRE)
    returns: str            # "returns_defect_only" or "returns_novelty"
    note: str = ""


@dataclass
class Quote:
    variant: str
    scenario: str
    breakdown_mean: dict
    p5: float
    p50: float
    p95: float
    price_p50_pretax: float
    price_p95_floor_pretax: float
    list_price_incl_gst: int
    margin_at_list_p50: float
    margin_at_list_p95: float
    status: str
    reasons: list = field(default_factory=list)
    cloud_month: dict = field(default_factory=dict)


def _tri(rng: random.Random | None, spec: dict) -> float:
    """Draw from a {low, mode, high} spec; with rng=None return the mode."""
    if "value" in spec:
        return spec["value"]
    if rng is None:
        return spec["mode"]
    return rng.triangular(spec["low"], spec["high"], spec["mode"])


class Engine:
    def __init__(self, catalog: dict, spec: dict, assumptions: dict, slice_stats: dict):
        self.cat = catalog["items"]
        self.spec = spec
        self.a = assumptions
        self.sl = slice_stats

    # one cost draw; rng=None gives the deterministic "mode" estimate
    def draw(self, variant: str, sc: Scenario, rng: random.Random | None) -> dict:
        v = self.spec["variants"][variant]
        a = self.a
        frac = a["parts_mode_by_qty"]["value"][sc.qty_key]

        parts = 0.0
        for m in v["modules"]:
            it = self.cat[m["catalog_id"]]
            lo, hi = it["low"], it["high"]
            mode = lo + frac * (hi - lo)
            unit = mode if rng is None else rng.triangular(lo, hi, mode)
            parts += unit * m["qty"]

        p = a["print"]
        grams = sum(part["filament_g"] for part in self.sl["parts"].values())
        hours = sum(part["print_h"] for part in self.sl["parts"].values())
        rate_g, rate_h = _tri(rng, p["material_rate_per_g"]), _tri(rng, p["machine_rate_per_h"])
        base_print = grams * rate_g + hours * rate_h
        fail = _tri(rng, p["print_fail_prob"])
        printed = print_cost(grams, hours, rate_g, rate_h,
                             _tri(rng, p["setup_support_removal"]), _tri(rng, p["finish_colour_allowance"]),
                             fail * base_print)

        asm = a["assembly"]
        minutes = sum(asm["minutes_per_op"]["value"][op] * n for op, n in v["assembly_ops"].items())
        minutes *= _tri(rng, asm["overrun_factor"])
        if sc.first_article:
            minutes *= asm["first_article_factor_qty1"]["value"]
        rate = _tri(rng, asm["loaded_labour_rate_per_h"])
        rework = _tri(rng, asm["rework_prob"]) * _tri(rng, asm["rework_minutes"])
        labour = (minutes + rework) / 60.0 * rate
        nre = asm["new_design_setup_nre"]["value"] / sc.nre_units if sc.nre_units else 0.0

        lg = a["logistics"]
        bw = billable_weight(_tri(rng, lg["packed_weight_kg"]), lg["box_cm"]["value"], lg["volumetric_divisor"]["value"])
        ship = courier_cost(bw, _tri(rng, lg["courier_rate_per_0p5kg_slab"]), _tri(rng, lg["courier_fixed"]))

        r = a["risk"]
        build = parts + printed + labour
        warranty = _tri(rng, r["warranty_fail_prob"]) * (build + 2 * ship)
        ret = _tri(rng, r[sc.returns])
        return_loss = _tri(rng, r["return_handling"]) + (1 - _tri(rng, r["return_salvage_fraction_of_parts"])) * parts + printed

        landed = build + nre + ship + warranty
        # spread the cost of returned orders over the orders that are kept
        effective = (landed + ret * return_loss) / (1 - ret)
        return {"parts": parts, "print": printed, "assembly_test": labour, "setup_nre": nre, "courier": ship,
                "warranty_reserve": warranty, "returns": effective - landed, "total_excl_fee": effective,
                "filament_g": grams, "print_h": hours, "assembly_min": minutes + rework, "billable_kg": bw}

    def quote(self, variant: str, sc: Scenario, n: int = 20000, seed: int = 7) -> Quote:
        a = self.a
        m, f, g = a["target_gross_margin"]["value"], a["payment_fee_rate"]["value"] * 1.18, a["gst_rate"]["value"]
        floor = a["binding_floor_margin_at_p95"]["value"]
        rng = random.Random(seed)
        draws = [self.draw(variant, sc, rng) for _ in range(n)]
        sims = sorted(d["total_excl_fee"] for d in draws)
        mean = {k: round(sum(d[k] for d in draws) / n, 1) for k in draws[0]}
        pct = lambda q: sims[min(n - 1, int(q * n))]
        p5, p50, p95 = pct(0.05), pct(0.50), pct(0.95)

        price50 = price_with_fee(p50, m, f, g)
        price95 = price_with_fee(p95, floor, f, g)
        listp = round_up_99(max(price50, price95) * (1 + g))
        pretax_list = listp / (1 + g)

        reasons = ["no supplier rate card or contract: every cost is a planning range or assumption (blueprint §7.1, §1.3)",
                   "delivery pincode unknown: courier cost is an estimate (blueprint §8.5)"]
        v = self.spec["variants"][variant]
        if v.get("launch_status") == "UNSUPPORTED_AT_LAUNCH":
            status = "UNSUPPORTED_AT_LAUNCH"
            reasons.insert(0, "child-directed device with a microphone and cloud AI: excluded at launch (blueprint §1.1, §4.1)")
        else:
            status = "ESTIMATE_REVIEW_REQUIRED"
            reasons.insert(0, "child-directed product: needs safety, toy-category and child-data review before any binding price (blueprint §11)")

        cloud_key = "A_live_voice" if v["cloud"] == "live_voice_conversation" else "B_story_generation"
        cloud = a["cloud_per_month"][cloud_key]
        return Quote(variant, sc.name, mean,
                     p5, p50, p95, price50, price95, listp,
                     margin_at_price(pretax_list, p50, f, g), margin_at_price(pretax_list, p95, f, g),
                     status, reasons, {"low": cloud["low"], "mode": cloud["mode"], "high": cloud["high"]})


SCENARIOS = [
    Scenario("pilot one-off today", "1", True, 1, "returns_defect_only",
             "first unit of a never-built design: retail-ish parts, first-article labour, full 3,000 setup"),
    Scenario("pilot batch of 10 variants", "10", False, 10, "returns_defect_only",
             "10 different shells on the same core: setup spread over 10"),
    Scenario("steady state, 100+/month", "100", False, 0, "returns_defect_only",
             "qualified shell family, volume parts, no per-design setup"),
    Scenario("steady state, toy-style returns", "100", False, 0, "returns_novelty",
             "as above but with BubblePal-like 20-30% novelty returns"),
]


def load_all(root: Path, slice_stats_path: Path):
    j = lambda p: json.loads(Path(p).read_text())
    return Engine(j(root / "spec/catalog.json"), j(root / "spec/bedtime-robot.json"),
                  j(root / "spec/assumptions.json"), j(slice_stats_path))
