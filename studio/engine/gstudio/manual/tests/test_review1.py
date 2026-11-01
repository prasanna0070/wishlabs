"""REVIEW-1 manual items, tested on the JSON outputs of the benchmark build (fixtures/v3lite: the Sleepy Bear from
_out/e2e/v3, with its real speaker-ring/LED collision) and on small synthetic cases. No CAD kernel or renderer needed,
except where marked."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

FIX = Path(__file__).resolve().parent / "fixtures" / "v3lite"


# --------------------------------------------------------------------------------------------- helpers
def v3_ctx(tmp_path: Path, mutate=None):
    out = tmp_path / "v3"
    shutil.copytree(FIX, out)
    if mutate:
        mutate(out)
    return SimpleNamespace(spec=json.loads((out / "spec.json").read_text()), lib=None, out=out,
                           electrical=json.loads((out / "electrical.json").read_text()), artifacts={}, options={},
                           progress=lambda *a, **k: None, warn=lambda *a, **k: None)


def prepared(ctx):
    from gstudio.manual.data import load
    from gstudio.manual.model import classify
    from gstudio.manual.steps import plan
    from gstudio.manual.issues import load_issues

    data = load(ctx)
    sb = json.loads((Path(ctx.out) / "cad" / "solid-boxes.json").read_text())
    data.solid_boxes.update({k: [tuple(b) for b in v] for k, v in sb["solid_boxes"].items()})
    data.rings = sb["rings"]
    A = classify(data)
    issues = load_issues(data)
    return data, A, issues, plan(A, issues)


def step(steps, sid):
    return next(s for s in steps if s.id == sid)


def no_failures(out: Path):
    """Turn the benchmark's failed CAD checks into passes (what the guide should look like after BLK-1)."""
    for rel in ("checks/digital-checks.json", "cad/digital-checks.json"):
        d = json.loads((out / rel).read_text())
        for c in d["checks"]:
            if c.get("status") == "failed":
                c["status"] = "passed"
                if isinstance(c.get("data"), dict):
                    c["data"].pop("intersections", None)
                    c["data"]["pairs"] = [p for p in c["data"].get("pairs") or [] if (p.get("gap_mm") or 0) >= 0.5]
        (out / rel).write_text(json.dumps(d))


# --------------------------------------------------------------------------------------------- BLK-6
def test_every_ref_resolves_to_its_own_module(tmp_path):
    data, A, _, _ = prepared(v3_ctx(tmp_path))
    for inst in data.instances:
        c = data.comp_for_ref(inst["ref"])
        if c is not None:
            assert c.module_id == inst.get("module"), (inst["ref"], c.name, c.module_id)
    assert data.comp_for_ref("MIC1").name == "PURCHASE_Microphone"
    assert data.comp_for_ref("SPK1").name == "PURCHASE_Speaker"


def test_wiring_parts_rows_equal_their_instance(tmp_path):
    from gstudio.manual.wiring import wiring_parts

    data, A, _, _ = prepared(v3_ctx(tmp_path))
    rows = {r["id"]: r for r in wiring_parts(data, A)}
    for inst in data.instances:
        r = rows.get(inst["ref"])
        if not r:
            continue
        assert r["module_id"] == inst.get("module")
        assert "gasket" not in r["name"].lower(), r
        c = data.comp_for_ref(inst["ref"])
        if c is not None and "sizeMm" in r:
            assert r["sizeMm"] == [round(v, 1) for v in c.size]
    assert rows["MIC1"]["sizeMm"] == [14.0, 9.5, 14.0]


def test_module_steps_list_their_purchase_and_titles_do_not_repeat(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    titles = [s.title for s in steps]
    assert len(titles) == len(set(titles)), titles
    for g in [*A.features, *A.internals]:
        st = step(steps, f"mount-{g.key}")
        assert g.module.name in st.parts, (st.id, st.parts)
    mic = step(steps, "mount-mic")
    assert {"PURCHASE_Microphone", "PURCHASE_MicGasket", "PRINT_MicClip"} <= set(mic.parts)
    assert not any(s.id.endswith("-2") for s in steps)
    body = mic.body
    # one step per module, in this order: cut the ring, stick it, seat the mic, fit the clip, then the screws
    i_cut, i_stick = body.index("Cut a foam ring"), body.index("stick it to the inside wall")
    i_seat, i_clip, i_screw = body.index("Seat the **INMP441"), body.index("`PRINT_MicClip`"), body.index("Fix it with")
    assert i_cut < i_stick < i_seat < i_clip < i_screw
    assert "Speaker gasket" not in mic.title and "Speaker gasket" not in data.module_name("MIC1")


# --------------------------------------------------------------------------------------------- MAJ-15
def test_retention_verb_order_and_no_face_sentence_for_accessories(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    for s in steps:
        if "Peel the backing and press the **" in s.body:
            assert "Fix it with" not in s.body and "clip" not in s.body.lower(), s.id
        assert "as shown in the image" not in s.body, s.id
    led = step(steps, "mount-status-light")
    assert led.body.index("PRINT_LedDiffuser") < led.body.index("Seat the **Addressable RGB LED")
    spk = step(steps, "mount-speaker")
    # the gasket ring gets cut/stick lines only: no 'cone' or 'cable connector' sentence names it
    for ln in spk.body.splitlines():
        if "foam ring" in ln:
            assert "cone toward" not in ln and "connector" not in ln


def test_retention_kind_rules():
    from gstudio.manual.data import Comp
    from gstudio.manual.model import Group
    from gstudio.manual.steps import retention_kind

    mod = Comp("PURCHASE_Led", "PURCHASE", "led", ref="LED1", module_id="X")
    clip = Comp("PRINT_LedClip", "PRINT", "printed clamp strap")
    scr = Comp("PURCHASE_ModuleScrews#1,2", "PURCHASE", "screws")
    assert retention_kind("adhesive", Group(key="l", role="led", module=mod, prints=[clip], fasteners=[scr])) == "clamp_screws"
    assert retention_kind("adhesive", Group(key="l", role="led", module=mod, prints=[clip])) == "clamp"
    assert retention_kind("adhesive", Group(key="l", role="led", module=mod)) == "adhesive"
    assert retention_kind("snap_out", Group(key="b", role="button", module=mod, prints=[clip])) == "snap_out"


# --------------------------------------------------------------------------------------------- MAJ-11
def test_fastener_table_equals_cad_records(tmp_path):
    from gstudio.manual.guide import fastener_rows, write_guide

    data, A, issues, steps = prepared(v3_ctx(tmp_path))
    rows = fastener_rows(A)
    assert sum(r[3] for r in rows) == 17
    assert {r[2] for r in rows} == {"5 mm", "8 mm", "12 mm"}
    per = {r[0].split("(")[-1].split(")")[0]: r[3] for r in rows if "(" in r[0]}
    assert per == {"SPK1": 3, "DISP1": 2, "LED1": 2, "MIC1": 2}
    recs = json.loads((Path(data.out) / "cad" / "digital-checks.json").read_text())
    eng = next(c for c in recs["checks"] if c["id"] == "fastener_engagement")["data"]["screws"]
    assert sum(r[3] for r in rows) == len(eng)
    md = write_guide(data, A, steps, {}, [], {"steps": []}, issues)
    assert "normalizer" not in md.lower() and "hex nut" not in md.lower()
    # the speaker step uses its 3 screws, as the CAD records say
    assert "3 × **M2.5 × 8 mm socket screws" in step(steps, "mount-speaker").body


def test_gasket_cut_sizes_from_cad(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    assert "Ø39.4 mm outside, Ø36 mm inside" in step(steps, "mount-speaker").body
    assert "Ø7.6 mm outside, Ø3.2 mm inside" in step(steps, "mount-mic").body


# --------------------------------------------------------------------------------------------- MAJ-13 / MIN-15
def test_bench_test_before_shell_and_card_before_bench(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    ids = [s.id for s in steps]
    assert ids.index("prepare-card") < ids.index("bench-test")
    for later in ["print-parts", "install-inserts", "mount-cartridge"] + [i for i in ids if i.startswith("mount-")]:
        assert ids.index("bench-test") < ids.index(later), later
    bt = step(steps, "bench-test")
    assert 'GS-SELFTEST {"ok":true,' in bt.body and '"failed":0' in bt.body
    assert "sha256sum -c SHA256SUMS" in bt.body and "firmware-merged.bin" in bt.body and "UART" in bt.body
    assert "`selftest`" in bt.body
    assert "audio" in step(steps, "prepare-card").body


def test_tests_have_bench_record(tmp_path):
    from gstudio.manual.finals import build_tests

    data, A, _, _ = prepared(v3_ctx(tmp_path))
    titles = [t["title"] for t in build_tests(data, A)]
    assert "Bench self-test on the table" in titles
    assert all(t["status"] == "not_run" for t in build_tests(data, A) if t["kind"] == "physical")


def test_flash_recheck_and_close_details(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    fl = step(steps, "flash-and-test")
    lines = [ln for ln in fl.body.splitlines() if ln.startswith("- ")]
    assert len(lines) == len(set(lines)) and lines
    assert "Micro-USB" in fl.stop_text
    close = step(steps, "close-cover")
    assert "USB-C opening" in close.body and "x ±53.7 mm" in close.body and "±57.6 mm" in close.body


# --------------------------------------------------------------------------------------------- MAJ-14
def test_route_step_names_excess_and_ties(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    rt = step(steps, "route-cables")
    for r in data.routes:
        assert f"{float(r.length_mm):.0f} mm" in rt.body
    assert "Z-fold" in rt.body and "Flush cutters for the tie tails" in rt.tools
    assert "STRAIN_TIE_NYLON66_100X2_5" in rt.rows
    from gstudio.manual.stage import json_steps

    js = {x["id"]: x for x in json_steps(steps, {})}
    assert "STRAIN_TIE_NYLON66_100X2_5" in js["route-cables"]["purchased"]
    for x in js.values():  # steps[].parts stays a list of CAD component names
        assert all(p in data.comps or p.startswith("TEST_") for p in x["parts"]), (x["id"], x["parts"])


def test_every_purchased_row_used_in_a_step(tmp_path):
    from gstudio.manual.stage import json_steps
    from gstudio.manual.summary import purchased_unused

    data, A, _, steps = prepared(v3_ctx(tmp_path))
    assert purchased_unused({"purchased": data.purchased(), "steps": json_steps(steps, {})}) == []
    extra = data.purchased() + [{"name": "Mystery widget", "quantity": 1, "module_id": "MYSTERY_1", "kind": "hardware"}]
    assert purchased_unused({"purchased": extra, "steps": json_steps(steps, {})}) == ["Mystery widget"]


# --------------------------------------------------------------------------------------------- BLK-7 / MAJ-19
def test_known_issues_and_banner_from_failed_checks(tmp_path):
    from gstudio.manual.guide import write_guide

    data, A, issues, steps = prepared(v3_ctx(tmp_path))
    assert issues.banner and issues.pairs
    md = write_guide(data, A, steps, {}, data.build_existing["tests"], {"steps": []}, issues)
    assert "Do not print this design yet" in md
    led = step(steps, "mount-status-light")
    assert any("PRINT_SpeakerRing" in k for k in led.known) and "will not seat" in led.stop_text
    spk = step(steps, "mount-speaker")
    assert any("PURCHASE_Led" in k for k in spk.known)
    assert not step(steps, "check-parts").known and not step(steps, "prepare-card").known  # the banner covers those
    assert not step(steps, "mount-eyes").known  # shared screw groups do not drag unrelated steps in
    for s in steps:
        for k in s.known:
            assert "overlapping" in k or "touching" in k or "only" in k


def test_no_banner_when_checks_pass(tmp_path):
    from gstudio.manual.guide import write_guide

    data, A, issues, steps = prepared(v3_ctx(tmp_path, mutate=no_failures))
    md = write_guide(data, A, steps, {}, [], {"steps": []}, issues)
    assert not issues.banner and "Do not print" not in md and "Known design issue" not in md
    assert not any(s.known for s in steps)


def test_final_checks_list_every_test_failed_first():
    from gstudio.manual.guide import final_section, guide_final_titles

    tests = [{"title": "A", "expected": "a", "kind": "physical", "status": "not_run"},
             {"title": "B", "expected": "b", "kind": "digital", "status": "passed"},
             {"title": "C", "expected": "c", "kind": "digital", "status": "failed", "detail": "15/18 checks passed"}]
    md = "\n".join(final_section(tests))
    t = guide_final_titles(md)
    assert sorted(t) == ["A", "B", "C"] and t.index("C") < t.index("B")
    assert "Status: FAILED" in md and "15/18" in md


def test_finalize_rerenders_from_checks(tmp_path):
    """finalize(ctx) after the checks stage: final checks = build.json tests[], banner from checks, deterministic."""
    from gstudio.manual import finalize
    from gstudio.manual.guide import guide_final_titles

    ctx = v3_ctx(tmp_path)
    out = Path(ctx.out)
    sb = json.loads((out / "cad" / "solid-boxes.json").read_text())
    (out / "manual").mkdir(exist_ok=True)
    s1 = finalize(ctx)
    md = (out / "BUILD_GUIDE.md").read_text()
    b = json.loads((out / "build.json").read_text())
    assert sorted(guide_final_titles(md)) == sorted(t["title"] for t in b["tests"])
    assert s1["tests"] == len(b["tests"]) and s1["banner"]
    assert "Do not print this design yet" in md
    frag = json.loads((out / ".gstudio" / "fragments" / "manual.json").read_text())
    assert frag["steps"] == b["steps"]
    before = (md, (out / "manual" / "manual-summary.json").read_text())
    finalize(ctx)
    assert before == ((out / "BUILD_GUIDE.md").read_text(), (out / "manual" / "manual-summary.json").read_text())
    assert sb  # fixture present


# --------------------------------------------------------------------------------------------- MIN-13
def test_print_notes_and_settings(tmp_path):
    from gstudio.manual.steps import print_notes, print_settings

    data, A, _, steps = prepared(v3_ctx(tmp_path))
    rc = next(p for p in data.printed() if p["name"] == "PRINT_RearCover")
    n = "; ".join(print_notes(rc))
    assert "slicer adds supports" in n and "no supports" not in n.replace("no supports (slicer)", "")
    dif = next(p for p in data.printed() if p["name"] == "PRINT_LedDiffuser")
    assert "<1 g" in "; ".join(print_notes(dif)) and "about 0 g" not in "; ".join(print_notes(dif))
    ps = print_settings(data)
    for token in ("0.2 mm layers", "3 walls", "15% gyroid", "PETG", "240 °C", "80 °C"):
        assert token in ps, token


# --------------------------------------------------------------------------------------------- MIN-14
def test_coupon_table_and_insert_groups(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    fc = step(steps, "fit-coupon")
    assert "Soldering iron with a heat-set insert tip" in fc.tools
    assert "30.3 mm" in fc.body and "2 × **M2.5 × 4 mm heat-set inserts**" in fc.body
    assert "| `TEST_SeamMate` |" in fc.body and "| `TEST_SeamShell` |" in fc.body
    ins = step(steps, "install-inserts")
    assert "PRINT_RearCover" not in ins.parts
    groups = {(m.group(1), int(m.group(2))) for m in re.finditer(r"\| (\w[\w ]+ bosses) \| (\d+) \|", ins.body)}
    assert sum(n for _, n in groups) == 17
    assert groups == {("Floor bosses", 4), ("Front wall bosses", 9), ("Rear seam bosses", 4)}, groups
    assert "-Z (down)" in ins.body and "-Y (front)" in ins.body
    assert "square-on" in ins.stop_text


# --------------------------------------------------------------------------------------------- MIN-31 / 32 / 33 / 34 / 35
def test_labels_wrap_without_ellipsis():
    from gstudio.manual.text import wrap

    lines = wrap("Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) and more words here", 30, 3)
    assert len(lines) <= 3 and not any("…" in x or "..." in x for x in lines)
    assert " ".join(lines).split() == "Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) and more words here".split()


def test_figure_labels_are_human_and_rear_views_say_so(tmp_path):
    from gstudio.manual.geom import REAR_NOTE, rear_view

    data, A, _, steps = prepared(v3_ctx(tmp_path))
    for s in steps:
        for f in s.figures:
            for p in f.panels:
                if rear_view(p.view_dir):
                    assert REAR_NOTE in p.subtitle, (f.file, p.subtitle)
                for lb in p.labels:
                    assert not re.search(r"\bPRINT_|\bPURCHASE_", lb.text), (f.file, lb.text)
                    assert "…" not in lb.text
                    assert not re.search(r"(?<![\w(])[+-][xyz]\b", lb.text), lb.text


def test_tools_list_from_structured_tools(tmp_path):
    from gstudio.manual.guide import write_guide

    data, A, issues, steps = prepared(v3_ctx(tmp_path))
    md = write_guide(data, A, steps, {}, [], {"steps": []}, issues)
    sec = md.split("Tools (not part of the kit", 1)[1].split("\n\n", 2)[1]
    items = [ln[2:] for ln in sec.splitlines() if ln.startswith("- ")]
    all_tools = {t.lower() for s in steps for t in s.tools}
    assert items and all(i.lower() in all_tools for i in items), items
    assert not any(i in ("Dry", "Non-metal surface", "Md`") or i.startswith("See `") for i in items)


def test_guide_has_no_pipeline_words(tmp_path):
    from gstudio.manual.guide import write_guide
    from gstudio.manual.wiring import build_wiring

    data, A, issues, steps = prepared(v3_ctx(tmp_path))
    md = write_guide(data, A, steps, {}, data.build_existing["tests"], build_wiring(data, A, {}), issues)
    for w in ("normalizer", "blueprint", "catalog", "slot map", "backplane-v1"):
        assert w not in md.lower(), w


def test_parts_figure_draws_every_row(tmp_path):
    data, A, _, steps = prepared(v3_ctx(tmp_path))
    f = step(steps, "check-parts").figures[0]
    p = f.panels[0]
    drawn = set(p.show) | {m.name for m in p.meshes}
    rows = data.purchased()
    for r in rows:
        assert r.get("modelPart") in drawn or any(str(r.get("module_id") or r.get("name")) in m for m in drawn), r["name"]
    strip = next(lb for lb in p.labels if "gasket strip" in lb.text)
    assert "2 rings" in strip.text and "Ø39" in strip.text and "Ø8" in strip.text and "{" not in strip.text
    assert "PURCHASE_SpeakerGasket" not in p.show and "PURCHASE_MicGasket" not in p.show


# --------------------------------------------------------------------------------------------- MAJ-10
def foundation_harness(out: Path):
    """The harness fields the electrical stage now writes (REVIEW-1 BLK-3/MAJ-4), for the benchmark's modules."""
    e = json.loads((out / "electrical.json").read_text())
    for i in e["instances"]:
        h = i.get("harness")
        if not h:
            continue
        col = {w["module_pin"]: (w["color"], w["slot_pin"]) for w in h["wires"]}

        def pos(k, mp):
            return {"pos": k, "module_pin": mp, "port_pin": col[mp][1], "wire_colour": col[mp][0]}

        if i["ref"] == "DISP1":
            h.update(module_end_family="dupont_2.54", made_to_drawing=True, crossed=True,
                     module_end_housings=[{"housing": "Dupont 1x4 female housing (2.54 mm)",
                                           "positions": [pos(1, "VCC"), pos(2, "GND"), pos(3, "SCL"), pos(4, "SDA")]}])
        elif i["ref"] == "MIC1":
            h.update(module_end_family="dupont_2.54", made_to_drawing=True,
                     module_end_housings=[{"housing": "Dupont 1x3 female housing (2.54 mm)", "positions": [pos(1, "L/R"), pos(2, "WS"), pos(3, "SCK")]},
                                          {"housing": "Dupont 1x3 female housing (2.54 mm)", "positions": [pos(1, "SD"), pos(2, "VDD"), pos(3, "GND")]}])
        elif i["ref"] == "BTN1":
            h.update(module_end_family="spade_2.8", module_end_housings=[
                {"housing": "2.8 mm (0.110 in) female insulated spade", "positions": [pos(1, "COM")]},
                {"housing": "2.8 mm (0.110 in) female insulated spade", "positions": [pos(1, "NO")]}])
        else:
            h.update(module_end_family="factory_prewired", factory_prewired=True)
    (out / "electrical.json").write_text(json.dumps(e))


def test_module_end_instructions(tmp_path):
    from gstudio.manual.cables import module_ends

    ctx = v3_ctx(tmp_path, mutate=foundation_harness)
    data, A, _, steps = prepared(ctx)
    ends = module_ends(data)
    assert ends["DISP1"].family == "dupont_2.54" and ends["DISP1"].pin1().module_pin == "VCC" and ends["DISP1"].made_to_drawing
    assert ends["BTN1"].family == "spade_2.8" and ends["SPK1"].family == "factory_prewired"
    assert len(ends["MIC1"].housings) == 2
    disp = step(steps, "mount-eyes")
    assert "Dupont 2.54 mm" in disp.body and "position 1 → **VCC**" in disp.body
    assert "no header pins fitted" in disp.stop_text and "made to drawing" in disp.stop_text
    assert "2.8 mm female spade" in step(steps, "mount-story-button").body
    assert "pre-wired from our factory" in step(steps, "mount-speaker").body
    for g in [*A.features, *A.internals]:
        assert g.ref in step(steps, f"mount-{g.key}").module_end, g.key
    from gstudio.manual.stage import module_end_missing

    assert module_end_missing(data, steps) == []


def test_stale_cable_reports_mismatch(tmp_path):
    from gstudio.manual.cables import module_ends

    data, A, _, steps = prepared(v3_ctx(tmp_path))  # benchmark data: PH-to-PH leads on header modules
    ends = module_ends(data)
    assert ends["DISP1"].mismatch and "Dupont" in ends["DISP1"].mismatch
    assert "do not mate" in step(steps, "mount-eyes").body


def test_lookalike_port_warning(tmp_path):
    from gstudio.manual.cables import lookalike_ports
    from gstudio.manual.model import classify
    from gstudio.manual.steps import plan

    data, A, issues, steps = prepared(v3_ctx(tmp_path))
    io1, led = data.backplane.slots["J_IO1"], data.backplane.slots["J_LED"]
    same = ("xh" in str(io1.footprint).lower()) == ("xh" in str(led.footprint).lower()) and len(io1.pins) == len(led.pins)
    plug = step(steps, "plug-harnesses")
    if same:  # a PH J_LED (before MAJ-3): the two 3-pin leads fit each other's port
        assert "J_IO1 and J_LED have the same connector and pin count" in plug.body
    else:  # after MAJ-3 the LED port is a different family: no warning
        assert "J_IO1 and J_LED" not in plug.body and "each lead fits only its own port" in plug.body
    led.footprint = io1.footprint  # force the pre-MAJ-3 board
    assert lookalike_ports(data, ["J_IO1", "J_LED", "J_I2C1"]) == [["J_IO1", "J_LED"]]
    plug2 = step(plan(classify(data), issues), "plug-harnesses")
    assert "J_IO1 and J_LED have the same connector and pin count" in plug2.body
    assert "look-alike port" in plug2.stop_text


def test_gpio_via_buffer(tmp_path):
    from gstudio.manual.wiring import build_wiring

    data, A, _, _ = prepared(v3_ctx(tmp_path))
    w = build_wiring(data, A, {})
    din = next(c for c in w["connections"] if c["from"] == "LED1.DIN")
    assert din["gpio"] == 2 and "GPIO2 via U3" in din["note"]
    vias = {c["via"] for c in w["connections"] if c["kind"] == "cable"}
    names = {p["name"] for p in data.purchased()}
    assert vias <= names, vias - names
    assert len([c for c in w["connections"] if c["to"].startswith("BP1.U1.")]) == 44


# --------------------------------------------------------------------------------------------- MIN-30 (no rendering)
def test_overlap_checker_detects_crossing():
    from gstudio.manual.diagram import overlaps
    from gstudio.manual.draw import Canvas

    cv = Canvas(400, 200)
    cv.text(50, 100, "J_IO1", size=14)
    cv.polyline([(40, 95), (120, 95)], stroke=(0, 0, 0))
    cv.text(55, 102, "J_LED", size=14)
    o = overlaps(cv)
    assert any("crosses" in x for x in o) and any("overlaps text" in x for x in o)
    cv2 = Canvas(400, 200)
    cv2.text(50, 100, "J_IO1", size=14)
    cv2.polyline([(40, 140), (120, 140)], stroke=(0, 0, 0))
    assert overlaps(cv2) == []


def test_figures_json_round_trip():
    from gstudio.manual.figs import FigureSpec, LabelSpec, MeshSpec, PanelSpec, figure_from_json, figure_to_json

    f = FigureSpec(file="manual/x.png", title="t", footer="f", alt="a", caption="c",
                   panels=[PanelSpec(subtitle="s", show={"A": "focus"}, offsets={"A": (1.0, 2.0, 3.0)}, view_dir=(0.1, -1.0, 0.4),
                                     labels=[LabelSpec(text="L", anchor="BP1.J_IO1", priority=2),
                                             LabelSpec(text="R", ray=(["S", "A"], (0, -400, 10), (0, 1, 0), "A"))],
                                     meshes=[MeshSpec(path="glyph:coil:X", color=(0.5, 0.5, 0.5), name="glyph:coil:X")],
                                     arrow_comps=["A"])])
    g = figure_from_json(json.loads(json.dumps(figure_to_json(f))))
    assert figure_to_json(g) == figure_to_json(f)
