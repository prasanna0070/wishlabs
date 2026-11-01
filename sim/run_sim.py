"""Run the bedtime-robot price simulation and write the results.

  python3 -I sim/run_sim.py            (from the project root)

Reads spec/*.json and the PrusaSlicer output in out/*.slice.txt, runs a 20,000-draw Monte Carlo per
variant x scenario, and writes out/results.json, out/bedtime-robot-quote.md and out/bedtime-robot-quote.html.
"""
from __future__ import annotations

import copy
import html
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from price_engine import SCENARIOS, Engine, load_all, margin_at_price, stl_volume_cm3  # noqa: E402

OUT = ROOT / "out"


def parse_slice(path: Path) -> dict:
    t = path.read_text()
    g = float(re.search(r"filament used \[g\] = ([\d.]+)", t).group(1))
    cm3 = float(re.search(r"filament used \[cm3\] = ([\d.]+)", t).group(1))
    m = re.search(r"estimated printing time \(normal mode\) = (?:(\d+)d )?(?:(\d+)h )?(?:(\d+)m )?(\d+)s", t)
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return {"filament_g": g, "filament_cm3": cm3, "print_h": round(d * 24 + h + mi / 60 + s / 3600, 3)}


def main() -> None:
    stats = {"slicer": "PrusaSlicer 2.7.2, 0.4 mm nozzle, 0.2 mm layers, 3 perimeters, 15% gyroid, PETG, supports from plate only",
             "parts": {p: parse_slice(OUT / f"{p}.slice.txt") for p in ("shell_print", "plate")}}
    stats["model_volume_cm3"] = {"shell": round(stl_volume_cm3(OUT / "shell.stl"), 1), "plate": round(stl_volume_cm3(OUT / "plate.stl"), 1)}
    (OUT / "slice-stats.json").write_text(json.dumps(stats, indent=1))

    eng = load_all(ROOT, OUT / "slice-stats.json")
    t0 = time.perf_counter()
    quotes = [eng.quote(v, sc) for v in ("B_parent_run", "A_as_asked") for sc in SCENARIOS]
    secs = time.perf_counter() - t0

    a = eng.a
    bureau = {k: round(sum(stats["model_volume_cm3"].values()) * a["print"]["bureau_fdm_rate_per_cm3"][k]) for k in ("low", "high")}

    # margin at the purchase-test price points (prices include GST)
    fee, gst = a["payment_fee_rate"]["value"] * 1.18, a["gst_rate"]["value"]
    price_points = []
    for q in quotes:
        if q.variant != "B_parent_run":
            continue
        row = {"scenario": q.scenario}
        for p in (5999, 7999, 9999):
            pre = p / (1 + gst)
            row[str(p)] = {"p50": round(margin_at_price(pre, q.p50, fee, gst), 3), "p95": round(margin_at_price(pre, q.p95, fee, gst), 3)}
        price_points.append(row)

    # levers: change one input at a time on the steady-state parent-run quote
    base_sc = SCENARIOS[2]
    base = eng.quote("B_parent_run", base_sc)

    def lever(name, basis, spec_fn=None, ass_fn=None, slice_fn=None):
        sp, aa, sl = copy.deepcopy(eng.spec), copy.deepcopy(eng.a), copy.deepcopy(eng.sl)
        if spec_fn: spec_fn(sp)
        if ass_fn: ass_fn(aa)
        if slice_fn: slice_fn(sl)
        q = Engine({"items": eng.cat}, sp, aa, sl).quote("B_parent_run", base_sc)
        return {"lever": name, "basis": basis, "p50": round(q.p50), "delta_p50": round(q.p50 - base.p50),
                "list_incl_gst": q.list_price_incl_gst, "delta_list": q.list_price_incl_gst - base.list_price_incl_gst}

    def drop(cid):
        return lambda sp: sp["variants"]["B_parent_run"].update(modules=[m for m in sp["variants"]["B_parent_run"]["modules"] if m["catalog_id"] != cid])

    def faster(sl):
        for p in sl["parts"].values():
            p["print_h"] *= 0.45

    levers = [
        lever("Ship without the USB-C adapter", "drops catalog row 04; parent uses any phone charger (also avoids BIS adapter registration)", spec_fn=drop("ADAPTER_USBC_A")),
        lever("Drop the microSD story library", "drops catalog row 37; stories fit in the board's own flash, fewer offline", spec_fn=drop("MICROSD_A")),
        lever("Faster printer (Bambu-class)", "ASSUMPTION: print time x0.45 vs the Prusa-default slicer estimate", slice_fn=faster),
        lever("Smaller box (20 x 15 x 17 cm)", "ASSUMPTION: tighter packaging drops billable weight", ass_fn=lambda aa: aa["logistics"]["box_cm"].update(value=[20, 15, 17])),
        lever("Toy-style returns (20-30%)", "RESEARCH: BubblePal return rate", ass_fn=lambda aa: None, spec_fn=None),
    ]
    levers[-1] = {"lever": "Toy-style returns (20-30%)", "basis": "RESEARCH: BubblePal return rate",
                  "p50": round(quotes[3].p50), "delta_p50": round(quotes[3].p50 - base.p50),
                  "list_incl_gst": quotes[3].list_price_incl_gst, "delta_list": quotes[3].list_price_incl_gst - base.list_price_incl_gst}
    levers.sort(key=lambda r: r["delta_p50"])

    top_parts = sorted(((eng.cat[m["catalog_id"]]["name"], m["catalog_id"],
                         eng.cat[m["catalog_id"]]["low"] * m["qty"], eng.cat[m["catalog_id"]]["high"] * m["qty"])
                        for m in eng.spec["variants"]["B_parent_run"]["modules"]), key=lambda r: -(r[2] + r[3]))[:6]

    res = {"prompt": eng.spec["customer_prompt"], "slice": stats, "compute_seconds_all_quotes": round(secs, 2),
           "draws_per_quote": 20000, "bureau_crosscheck_inr": bureau,
           "quotes": [asdict(q) for q in quotes], "scenarios": [asdict(s) for s in SCENARIOS],
           "price_points": price_points, "levers": levers, "top_parts": top_parts}
    (OUT / "results.json").write_text(json.dumps(res, indent=1))
    write_reports(res, eng)
    print(f"{len(quotes)} quotes, {secs:.2f}s total compute")
    for q in quotes:
        print(f"{q.variant:13s} | {q.scenario:32s} | p50 {q.p50:7.0f} p95 {q.p95:7.0f} | list incl GST {q.list_price_incl_gst:6d} | {q.status}")


def inr(x: float) -> str:
    s = f"{abs(round(x)):,}"
    # Indian grouping: 1,23,456
    n = str(abs(round(x)))
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        s = head + "," + tail
    return ("-" if x < 0 else "") + "₹" + s


ROWS = [("parts", "Electronics + hardware (catalog)"), ("print", "Printed shell + base plate"),
        ("assembly_test", "Assembly, flashing, test (labour)"), ("setup_nre", "One-time setup, spread"),
        ("courier", "Courier to customer"), ("warranty_reserve", "Warranty reserve"), ("returns", "Returns, spread over kept orders"),
        ("total_excl_fee", "Landed cost per kept order")]


def write_reports(res: dict, eng) -> None:
    qs = res["quotes"]
    by = {(q["variant"], q["scenario"]): q for q in qs}
    sl = res["slice"]["parts"]
    g = sum(p["filament_g"] for p in sl.values())
    h = sum(p["print_h"] for p in sl.values())
    scen = [s["name"] for s in res["scenarios"]]

    md = [f"# Bedtime robot: simulated build cost\n",
          f"Prompt: *{res['prompt']}*\n",
          f"Shell sliced for real: **{g:.0f} g PETG, {h:.1f} h print** ({res['slice']['slicer']}).",
          f"All {len(qs)} quotes ({res['draws_per_quote']:,} draws each) computed in {res['compute_seconds_all_quotes']} s.\n"]
    for v, label in (("B_parent_run", "B. Parent-run (recommended)"), ("A_as_asked", "A. As asked (mic + live AI)")):
        md.append(f"\n## {label}\n")
        md.append("| | " + " | ".join(scen) + " |")
        md.append("|---|" + "---:|" * len(scen))
        for k, name in ROWS:
            md.append(f"| {name} | " + " | ".join(inr(by[(v, s)]['breakdown_mean'][k]) for s in scen) + " |")
        md.append("| Simulated landed cost p5 / p50 / p95 | " + " | ".join(f"{inr(by[(v, s)]['p5'])} / {inr(by[(v, s)]['p50'])} / {inr(by[(v, s)]['p95'])}" for s in scen) + " |")
        md.append("| **Price to charge, incl. 18% GST** | " + " | ".join(f"**{inr(by[(v, s)]['list_price_incl_gst'])}**" for s in scen) + " |")
        md.append("| Gross margin at that price (p50 / p95 cost) | " + " | ".join(f"{by[(v, s)]['margin_at_list_p50']:.0%} / {by[(v, s)]['margin_at_list_p95']:.0%}" for s in scen) + " |")
        c = by[(v, scen[0])]["cloud_month"]
        md.append(f"\nCloud AI running cost (not in the price above): {inr(c['low'])} to {inr(c['high'])} a month, likely {inr(c['mode'])}.")
        md.append(f"Quote status: **{by[(v, scen[0])]['status']}**. " + " ".join(f"({i+1}) {r}." for i, r in enumerate(by[(v, scen[0])]['reasons'])))
    md.append(f"\nCross-check: an Indian print bureau at ₹3-5/cm³ (IamRapid, published) would charge about {inr(res['bureau_crosscheck_inr']['low'])}-{inr(res['bureau_crosscheck_inr']['high'])} for the two printed parts ({sum(res['slice']['model_volume_cm3'].values()):.0f} cm³ of model volume).")
    md.append("\n## Margin at the purchase-test prices (parent-run, prices incl. 18% GST)\n")
    md.append("| Scenario | ₹5,999 | ₹7,999 | ₹9,999 |\n|---|---:|---:|---:|")
    for r in res["price_points"]:
        md.append(f"| {r['scenario']} | " + " | ".join(f"{r[p]['p50']:.0%} (bad case {r[p]['p95']:.0%})" for p in ("5999", "7999", "9999")) + " |")
    md.append("\n## Biggest levers (steady state, parent-run)\n")
    md.append("| Change | Typical cost change | Price change | Basis |\n|---|---:|---:|---|")
    for l in res["levers"]:
        md.append(f"| {l['lever']} | {inr(l['delta_p50'])} | {inr(l['delta_list'])} | {l['basis']} |")
    md.append("\nCostliest parts (blueprint planning ranges): " + "; ".join(f"{n} {inr(lo)}-{inr(hi)}" for n, _, lo, hi in res["top_parts"]) + ".")
    (OUT / "bedtime-robot-quote.md").write_text("\n".join(md) + "\n")
    extra_html = ("<h2>Margin at the purchase-test prices</h2><p class='sub'>Parent-run version. Prices include 18% GST. Bad case = the 95th-percentile cost.</p>"
                  "<div class='scroll'><table><thead><tr><th>Scenario</th><th>₹5,999</th><th>₹7,999</th><th>₹9,999</th></tr></thead><tbody>"
                  + "".join(f"<tr><td>{html.escape(r['scenario'])}</td>" + "".join(
                      f"<td class='{'neg' if r[p]['p50'] < 0 else ('ok' if r[p]['p50'] >= 0.25 else '')}'>{r[p]['p50']:.0%}<br><span class='mut'>bad case {r[p]['p95']:.0%}</span></td>"
                      for p in ("5999", "7999", "9999")) + "</tr>" for r in res["price_points"])
                  + "</tbody></table></div>"
                  "<h2>Biggest levers</h2><p class='sub'>Steady-state parent-run robot, one change at a time.</p>"
                  "<div class='scroll'><table><thead><tr><th>Change</th><th>Typical cost</th><th>Price to charge</th><th>Basis</th></tr></thead><tbody>"
                  + "".join(f"<tr><td>{html.escape(l['lever'])}</td><td>{inr(l['delta_p50'])}</td><td>{inr(l['delta_list'])}</td><td style='text-align:left' class='mut'>{html.escape(l['basis'])}</td></tr>" for l in res["levers"])
                  + "</tbody></table></div>"
                  "<p>Costliest parts (blueprint planning ranges): " + "; ".join(f"{html.escape(n)} {inr(lo)}–{inr(hi)}" for n, _, lo, hi in res["top_parts"]) + ".</p>")

    # standalone HTML (no external assets except the two local PNGs)
    def table(v):
        head = "".join(f"<th>{html.escape(s)}</th>" for s in scen)
        rows = []
        for k, name in ROWS:
            cls = ' class="tot"' if k == "total_excl_fee" else ""
            rows.append(f"<tr{cls}><td>{name}</td>" + "".join(f"<td>{inr(by[(v, s)]['breakdown_mean'][k])}</td>" for s in scen) + "</tr>")
        rows.append("<tr><td>Simulated cost p5 / p50 / p95</td>" + "".join(f"<td>{inr(by[(v, s)]['p5'])}<br>{inr(by[(v, s)]['p50'])}<br>{inr(by[(v, s)]['p95'])}</td>" for s in scen) + "</tr>")
        rows.append('<tr class="price"><td>Price to charge, incl. GST</td>' + "".join(f"<td>{inr(by[(v, s)]['list_price_incl_gst'])}</td>" for s in scen) + "</tr>")
        rows.append("<tr><td>Margin at that price (p50 / p95)</td>" + "".join(f"<td>{by[(v, s)]['margin_at_list_p50']:.0%} / {by[(v, s)]['margin_at_list_p95']:.0%}</td>" for s in scen) + "</tr>")
        return f"<div class='scroll'><table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"

    def card(v, title, sub):
        q0 = by[(v, scen[0])]
        c = q0["cloud_month"]
        reasons = "".join(f"<li>{html.escape(r)}</li>" for r in q0["reasons"])
        return (f"<section><h2>{title}</h2><p class='sub'>{sub}</p>{table(v)}"
                f"<p>Cloud AI running cost, not in the price: <b>{inr(c['low'])}–{inr(c['high'])}/month</b> (likely {inr(c['mode'])}).</p>"
                f"<p class='status'>Quote status: <b>{q0['status']}</b></p><ul>{reasons}</ul></section>")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bedtime robot cost simulation</title>
<style>
:root{{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1da;--acc:#2f6f5e;--warn:#9a5b13}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--mut:#a3a29b;--line:#33322f;--acc:#7cc4ad;--warn:#e0a35a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,system-ui,Segoe UI,sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:28px 16px 60px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:34px 0 4px}} .sub,.mut{{color:var(--mut)}}
.prompt{{border-left:3px solid var(--acc);padding:6px 12px;margin:14px 0;font-style:italic}}
.imgs{{display:flex;gap:12px;flex-wrap:wrap}} .imgs img{{width:min(100%,320px);border:1px solid var(--line);border-radius:8px;background:#f7f7f7}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}}
.fact{{border:1px solid var(--line);border-radius:8px;padding:10px 12px}} .fact b{{display:block;font-size:20px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:640px;font-variant-numeric:tabular-nums}}
th,td{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:right;vertical-align:top}} th:first-child,td:first-child{{text-align:left}}
th{{font-weight:600;font-size:13px;color:var(--mut)}} tr.tot td{{font-weight:600}} tr.price td{{font-weight:700;color:var(--acc)}}
.status b{{color:var(--warn)}} td.neg{{color:#b3261e}} td.ok{{color:var(--acc);font-weight:600}} ul{{margin-top:4px}} code{{font-size:13px}}
</style></head><body><main>
<h1>Bedtime robot: what would it cost to build?</h1>
<p class="mut">Simulation, 1 November 2026. Every rupee here is a planning range or an assumption, not a supplier quote.</p>
<p class="prompt">“{html.escape(res['prompt'])}”</p>
<div class="imgs"><img src="front.png" alt="Front view of the functional robot shell"><img src="back.png" alt="Back view of the functional robot shell"></div>
<p class="mut">Functional shell used for costing (108 × 78 × 124 mm): eye window for the screen, big story button on top, chest night-light, speaker grille, USB-C at the back, screw-on base plate. The cute exterior styling comes later; the parts inside stay the same.</p>
<div class="facts">
<div class="fact"><b>{g:.0f} g</b>PETG plastic, from the real slicer</div>
<div class="fact"><b>{h:.1f} h</b>print time (Prusa-class speeds)</div>
<div class="fact"><b>{res['draws_per_quote']:,}</b>random cost draws per quote</div>
<div class="fact"><b>{res['compute_seconds_all_quotes']} s</b>to compute all {len(qs)} quotes</div>
</div>
{card('B_parent_run', 'B. Parent-run version (recommended)', 'No microphone. The parent approves each story in an app; stories live on the robot and play offline.')}
{card('A_as_asked', 'A. As asked: talks with the child', 'Microphone plus live cloud voice AI in the child’s bedroom. Excluded at launch by the blueprint.')}
{extra_html}
<h2>How to read this</h2>
<ul>
<li><b>Landed cost</b> = parts + printing + labour + setup + courier + warranty reserve + returns, per order the customer keeps (blueprint §8.2).</li>
<li><b>Price to charge</b> is the higher of: 30% gross margin at the typical (p50) cost, or 10% margin even at the bad-case (p95) cost, after the payment-gateway fee, rounded up to ₹…99 and shown with 18% GST.</li>
<li>Cross-check: an Indian print bureau at ₹3–5/cm³ (IamRapid, published rates) would charge about {inr(res['bureau_crosscheck_inr']['low'])}–{inr(res['bureau_crosscheck_inr']['high'])} for the two printed parts.</li>
<li>Inputs: <code>sim/spec/catalog.json</code> (blueprint §3.3 ranges), <code>sim/spec/assumptions.json</code> (every guess labelled), <code>sim/out/slice-stats.json</code> (slicer output).</li>
</ul>
</main></body></html>"""
    (OUT / "bedtime-robot-quote.html").write_text(page)


if __name__ == "__main__":
    main()
