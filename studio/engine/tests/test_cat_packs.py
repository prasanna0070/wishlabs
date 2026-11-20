"""Catalog packs (CATALOG-PLAN 0.5, package E1): the switch, the merge of pack rows and what the agent can see.

- a temp pack with one J_IO row merges into a supported row on J_IO1-3 whose every pin reaches a port pin;
- the row is invisible when its pack is off (file, GS_PACKS_ENABLED, GS_PACKS_OFF);
- the merge stops on a duplicate id, an unmapped module pin or a role the port does not accept;
- every row of the repo's own packs is merged, wired pin for pin and has an exact harness row (unless its pack lists
  it in `known_harness_gaps`), and the agent's parts table stays under 8000 characters with every pack on;
- the EX3 examples normalize to the same derived block as BASELINE (fixtures_cat/ex3_derived, captured from
  origin/hack/integration b06a131 before E1).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gstudio import packs
from gstudio.electrical import assign_slots, harness_wires, select_cable
from gstudio.library import Library

STUDIO = Path(__file__).resolve().parents[2]
LIB = STUDIO / "library"
FIX = Path(__file__).resolve().parent / "fixtures_cat"
ENV = ("GS_PACKS_ENABLED", "GS_PACKS_OFF", "GS_LIBRARY")

SOIL = {
    "id": "TEST_SOIL_PROBE_CAP", "catalog_row": "P-testpack-1", "catalog_id": None, "role": "sensor",
    "name": "Test capacitive soil probe (unit-test row)",
    "reference_product": {"maker": "test", "mpn_or_model": "test", "product_url": None, "datasheet_url": None},
    "dims_mm": {"x": 98.0, "y": 23.0, "z": 4.0}, "pcb_thickness_mm": 1.6,
    "mount": {"type": "none", "holes": [], "hole_origin": "n/a", "notes": "test"},
    "pins": [{"name": "AOUT", "func": "analog out"}, {"name": "VCC", "func": "power"}, {"name": "GND", "func": "ground"}],
    "module_connector": {"type": "header_2.54", "pin_order": ["AOUT", "VCC", "GND"], "location": "3-pin header"},
    "backplane_harness": {"backplane_connector": "JST-PH 2.0 3p", "wires": [], "length_mm": 150},
    "aperture": {"needs": "cable_exit", "size_mm": {"w": 6, "h": 6}},
    "electrical": {"supply_v": "3.3-5.5", "logic_v": "3.3", "current_ma_typ": 5, "current_ma_max": 5, "bus": "analog"},
    "firmware": {"arduino_libraries": [], "driver_notes": "analogRead"}, "kicad": {"backplane_footprint": "NONE", "notes": ""},
    "sourcing_in": [{"vendor": "Probots", "url": "https://probots.co.in/test-soil.html", "price_inr_seen": 89,
                     "seen_on": "2026-11-20", "status": "page_read", "stock": "ships in 5 days", "notes": "test row"}],
    "price_inr_planning": [55, 130], "evidence": ["unit test row"], "uncertain": ["unit test row"],
    "manual": {"word": "soil probe", "noun": "soil probe", "part_kind": "sensor", "mount_type": "cable exit"},
    "merge": {"role": "soil_moisture", "prefix": "SOIL", "placement": "feature", "designable": True,
              "port": {"port": "io", "slots": ["J_IO1", "J_IO2", "J_IO3"], "cable": {"family": "JST-PH 2.0", "pins": 3},
                       "supported": True, "why": "3.3 V analog signal on a simple port"},
              "prewire": None, "face_mm": [20, 12], "depth_mm": 15},
}


def _clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def _copy_library(dst: Path) -> Path:
    """The merge inputs only (sources, backplane, sourcing updates, references index, tools), not the 14 MB of sources."""
    lib = dst / "library"
    (lib / "references").mkdir(parents=True)
    for f in ("modules-a.json", "modules-b.json", "modules-foundation.json", "backplane-v1.json", "sourcing-updates.json"):
        if (LIB / f).exists():
            shutil.copy2(LIB / f, lib / f)
    if (LIB / "references" / "index.json").exists():
        shutil.copy2(LIB / "references" / "index.json", lib / "references" / "index.json")
    shutil.copytree(LIB / "tools", lib / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    return lib


def _merge(lib: Path, pack_files: dict[str, dict]) -> subprocess.CompletedProcess:
    (lib / "packs").mkdir(exist_ok=True)
    for name, data in pack_files.items():
        (lib / "packs" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ENV}
    return subprocess.run([sys.executable, "-I", str(lib / "tools" / "merge_modules.py")], capture_output=True, text=True,
                          env=env, timeout=300)


@pytest.fixture(scope="module")
def merged(tmp_path_factory):
    lib = _copy_library(tmp_path_factory.mktemp("catlib"))
    (lib / "packs" / "examples" / "testpack").mkdir(parents=True)  # the examples folder is ignored by the merge
    (lib / "packs" / "examples" / "testpack" / "note.json").write_text("{}", encoding="utf-8")
    r = _merge(lib, {"testpack": {"pack": "testpack", "rows": [SOIL], "prompt_rules": ["Test rule."]}})
    assert r.returncode == 0, r.stdout + r.stderr
    return lib


# ------------------------------------------------------------------------------------------------ the switch
def test_switch_file_env_and_kill(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    lib = tmp_path / "library"
    (lib / "packs").mkdir(parents=True)
    (lib / "modules.json").write_text("[]", encoding="utf-8")
    assert packs.enabled_set(lib) == frozenset()  # no enabled.json: nothing on
    f = lib / "packs" / "enabled.json"
    f.write_text(json.dumps({"enabled": ["sensing", "motion"]}), encoding="utf-8")
    assert packs.enabled_set(lib) == {"sensing", "motion"} and packs.enabled("motion", lib) and not packs.enabled("mount", lib)
    monkeypatch.setenv("GS_PACKS_OFF", "motion")
    assert packs.enabled_set(lib) == {"sensing"}
    monkeypatch.setenv("GS_PACKS_OFF", "motion+sensing")
    assert packs.enabled_set(lib) == frozenset()
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,mount")  # replaces the file list; the kill switch still applies
    monkeypatch.setenv("GS_PACKS_OFF", "mount")
    assert packs.enabled_set(lib) == {"printed"}
    monkeypatch.setenv("GS_PACKS_ENABLED", "")
    assert packs.enabled_set(lib) == frozenset()
    monkeypatch.delenv("GS_PACKS_ENABLED")
    monkeypatch.delenv("GS_PACKS_OFF")
    f.write_text(json.dumps({"enabled": ["sculpt"]}), encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 5_000_000_000))  # the mtime cache sees the edit
    assert packs.enabled_set(lib) == {"sculpt"}
    f.write_text("{not json", encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000_000))
    assert packs.enabled_set(lib) == frozenset()  # a broken switch file fails closed


def test_pack_helpers_read_rules_and_examples(merged, monkeypatch):
    _clean_env(monkeypatch)
    assert [n for n, _ in packs.pack_files(merged)] == ["testpack"]
    assert packs.prompt_rules(merged) == [] and packs.example_specs(merged) == []
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack")
    assert packs.prompt_rules(merged) == [("testpack", ["Test rule."])]
    ex = merged / "packs" / "examples" / "testpack" / "demo"
    ex.mkdir(parents=True)
    (ex / "spec.json").write_text(json.dumps(_example("desk-buddy")), encoding="utf-8")
    bad = merged / "packs" / "examples" / "testpack" / "needs-printed"
    bad.mkdir(parents=True)
    printed = {"schema": "gadget-spec/1", "name": "Cup", "slug": "cup", "kind": "printed",
               "body": {"family": "revolve", "profile": [[0, 0], [30, 0], [30, 60], [0, 60]], "split": "none", "feet": 0}}
    (bad / "spec.json").write_text(json.dumps(printed), encoding="utf-8")
    # d-to-e 9: an example that needs a pack that is off (printed) is not listed; listed again once that pack is on
    assert packs.example_specs(merged) == [ex / "spec.json"]
    assert packs.example_specs(merged, loadable=False) == [ex / "spec.json", bad / "spec.json"]
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack,printed")
    assert packs.example_specs(merged) == [ex / "spec.json", bad / "spec.json"]


# ------------------------------------------------------------------------------------------------ merge of a temp pack
def test_temp_pack_row_merges_to_a_wired_io_row(merged, monkeypatch):
    _clean_env(monkeypatch)
    rows = {m["id"]: m for m in json.loads((merged / "modules.json").read_text(encoding="utf-8"))}
    m = rows[SOIL["id"]]
    assert m["pack"] == "testpack" and m["role"] == "soil_moisture" and m["category"] == "sensor" and m["ref_prefix"] == "SOIL"
    pv1 = m["backplane_v1"]
    assert pv1["supported"] and pv1["slots"] == ["J_IO1", "J_IO2", "J_IO3"] and pv1["cable"] == {"family": "JST-PH 2.0", "pins": 3}
    assert m["aperture"]["feature_allowed"] and m["aperture"]["required"] and m["merge"]["designable"]
    assert m["electrical"]["supply_rail"] == "+3V3" and m["manual"]["word"] == "soil probe"
    assert m["face_mm"] == [20, 12] and m["depth_mm"] == 15  # the in-shell envelope of a part that sits outside (c-to-e 1)
    src = m["sourcing_in"][0]  # a pack's own stock words are kept as the basis; the field uses the vocabulary
    assert src["stock"] == "unknown" and "ships in 5 days" in src["stock_basis"] and src["checked_at"] == "2026-11-20"
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack")
    lib = Library(merged)
    slot = lib.slot("J_IO1")
    wires = {w["module_pin"]: w["slot_pin_name"] for w in harness_wires(lib.get(SOIL["id"]), slot)}
    assert wires == {"AOUT": "SIG", "VCC": "3V3", "GND": "GND"}  # every module pin lands on a port pin (none dropped)
    sel = select_cable(lib, lib.get(SOIL["id"]), slot, 150)
    assert sel["match"] == "exact" and sel["module"] == "CABLE_JSTPH_3P_DUPONT1X3_150_X321"
    smap, errs = assign_slots([{"ref": f"SOIL{k}", "module": SOIL["id"], "role": "soil_moisture", "source": "feature"}
                               for k in (1, 2, 3, 4)], lib)
    assert smap == {"SOIL1": "J_IO1", "SOIL2": "J_IO2", "SOIL3": "J_IO3"} and [e["code"] for e in errs] == ["slot_full"]


def test_pack_row_invisible_when_off(merged, monkeypatch):
    _clean_env(monkeypatch)
    off = Library(merged)
    assert not off.has(SOIL["id"]) and SOIL["id"] not in off.summary_for_llm()
    assert any(m["id"] == SOIL["id"] for m in off.all_modules)
    assert not [m for m in off if m.get("pack")]  # generated harness rows used only by the pack hide with it
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack")
    on = Library(merged)
    assert on.has(SOIL["id"])
    line = next(l for l in on.summary_for_llm().splitlines() if l.startswith(SOIL["id"] + " |"))
    assert line.endswith("| feature") and "soil_moisture" in line and "cable_exit 6x6" in line
    assert len(on) == len(off) + sum(1 for m in on if m.get("pack"))
    monkeypatch.setenv("GS_PACKS_OFF", "testpack")
    assert not Library(merged).has(SOIL["id"])
    (merged / "packs" / "enabled.json").write_text(json.dumps({"enabled": ["testpack"]}), encoding="utf-8")
    try:
        monkeypatch.delenv("GS_PACKS_ENABLED")
        assert not Library(merged).has(SOIL["id"])  # the kill switch beats the file
        monkeypatch.delenv("GS_PACKS_OFF")
        assert Library(merged).has(SOIL["id"])
    finally:
        (merged / "packs" / "enabled.json").unlink()


def test_merge_rejects_duplicates_and_silent_wiring_errors(tmp_path):
    lib = _copy_library(tmp_path)
    dup = dict(SOIL, id="BUTTON_TACTILE_KY004")
    r = _merge(lib, {"dup": {"pack": "dup", "rows": [dup]}})
    assert r.returncode != 0 and "never replaces" in (r.stdout + r.stderr)
    (lib / "packs" / "dup.json").unlink()
    twice = {"one": {"pack": "one", "rows": [SOIL]}, "two": {"pack": "two", "rows": [dict(SOIL)]}}
    r = _merge(lib, twice)
    assert r.returncode != 0 and "never replaces" in (r.stdout + r.stderr)
    for f in (lib / "packs").glob("*.json"):
        f.unlink()
    lost = json.loads(json.dumps(SOIL))
    lost["module_connector"]["pin_order"] = ["AOUT", "VCC", "GND", "EN"]
    lost["pins"].append({"name": "EN", "func": "enable"})
    r = _merge(lib, {"lost": {"pack": "lost", "rows": [lost]}})
    assert r.returncode != 0 and "map to no pin" in (r.stdout + r.stderr)
    lost["merge"]["unconnected"] = ["EN"]  # an open pin is fine when the pack says so
    r = _merge(lib, {"lost": {"pack": "lost", "rows": [lost]}})
    assert r.returncode == 0, r.stdout + r.stderr
    (lib / "packs" / "lost.json").unlink()
    bad = json.loads(json.dumps(SOIL))
    bad["merge"]["role"] = "pump"
    r = _merge(lib, {"bad": {"pack": "bad", "rows": [bad]}})
    assert r.returncode != 0 and "does not accept role 'pump'" in (r.stdout + r.stderr)
    (lib / "packs" / "bad.json").unlink()
    wrong = json.loads(json.dumps(SOIL))
    wrong["merge"]["port"]["cable"] = {"family": "JST-PH 2.0", "pins": 4}
    r = _merge(lib, {"wrong": {"pack": "wrong", "rows": [wrong]}})
    assert r.returncode != 0 and "does not match J_IO1" in (r.stdout + r.stderr)


# ------------------------------------------------------------------------------------------------ the repo's own packs
def _repo_packs() -> list[tuple[str, dict]]:
    out = []
    for name, f in packs.pack_files(LIB):
        out.append((name, json.loads(f.read_text(encoding="utf-8"))))
    return out


def test_repo_pack_rows_are_merged_wired_and_harnessed(monkeypatch):
    _clean_env(monkeypatch)
    rp = _repo_packs()
    if not rp:
        pytest.skip("no library/packs/*.json in this checkout")
    monkeypatch.setenv("GS_PACKS_ENABLED", ",".join(n for n, _ in rp))
    lib = Library()
    problems = []
    for name, data in rp:
        gaps = set(data.get("known_harness_gaps") or [])
        for row in data["rows"]:
            mid = row["id"]
            if not lib.has(mid):
                problems.append(f"{name}/{mid}: not in modules.json (regenerate: gen_backplane_v1.py then merge_modules.py)")
                continue
            m = lib.get(mid)
            if m.get("pack") != name:
                problems.append(f"{name}/{mid}: merged row says pack {m.get('pack')!r}")
            pv1 = m.get("backplane_v1") or {}
            if not pv1.get("supported") or not pv1.get("cable"):
                continue
            for sid in pv1["slots"]:
                slot = lib.slot(sid)
                got = {w["module_pin"] for w in harness_wires(m, slot)}
                want = [w.get("module_pin") for w in (m.get("backplane_harness") or {}).get("wires", []) if w.get("module_pin")]
                want = want or list((m.get("module_connector") or {}).get("pin_order") or [p["name"] for p in m.get("pins") or []])
                lost = [p for p in want if p not in got and p not in set((m.get("merge") or {}).get("unconnected") or [])]
                if lost:
                    problems.append(f"{name}/{mid}: pins {lost} reach no pin of {sid}")
                sel = select_cable(lib, m, slot, 150)
                if sel["match"] != "exact" and mid not in gaps:
                    problems.append(f"{name}/{mid}: no exact harness row on {sid} ({sel['why']}); add one or list it in known_harness_gaps")
    assert not problems, "\n".join(problems)
    assert len(lib.summary_for_llm()) < 8000


def test_bare_servo_row_hidden_from_the_agent(monkeypatch):
    _clean_env(monkeypatch)
    lib = Library()
    assert lib.has("SERVO_SG90_TOWERPRO")  # still in the library (BOM, refs) ...
    assert "SERVO_SG90_TOWERPRO" not in lib.summary_for_llm()  # ... but no app drives it and it has no CAD mount
    assert "SERVO_SG90_TOWERPRO" not in [m["id"] for m in lib.internal_modules()]


# ------------------------------------------------------------------------------------------------ EX3 unchanged
@pytest.mark.parametrize("slug", ["bedtime-robot", "cloud-lamp", "desk-buddy"])
def test_ex3_derived_block_matches_baseline(slug, monkeypatch):
    from gstudio.normalize import normalize
    from gstudio.spec import load_spec

    _clean_env(monkeypatch)
    want = json.loads((FIX / "ex3_derived" / f"{slug}.json").read_text(encoding="utf-8"))
    sp, rep = normalize(load_spec(STUDIO / "examples" / slug / "spec.json"), Library())
    got = json.loads(json.dumps(sp.derived.model_dump(mode="json"), sort_keys=True))
    assert rep["errors"] == want["errors"] == []
    for d in (got, want["derived"]):
        d.pop("engine_version", None)
    assert got == want["derived"]


# ------------------------------------------------------------------------------------------------ E1.5 normalize rules
def _example(slug: str) -> dict:
    return json.loads((STUDIO / "examples" / slug / "spec.json").read_text(encoding="utf-8"))


def test_servo_in_a_child_directed_design_is_refused(monkeypatch):
    from gstudio.normalize import normalize

    _clean_env(monkeypatch)
    lib = Library()
    d = _example("bedtime-robot")
    assert d["audience"]["child_directed"] is True
    d["electronics"]["extra_modules"] = list(d["electronics"].get("extra_modules") or []) + ["SERVO_SG90_TOWERPRO"]
    _, rep = normalize(d, lib)
    hit = [e for e in rep["errors"] if e.get("code") == "actuator_child"]
    assert hit and hit[0]["fix"] == "remove the servo" and hit[0]["path"].startswith("electronics.extra_modules")
    d["audience"]["child_directed"] = False
    d["safety"]["flags"] = [f for f in d["safety"]["flags"] if f != "CHILD_DIRECTED"]
    _, rep = normalize(d, lib)
    assert not [e for e in rep["errors"] if e.get("code") == "actuator_child"]


def test_notifier_clock_light_rule_names_only_visible_roles(monkeypatch):
    from gstudio.normalize import normalize

    _clean_env(monkeypatch)
    # every pack off explicitly: the checkout's library/packs/enabled.json (integrator, hack/catalog) may switch motion on
    monkeypatch.setenv("GS_PACKS_ENABLED", "")
    d = _example("desk-buddy")
    d["behaviour"] = {"template": "notifier_clock", "summary": "", "settings": {}}
    d["features"] = [f for f in d["features"] if Library().get(f["module"])["role"] != "led"]
    _, rep = normalize(d, Library())
    e = next(e for e in rep["errors"] if e.get("code") == "template_requirement" and "notifications" in e["msg"])
    # no motion pack: the agent is told about LEDs only, never about a servo it cannot see
    assert e["msg"] == "notifier_clock needs an LED feature for notifications"
    assert "servo" not in e["fix"] and "LED_RING_12_WS2812_50MM" in e["fix"]
    if any(n == "motion" for n, _ in packs.pack_files()):  # with the motion pack on, the servo flag is named too
        monkeypatch.setenv("GS_PACKS_ENABLED", "motion")
        _, rep = normalize(d, Library())
        e = next(e for e in rep["errors"] if e.get("code") == "template_requirement" and "notifications" in e["msg"])
        assert e["msg"] == "notifier_clock needs an LED or a servo flag for notifications"


def test_cable_exit_part_gets_the_longest_lead(merged, monkeypatch):
    """c-to-e 2: a soil probe lives in the pot, so its lead is always the 300 mm class whatever the in-shell route."""
    from gstudio.normalize import normalize

    _clean_env(monkeypatch)
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack")
    d = _example("desk-buddy")
    d["features"].append({"id": "soil", "module": SOIL["id"], "face": "rear", "at": [0, 30], "label": "Soil probe lead"})
    sp, rep = normalize(d, Library(merged))
    h = next(h for h in sp.derived.harness if h.ref.startswith("SOIL"))
    assert h.length_mm == 300 and h.module == "CABLE_JSTPH_3P_DUPONT1X3_300_X321", rep["errors"]


def test_soil_probe_only_under_sensor_display(merged, monkeypatch):
    """c-to-e 3: only the sensor_display firmware reads a soil probe or float switch; elsewhere it is refused."""
    from gstudio.normalize import normalize

    _clean_env(monkeypatch)
    monkeypatch.setenv("GS_PACKS_ENABLED", "testpack")
    d = _example("desk-buddy")
    d["features"].append({"id": "soil", "module": SOIL["id"], "face": "rear", "at": [0, 30], "label": "Soil probe lead"})
    _, rep = normalize(d, Library(merged))
    e = [x for x in rep["errors"] if x["path"] == "behaviour.template"]
    assert e and e[0]["code"] == "template_requirement" and "soil probe is read only by the sensor_display" in e[0]["msg"]
    d["behaviour"] = {"template": "sensor_display", "summary": "", "settings": {}}
    _, rep = normalize(d, Library(merged))
    assert not [x for x in rep["errors"] if x["path"] == "behaviour.template"]
