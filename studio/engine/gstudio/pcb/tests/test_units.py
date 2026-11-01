"""Fast unit tests for gstudio.pcb that need no KiCad install (sexpr, transforms, design model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gstudio.pcb import sexpr
from gstudio.pcb.design import POWER_NETS, SUPPORT, build_design, load_backplane, resolve_node
from gstudio.pcb.symbols import transform

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
STUDIO = HERE.parents[3]
BACKPLANE = STUDIO / "library" / "backplane-v1.json"
META = {"name": "Unit", "slug": "unit", "revision": "A", "date": "2026-11-01", "engine_version": "test"}


def _el(name: str) -> dict:
    return json.loads((FIX / f"electrical.{name}.json").read_text())


def test_sexpr_roundtrip_keeps_quotes_and_numbers():
    src = '(kicad_sch (version 20250114) (title_block (title "A \\"q\\" b")) (at 1.27 -2.54 90) (x yes))'
    node = sexpr.parse(src)
    assert node[0] == "kicad_sch"
    title = sexpr.find(sexpr.find(node, "title_block"), "title")
    assert isinstance(title[1], sexpr.Q) and str(title[1]) == 'A "q" b'
    again = sexpr.parse(sexpr.dumps(node))
    assert again == node
    assert sexpr.num(1.27000001) == "1.27" and sexpr.num(-0.0) == "0"


@pytest.mark.parametrize("rot,expected", [(0, (0.0, -3.81)), (90, (-3.81, 0.0)), (180, (0.0, 3.81)),
                                          (270, (3.81, 0.0))])
def test_pin_transform_matches_kicad_rotation(rot, expected):
    # Device:R pin 1 sits at (0, 3.81) in library coordinates (Y up)
    x, y = transform(0.0, 3.81, rot)
    assert (round(x, 4) + 0.0, round(y, 4) + 0.0) == expected


def test_full_design_uses_backplane_positions_and_fixed_support_refs():
    bp = load_backplane(BACKPLANE)
    d = build_design(bp, _el("full"), META)
    refs = {p.ref for p in d.parts}
    # every physical slot part of every slot is placed at its backplane position
    for sid, s in bp["slots"].items():
        for r in s.get("parts") or [sid]:
            assert r in refs, r
            assert d.part(r).at == tuple(float(v) for v in bp["parts"][r]["at"])
    # support parts keep backplane-v1 refs and positions
    for r in ("R1", "R2", "F1", "D1", "C1", "C2", "U2"):
        assert r in refs and d.part(r).at == tuple(float(v) for v in bp["parts"][r]["at"])
    assert not d.omitted_slots


def test_minimal_design_omits_unused_slots_and_their_support():
    bp = load_backplane(BACKPLANE)
    d = build_design(bp, _el("minimal"), META)
    refs = {p.ref for p in d.parts}
    assert "J_A1" not in refs and "J_S1" not in refs and "J_MIC" not in refs
    assert "R7" not in refs and "C3" not in refs           # amp support only with A1
    assert {"U3", "R3", "C5", "R4", "R5"} <= refs          # LED buffer + I2C pull-ups are needed
    assert set(d.omitted_slots) == set(bp["slots"]) - set(_el("minimal")["slots_used"])
    # GPIOs whose slot is absent are left unconnected, not routed to nowhere
    u1a = d.part("J_U1A")
    assert u1a.pads["7"] == ""  # J1-7 = I2S_SPK_DIN, amp not fitted


def test_every_design_net_has_two_or_more_pads_except_rails():
    bp = load_backplane(BACKPLANE)
    for name in ("full", "minimal"):
        d = build_design(bp, _el(name), META)
        for net, nodes in d.nets().items():
            assert len(nodes) >= 2 or net in POWER_NETS, (name, net, nodes)


def test_resolve_node_accepts_slot_pin_names():
    bp = load_backplane(BACKPLANE)
    assert resolve_node("U1.J1-12", bp) == ("J_U1A", "12")
    assert resolve_node("U1.J3-19", bp) == ("J_U1B", "19")
    assert resolve_node("BP1.J_I2C1.3", bp) == ("J_I2C1", "3")
    assert resolve_node("A1.VO+", bp) == ("J_A1P", "1")
    assert resolve_node("DISP1.SDA", bp) is None  # module side, off the board


def test_support_table_only_names_parts_the_backplane_defines_when_needed():
    bp = load_backplane(BACKPLANE)
    for ref, sp in SUPPORT.items():
        if sp["when"] == "always" or set(sp["when"]) & set(bp["slots"]):
            assert ref in bp["parts"], f"{ref} missing from backplane-v1 parts"


# ============================================================================ REVIEW-1 (MAJ-3, MAJ-16, MAJ-22, MIN-1, MIN-36)

import copy  # noqa: E402

from gstudio.pcb import reuse, rules, silk_audit, sourcing  # noqa: E402
from gstudio.pcb.design import design_id, silk_name  # noqa: E402

OLD_BACKPLANE = FIX / "backplane-v1.pre-review1.json"
XH = "Connector_JST:JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical"


def xh_backplane(raw: dict, y: float = 3.8) -> dict:
    """backplane-v1 with J_LED on a JST-XH B3B-XH-A header (REVIEW-1 MAJ-3, foundation's change), pin 1 at board
    (41.0, y). y = 3.8 (the PH position) routes the full board with 0 DRC errors; y = 4.4, which would keep the XH
    courtyard (3.9 mm in front of pin 1) inside the edge, left 12-13 connections unrouted on 2026-11-01 because the
    deeper XH body narrows the channel under A1/S1. At 3.8 the courtyard reaches 0.1 mm past the front edge and the
    body front sits 0.4 mm inside it. No-op when backplane-v1 already has an XH J_LED."""
    bp = copy.deepcopy(raw)
    H = float(bp["board"]["size"][1])
    for s in bp["slots"]:
        if s["id"] == "J_LED" and "XH" not in s["footprint"]:
            s["footprint"], s["connector"] = XH, "JST-XH 2.5"
            s["at"], s["kicad"] = [41.0, y], {"x": 41.0, "y": round(H - y, 3), "rot": 0}
            for i, p in enumerate(s["pins"]):
                p["at"] = [41.0 + 2.5 * i, y]
    for p in bp["parts"]:
        if p["ref"] == "J_LED" and "XH" not in p["footprint"]:
            p["footprint"], p["at"] = XH, [41.0, y]
            p["kicad"] = {"x": 41.0, "y": round(H - y, 3), "rot": 0}
            p["courtyard"] = [38.05, 48.95, round(y - 3.9, 2), round(y + 2.85, 2)]
    return bp


def test_connector_family_reads_the_footprint():
    assert rules.connector_family("Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical") == ("JST-PH", 3)
    assert rules.connector_family(XH) == ("JST-XH", 3)
    assert rules.connector_family("Connector_PinSocket_2.54mm:PinSocket_1x07_P2.54mm_Vertical") is None


def test_port_keying_rule_fails_on_old_backplane_and_passes_with_xh_led():
    old = json.loads(OLD_BACKPLANE.read_text())
    bad = rules.port_keying_conflicts(old)
    assert len(bad) == 1 and bad[0]["family"] == "JST-PH" and bad[0]["pins"] == 3
    assert bad[0]["ports"]["J_LED"] == "+5V" and bad[0]["ports"]["J_IO1"] == "+3V3"
    assert rules.port_keying_check(old)["status"] == "failed"
    new = xh_backplane(old)
    assert rules.port_keying_conflicts(new) == []
    assert rules.port_keying_check(new)["status"] == "passed"


def test_live_backplane_port_keying():
    live = json.loads(BACKPLANE.read_text())
    if rules.port_keying_conflicts(live):
        pytest.xfail("library/backplane-v1.json still has J_LED on JST-PH (foundation's half of REVIEW-1 MAJ-3)")
    assert rules.port_keying_check(live)["status"] == "passed"


@pytest.mark.parametrize("text,ohms", [("10k", 1e4), ("100 k (AMP_SD pull-down)", 1e5), ("330R", 330.0),
                                       ("4k7", 4700.0), ("5.1k 1%", 5100.0), ("1M", 1e6), ("330 ohm", 330.0)])
def test_parse_ohms(text, ohms):
    assert rules.parse_ohms(text) == pytest.approx(ohms)


def test_amp_sd_rule_fails_at_100k_and_passes_at_10k_with_1M_pullup():
    module = {"id": "AMP", "electrical": {"sd_pullup_ohm": 1e6, "sd_pullup_source": "test"}}
    at100 = rules.amp_sd_boot_check(100e3, module)
    at10 = rules.amp_sd_boot_check(10e3, module)
    assert at100["status"] == "failed" and at100["sd_v"] > rules.SD_SHUTDOWN_MAX_V   # ~0.25 V at 5.25 V VIN
    assert at10["status"] == "passed" and at10["sd_v"] < 0.06                          # ~0.047 V
    assert rules.amp_sd_voltage(1e6, 100e3, vin=5.0) == pytest.approx(5 * 50e3 / 1050e3)
    # without a library value the Adafruit reference network is used and labelled unconfirmed
    ref = rules.amp_sd_boot_check(10e3, None)
    assert ref["status"] == "passed" and not ref["confirmed"] and "not confirmed" in ref["detail"]


def test_r7_is_10k_and_the_deviation_from_backplane_is_recorded():
    bp = load_backplane(BACKPLANE)
    d = build_design(bp, _el("full"), META)
    assert d.part("R7").value == "10k"
    if "100" in str(bp["parts"]["R7"].get("value")):
        assert any(x.startswith("R7: fitted 10k") for x in d.deviations)


def test_every_bom_part_has_a_pinned_mpn_and_lcsc_id():
    for bpsrc in (json.loads(BACKPLANE.read_text()), xh_backplane(json.loads(BACKPLANE.read_text()))):
        bp = load_backplane(bpsrc)
        d = build_design(bp, _el("full"), META)
        for p in d.parts:
            if not p.in_bom or p.board_only:
                continue
            ident = sourcing.identity(p)
            assert ident and ident["mpn"] and ident["lcsc"].startswith("C") and ident["url"].startswith("https://"), p.ref
            assert "generic" not in ident["mpn"] and "class" not in ident["mpn"], p.ref
        assert "J_SRV" not in d.part("C4").bom["description"]
    xh = build_design(load_backplane(xh_backplane(json.loads(BACKPLANE.read_text()))), _el("full"), META)
    assert sourcing.identity(xh.part("J_LED"))["mpn"] == "B3B-XH-A(LF)(SN)"
    assert xh.part("J_LED").value.startswith("JST-XH 2.50")


def test_parts_table_entries_are_complete():
    db = sourcing.parts_db()
    for key, row in db["parts"].items():
        for f in ("maker", "mpn", "lcsc", "url", "unit_usd_planning"):
            assert row.get(f), (key, f)
    assert set(db["by_footprint"].values()) <= set(db["parts"])
    for k, sp in SUPPORT.items():
        if sp.get("part"):
            assert sp["part"] in db["parts"], k


def test_pcba_cost_covers_board_components_and_assembly():
    lines = [{"refs": "R1 R2", "qty": 2, "mpn": "x", "jlc_library": "basic", "fitting": "SMT", "unit_usd_planning": [0.001, 0.005]},
             {"refs": "J_PWR", "qty": 1, "mpn": "y", "jlc_library": None, "fitting": "SMT", "unit_usd_planning": [0.3, 1.2]},
             {"refs": "J_U1A J_U1B", "qty": 2, "mpn": "z", "jlc_library": None, "fitting": "THT", "unit_usd_planning": [0.05, 0.4]}]
    c = sourcing.pcba_cost(lines, {"smt": 40, "manual": 60}, "abc")
    assert c["complete"] and c["includes_usb_c_receptacle"] and c["status"] == "ESTIMATE"
    pilot, vol = c["batches"]["5"], c["batches"]["100"]
    for b in (pilot, vol):
        assert 0 < b["inr_per_board"]["low"] < b["inr_per_board"]["high"]
        assert set(b["parts_usd"]) == {"bare_board", "components", "assembly", "shipping_to_india"}
    assert vol["usd_per_board"]["high"] < pilot["usd_per_board"]["high"]  # fixed fees spread over more boards
    assert any("customs" in e for e in c["excludes"])


def test_design_id_and_revision_ignore_project_name_version_and_date():
    bp = load_backplane(BACKPLANE)
    el = _el("full")
    a = build_design(bp, el, META)
    b = build_design(bp, el, dict(META, name="Other", slug="other", revision="v9", date="2030-01-01"))
    assert a.design_id == b.design_id and a.revision == b.revision == a.design_id[:6].upper()
    el2 = copy.deepcopy(el)
    el2["nets"] = [n for n in el2["nets"] if n["name"] != "I2C_SDA"]
    assert design_id(bp, el2) != a.design_id


def test_pcb_key_changes_only_with_board_inputs():
    raw = json.loads(BACKPLANE.read_text())
    el = _el("minimal")
    k = reuse.pcb_key(raw, el, "robot", {"fast": True})
    shuffled = copy.deepcopy(el)
    shuffled["nets"] = [dict(n, nodes=list(reversed(n["nodes"]))) for n in reversed(shuffled["nets"])]
    shuffled["instances"] = [{"ref": "DISP1", "color": "pink"}]  # module-side/colour data is not a board input
    assert reuse.pcb_key(raw, shuffled, "robot", {"fast": True, "stories_audio": True, "only": "pcb"}) == k
    assert reuse.pcb_key(raw, el, "other", {"fast": True}) != k
    assert reuse.pcb_key(raw, el, "robot", {"fast": False}) != k
    assert reuse.pcb_key(json.loads(OLD_BACKPLANE.read_text()), el, "robot", {"fast": True}) != k  # placement change
    el2 = copy.deepcopy(el)
    el2["slots_used"] = [s for s in el2["slots_used"] if s != "J_IO1"]
    assert reuse.pcb_key(raw, el2, "robot", {"fast": True}) != k


def _fake_board(d: Path, key: str) -> None:
    (d / "gerbers").mkdir(parents=True)
    (d / "gerbers" / "b-F_Cu.gtl").write_text("G04 copper*\n")
    (d / "bom.csv").write_text("Item\n1\n")
    s = {"pcb_key": key, "routed": True, "revision": "ABC123", "say": "routed", "timings_s": {"total": 50.0},
         "file_sha256": reuse.file_hashes(d)}
    (d / "pcb-summary.json").write_text(json.dumps(s))


def test_reuse_finds_a_matching_board_and_rejects_a_tampered_one(tmp_path):
    base = tmp_path / "v1"
    _fake_board(base / "pcb", "K1")
    out = tmp_path / "v2"
    hit = reuse.find(out, "K1", {"pcb_reuse_from": str(base)})
    assert hit and hit[0] == str(base) and hit[2]["revision"] == "ABC123"
    reuse.adopt(hit[1], out / "pcb")
    assert (out / "pcb" / "gerbers" / "b-F_Cu.gtl").read_bytes() == (base / "pcb" / "gerbers" / "b-F_Cu.gtl").read_bytes()
    assert reuse.find(tmp_path / "v3", "K2", {"pcb_reuse_from": str(base)}) is None   # other key
    (base / "pcb" / "bom.csv").write_text("changed\n")
    assert reuse.find(tmp_path / "v3", "K1", {"pcb_reuse_from": [str(base)]}) is None  # file no longer matches


def test_reuse_uses_the_cache_and_the_pipeline_archive(tmp_path):
    cache = tmp_path / "cache"
    _fake_board(tmp_path / "built" / "pcb", "K9")
    assert reuse.store(tmp_path / "built" / "pcb", "K9", {"pcb_cache_dir": str(cache)}) == cache / "K9"
    hit = reuse.find(tmp_path / "v2", "K9", {"pcb_cache_dir": str(cache)})
    assert hit and hit[0] == "cache"
    _fake_board(tmp_path / "v3" / ".gstudio" / "previous" / "pcb", "K7")
    hit = reuse.find(tmp_path / "v3", "K7", {})
    assert hit and hit[0] == "previous run"


def test_reuse_from_gcs_downloads_only_a_matching_board(tmp_path):
    src = tmp_path / "bucketdata" / "projects" / "p1" / "v1" / "pcb"
    _fake_board(src, "KG")

    class Blob:
        def __init__(self, name):
            self.path = tmp_path / "bucketdata" / name

        def exists(self):
            return self.path.is_file()

        def download_as_bytes(self):
            return self.path.read_bytes()

        def download_to_filename(self, fn):
            Path(fn).write_bytes(self.path.read_bytes())

    class Client:
        def bucket(self, name):
            assert name == "bkt"
            return type("B", (), {"blob": staticmethod(lambda n: Blob(n))})()

    hit = reuse.find(tmp_path / "v2", "KG", {"pcb_reuse_from": "gs://bkt/projects/p1/v1"}, gcs_client=Client())
    assert hit and hit[0] == "gs://bkt/projects/p1/v1" and (hit[1] / "gerbers" / "b-F_Cu.gtl").exists()
    assert reuse.find(tmp_path / "v2", "OTHER", {"pcb_reuse_from": "gs://bkt/projects/p1/v1"}, gcs_client=Client()) is None


def test_silk_audit_reads_visible_silk_text_and_guide_designators(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text('(kicad_pcb (gr_text "J_LED\\nLED 5V" (at 1 2 0) (layer "F.SilkS")) (gr_text "U1" (at 1 2 0) (layer "F.Fab"))'
                   ' (footprint "R" (property "Reference" "R7" (at 0 0 0) (layer "F.SilkS"))'
                   ' (property "Reference" "J_MIC" (at 0 0 0) (layer "F.SilkS") (hide yes))))')
    texts = silk_audit.silk_texts(pcb)
    words = silk_audit.silk_words(texts)
    assert {"J_LED", "LED", "5V", "R7"} <= words and "U1" not in words and "J_MIC" not in words
    guide = "Plug the core board into U1 and the amp into A1. Plug BTN1 into J_IO1; J_DISP is not fitted. BP1.J_LED."
    assert silk_audit.guide_designators(guide) == {"U1", "A1", "J_IO1", "J_DISP", "BP1", "J_LED"}
    assert silk_audit.missing_designators(guide, words, omitted=["J_DISP"]) == ["A1", "BP1", "J_IO1", "U1"]


def test_silk_names_use_the_guide_designators():
    assert silk_name("J_LED") == ("J_LED", "LED 5V")
    assert silk_name("J_A1") == ("A1", "AMP") and silk_name("J_S1") == ("S1", "SD")
    assert silk_name("J_MIC") == ("J_MIC", None) and silk_name("J_PWR") == ("J_PWR", "USB-C")
    assert silk_name("J_U1A") is None and silk_name("J_A1P") is None
