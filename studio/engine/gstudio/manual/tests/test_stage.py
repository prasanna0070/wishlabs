"""Full manual stage run with rendering against the fixture gadget."""

import json
import re
import xml.etree.ElementTree as ET

from PIL import Image

from gstudio.manual.guide import guide_final_titles, guide_images


def test_summary_clean(minibot_run):
    out, s, _ = minibot_run
    assert s["figures_failed"] == []
    assert s["orphans"] == []
    assert s["missing_references"] == []
    assert s["label_overlaps"] == 0, s["label_violations"]
    assert s["steps_with_empty_parts"] == []
    assert s["final_checks_match_tests"]
    assert s["ok"]


def test_every_referenced_image_exists_and_no_orphans(minibot_run):
    out, s, _ = minibot_run
    build = json.loads((out / "build.json").read_text())
    md = (out / "BUILD_GUIDE.md").read_text()
    refs = {v["path"] for st in build["steps"] for v in st.get("visuals", [])}
    refs |= {v["path"] for st in build["wiring"]["steps"] for v in st.get("visuals", [])}
    refs |= set(guide_images(md))
    for r in refs:
        assert (out / r).is_file(), r
    shipped = {p.relative_to(out).as_posix() for p in (out / "manual").iterdir() if p.suffix in (".png", ".svg")}
    assert shipped <= refs, shipped - refs
    for st in build["steps"]:
        for v in st.get("visuals", []):
            assert v["alt"] and v["caption"]
            assert v["path"] in md


def test_images_are_real(minibot_run):
    out, s, _ = minibot_run
    for p in (out / "manual").glob("*.png"):
        im = Image.open(p)
        assert im.width >= 1000 and im.height >= 700, p
        ex = im.convert("L").getextrema()
        assert ex[1] - ex[0] > 100, f"{p} looks blank"
    assert s["images_rendered"] >= 12
    assert (out / "manual" / "backplane-pcb.png").is_file()


def test_wiring_svg(minibot_run):
    out, _, _ = minibot_run
    root = ET.parse(out / "manual" / "wiring.svg").getroot()
    txt = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
    for token in ("J_I2C1", "J_MIC", "DISP1", "GPIO8", "I2C_SDA", "Sources:"):
        assert token in txt, token


def test_guide_structure(minibot_run):
    out, _, _ = minibot_run
    md = (out / "BUILD_GUIDE.md").read_text()
    build = json.loads((out / "build.json").read_text())
    assert guide_final_titles(md) == [t["title"] for t in build["tests"]]
    for i, st in enumerate(build["steps"], 1):
        assert f"### {i}. {st['title']}" in md
        assert f"Checklist ID: `{st['id']}`." in md
    assert md.count("**Expected result:**") == len(build["steps"])
    assert md.count("**Stop if:**") == len(build["steps"])
    assert "**Before you start:**" in md and "## Final checks to record" in md
    physical = md.split("## Final checks to record")[1].split("Digital checks")[0]
    bullets = [ln for ln in physical.splitlines() if ln.startswith("- **")]
    assert bullets and not any(re.search(r"\bpassed\b", b) for b in bullets)


def test_build_json_merges(minibot_run):
    out, _, _ = minibot_run
    build = json.loads((out / "build.json").read_text())
    assert build["schema"] == "gadget-build/1"
    assert {"steps", "wiring", "tests"} <= set(build)


def test_stage_is_fast_enough(minibot_run):
    _, s, _ = minibot_run
    assert s["_wall"] < 120, s["_wall"]
