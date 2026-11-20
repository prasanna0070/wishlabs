"""Pack `freeform` on the agent side (contract: projects/wish-labs-hack/_notes/freeform-contract.md).

- pack off: the prompt blocks, the tool wording and the photo prompt are exactly the live ones;
- pack on: the concept step and design rules ask for functional forms (no forced face, the contradiction is gone), the
  csg design block is shown (printed line only with pack printed), csg examples come first, faceless ones next;
- concept rules: with the pack on a face is only welcome for a playful or child wish (or a character subject), and
  only the wildcard may carry one otherwise; with the pack off every concept still needs a face;
- concept photos: a faceless concept is photographed as a plain product (pack on only);
- repair loop: a concept whose CAD render fails goes back to the agent with the builder's errors (pack on), the kept
  concepts are not rendered twice; with the pack off, or for a builder outage, a failed render stays final.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from conftest import KEY, events_named, make_settings, sse

# sha256 of the live (e8eb54d) prompt blocks: the pack-off prompt must stay byte-for-byte what is live
LIVE = {"FLOW": "a541b91fd2e40080c9b2f4d6c4c88b373213a935dd920584817781ca8fa391a6",
        "DESIGN": "967edda784e8b797369261d555f2c55ebb105f625c7b7461c286bd8ccc71d506",
        "ROLE": "6c5117a862512c74dd1e064ffcb86e8429f9617e4b95c9a6765f012c2b269ac1",
        "VOICE": "e0800e7e2653a3451f40ebd7f47dda1ca04dccb455cea6d0d91ba1875223a4a9"}
FF_MARKERS = ("Designing with csg bodies", "No face by default", "most functional product form",
              "Design rules for useful, printable, safe objects")
LIVE_LINES = ("Design rules for cute, printable, safe objects", "the obvious cute take on the subject",
              "A clear character face on every concept", "otherwise prefer clean product forms")


def _prompt(monkeypatch, tmp_path, packs: str, wish: str | None = None) -> str:
    from app.agent.prompt import PromptBuilder
    from app.engine_bridge import EngineBridge

    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", packs)
    s = make_settings(tmp_path)
    return PromptBuilder(EngineBridge(s), s).system_prompt(wish)


def test_live_blocks_are_unchanged():
    from app.agent import prompt

    for name, h in LIVE.items():
        assert hashlib.sha256(getattr(prompt, name).encode()).hexdigest() == h, name
    assert prompt.FLOW_CONCEPTS in prompt.FLOW and prompt.FLOW_FREEFORM != prompt.FLOW


def test_pack_off_prompt_has_no_freeform_text(monkeypatch, tmp_path):
    for packs in ("", "sensing,schedule"):
        text = _prompt(monkeypatch, tmp_path, packs, "a desk station with a tray for my keys")
        assert not any(m in text for m in FF_MARKERS), packs
        assert all(m in text for m in LIVE_LINES), packs


def test_pack_on_prompt_teaches_functional_forms(monkeypatch, tmp_path):
    from app.agent.prompt import CSG_PRINTED, DESIGN_FREEFORM, FLOW_CONCEPTS_FREEFORM, csg_rules

    text = _prompt(monkeypatch, tmp_path, "sensing,schedule,freeform", "a desk station with a tray for my keys")
    assert DESIGN_FREEFORM in text and FLOW_CONCEPTS_FREEFORM in text and csg_rules(False) in text
    assert not any(m in text for m in LIVE_LINES)          # no forced face, no "cute take", no contradiction
    assert "wedge console" in text and "stepped organiser" in text and "dock with a tray groove" in text
    assert "wildcard = a character" in text and "(extrude axis \"x\")" in text
    assert CSG_PRINTED.strip() not in text
    on = _prompt(monkeypatch, tmp_path, "freeform,printed", "a sculpted teapot")
    assert CSG_PRINTED.strip() in on and "Every axis 60-260 mm (a printed object: 20-260 mm);" in on


class _StubBridge:
    def __init__(self, on, examples):
        self.on, self.examples = frozenset(on), examples

    def packs_on(self):
        return self.on

    def pack_example_paths(self):
        return self.examples


def _example(root: Path, slug: str, family: str, face: bool) -> Path:
    d = root / slug
    d.mkdir(parents=True)
    sp = {"name": slug.replace("-", " ").title(), "slug": slug, "body": {"family": family},
          "decor": [{"type": "eye", "at": [0, 90], "size": [10, 10]}] if face else []}
    (d / "spec.json").write_text(json.dumps(sp))
    return d / "spec.json"


def test_pack_on_shows_csg_examples_first(tmp_path):
    from app.agent.prompt import PromptBuilder

    ex = tmp_path / "studio" / "examples"
    robot = _example(ex, "bedtime-robot", "blob", face=True)
    owl = _example(ex, "owl-lamp", "revolve", face=True)
    plain = _example(ex, "plain-speaker", "silhouette", face=False)
    packs = tmp_path / "packs"
    tray = _example(packs, "tray-dock", "csg", face=False)
    wedge = _example(packs, "wedge-console", "csg", face=False)
    s = make_settings(tmp_path, studio_root=tmp_path / "studio")
    off = PromptBuilder(_StubBridge([], []), s).example_paths("a desk station")
    assert off[0] == robot and tray not in off
    on = PromptBuilder(_StubBridge(["freeform"], [tray, wedge]), s).example_paths("a wedge console for my desk")
    assert on[:2] == [wedge, tray] and len(on) == 4 and set(on[2:]) <= {robot, owl, plain}
    one = PromptBuilder(_StubBridge(["freeform"], [tray]), s).example_paths("an owl lamp")
    assert one[0] == tray and one[1] == plain                     # one csg example: faceless ones come next


def test_tool_wording_follows_the_pack():
    from app.agent.tools import SPEC_TOOLS, TOOLS, tool_spec

    for n in TOOLS:
        assert tool_spec(n, ()) is TOOLS[n] and tool_spec(n, ("sensing",)) is TOOLS[n]
    for n in SPEC_TOOLS:                        # pack on: the spec travels as JSON text (one string)
        props = tool_spec(n, ("freeform",))["parameters"]["properties"]
        spec = props["concepts"]["items"]["properties"]["spec"] if n == "propose_concepts" else props["spec"]
        assert spec["type"] == "string" and "JSON text" in spec["description"]
        live = TOOLS[n]["parameters"]["properties"]
        assert (live["concepts"]["items"]["properties"]["spec"] if n == "propose_concepts" else live["spec"])["type"] == "object"
    assert tool_spec("say", ("freeform",)) is TOOLS["say"]
    t = tool_spec("propose_concepts", ("freeform",))
    assert "cute" not in t["description"] and "most functional product form" in t["description"]
    assert "CAD render come back with errors" in t["description"]
    title = t["parameters"]["properties"]["concepts"]["items"]["properties"]["title"]["description"]
    assert "cute" not in title
    assert "cute" in TOOLS["propose_concepts"]["description"]   # the live wording is untouched


# ------------------------------------------------------------------------------------------- concept rules
NAMES = {"LCD_200_ST7789_240X320_WAVESHARE": {"name": "2.0 in IPS colour LCD 240x320, ST7789V", "role": "display"},
         "BUTTON_ARCADE_30MM_SNAPIN": {"name": "button", "role": "button"}}
EYES = [{"type": "eye", "at": [-20, 90], "size": [12, 12]}, {"type": "mouth", "at": [0, 70], "size": [20, 6]}]


def _c(title, tier, decor, x=0, face="front"):
    return {"title": title, "tier": tier, "blurb": "a desk station",
            "spec": {"decor": copy.deepcopy(decor),
                     "features": [{"module": "BUTTON_ARCADE_30MM_SNAPIN", "face": face, "at": [x, 40]}]}}


def test_freeform_rules_only_the_wildcard_may_have_a_face():
    from app.concept_rules import check_set, subject_from_text

    wish = "a desk station that shows the air quality and holds my keys"
    cs = [_c("Tray Dock", "expected", EYES, -40), _c("Wedge", "expected", [], 0), _c("Tower", "different", [], 40),
          _c("Owl Pal", "wildcard", EYES, 0, "top")]
    fails = check_set(cs, subject_from_text(wish), NAMES, freeform=True, wish=wish)
    assert [f["rule"] for f in fails] == ["no_face"] and fails[0]["titles"] == ["Tray Dock"]
    assert "Tray Dock has eye, mouth" in fails[0]["message"]
    cs[0]["spec"]["decor"] = []
    assert check_set(cs, subject_from_text(wish), NAMES, freeform=True, wish=wish) == []
    live = check_set(cs, subject_from_text(wish), NAMES)          # pack off: every concept still needs a face
    assert [f["rule"] for f in live] == ["face"] and set(live[0]["titles"]) == {"Tray Dock", "Wedge", "Tower"}


@pytest.mark.parametrize("wish", ["a night light for my daughter", "a cute plant buddy", "a robot that reads stories",
                                  "a playful desk toy"])
def test_freeform_faces_welcome_for_playful_child_or_character(wish):
    from app.concept_rules import check_set, faces_welcome, subject_from_text

    cs = [_c("A", "expected", EYES, -40), _c("B", "different", EYES, 40)]
    assert faces_welcome(wish, subject_from_text(wish), cs)
    assert "no_face" not in {f["rule"] for f in check_set(cs, subject_from_text(wish), NAMES, freeform=True, wish=wish)}


def test_child_directed_spec_welcomes_a_face():
    from app.concept_rules import faces_welcome

    c = _c("A", "expected", EYES)
    assert not faces_welcome("a lamp", None, [c])
    c["spec"]["audience"] = {"child_directed": True}
    assert faces_welcome("a lamp", None, [c])


# ------------------------------------------------------------------------------------------- concept photos
def test_photo_prompt_is_plain_for_a_faceless_concept():
    from app.imagegen import PROMPT, PROMPT_PLAIN, photo_prompt

    spec = {"colors": {"body": "#E7D7C1", "detail": "#333333", "body_name": "oat"}, "decor": []}
    live = PROMPT.format(body="oat", body_hex="#E7D7C1", detail_hex="#333333", title="T", blurb="B")
    assert photo_prompt(spec, "T", "B") == live
    plain = photo_prompt(spec, "T", "B", plain=True)
    assert plain != live and plain.startswith("Plain studio product photograph")
    assert "do not add a face, eyes, a mouth" in PROMPT_PLAIN and "bedside" not in plain


def test_plain_photo_only_with_the_pack_and_no_face(monkeypatch, tmp_path):
    from app.concepts import plain_photo

    class Svc:
        def __init__(self, on):
            self.bridge = _StubBridge(on, [])

    faceless, faced = {"decor": [{"type": "text", "text": "hi"}]}, {"decor": EYES}
    assert plain_photo(Svc(["freeform"]), faceless) and not plain_photo(Svc(["freeform"]), faced)
    assert not plain_photo(Svc([]), faceless) and not plain_photo(Svc(["sensing"]), faceless)
    screen_face = dict(faceless, behaviour={"template": "desk_companion"})   # its firmware draws a face on the screen
    assert not plain_photo(Svc(["freeform"]), screen_face)
    assert plain_photo(Svc(["freeform"]), dict(faceless, behaviour={"template": "sensor_display"}))


# ------------------------------------------------------------------------------------------- render repair loop
class _RenderBrain:
    """Two concepts; the wildcard's first spec does not render on the builder; the fix is resent alone."""

    kind = "mock"

    def __init__(self, svc):
        self.svc = svc
        self.calls: list[dict] = []

    def _specs(self):
        from app.agent.scripted import ScriptedBrain

        base = ScriptedBrain(self.svc).base_spec()
        other = copy.deepcopy(base)
        for f in other["features"]:          # its own layout (no two concepts share one)
            if f.get("at") and abs(f["at"][0]) > 10:
                f["at"] = [-f["at"][0], f["at"][1]]
        return base, other

    async def run(self, turn):
        base, other = self._specs()
        broken = copy.deepcopy(other)
        broken["name"] = "Broken Wedge"
        r1 = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape you prefer.", "concepts": [
            {"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
            {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": broken}]})
        self.calls.append(r1.response)
        if r1.terminal:
            return
        fixed = copy.deepcopy(other)
        fixed["name"] = "Fixed Wedge"
        r2 = await turn.call_tool("propose_concepts", {"concepts": [
            {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": fixed}]})
        self.calls.append(r2.response)


def _client(tmp_path, monkeypatch, packs: str, error: str):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import Services

    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", packs)
    s = make_settings(tmp_path)
    svc = Services(s)
    svc.brain = _RenderBrain(svc)
    real = svc.builder.preview
    svc.previews = []

    async def preview(spec):
        svc.previews.append(spec.get("name"))
        if spec.get("name") == "Broken Wedge":
            return {"ok": False, "png": None, "glb": None, "warnings": [], "errors": [error]}
        return await real(spec)

    svc.builder.preview = preview
    return TestClient(create_app(s, svc)), svc


CSG_ERR = "body.csg.nodes[1]: csg_disconnected: the tray block does not touch the body (fix: move it to overlap)"


def _run(c):
    c.post("/api/login", json={"key": KEY})
    pid = c.post("/api/projects", json={}).json()["id"]
    return pid, sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "a cute robot for my desk"})


def test_pack_on_render_failure_goes_back_to_the_agent(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule,freeform", CSG_ERR)
    with c:
        pid, evs = _run(c)
        reps = [r for r in events_named(evs, "repair") if r.get("kind") == "concept_render"]
        assert len(reps) == 1 and reps[0]["errors"] == [f"Wedge Robot: {CSG_ERR}"]
        first = svc.brain.calls[0]
        assert first["render_failures"] == ["Wedge Robot"] and first["kept"] == ["Desk Robot"]
        assert first["errors_by_concept"] == {"Wedge Robot": [CSG_ERR]} and "CAD render failed" in first["instruction"]
        assert "silhouette, revolve or blob body instead" in first["instruction"]
        cons = events_named(evs, "concepts")[0]["concepts"]
        assert [x["title"] for x in cons] == ["Desk Robot", "Wedge Robot"]
        assert all(x["state"] == "ready" and "render_errors" not in x for x in cons)
        assert cons[1]["spec"]["name"] == "Fixed Wedge"
        assert svc.previews.count("Broken Wedge") == 1 and svc.previews.count("Fixed Wedge") == 1
        assert len(svc.previews) == 3            # the kept concept was rendered once
        p = c.get(f"/api/projects/{pid}").json()
        assert all("render_errors" not in x for x in p["concepts"])


def test_pack_off_render_failure_stays_final(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule", CSG_ERR)
    with c:
        _, evs = _run(c)
        assert not [r for r in events_named(evs, "repair") if r.get("kind") == "concept_render"]
        cons = events_named(evs, "concepts")[0]["concepts"]
        bad = next(x for x in cons if x["title"] == "Wedge Robot")
        assert bad["state"] == "failed" and CSG_ERR in bad["photo_note"] and "render_errors" not in bad
        assert len(svc.brain.calls) == 1 and svc.brain.calls[0].get("ok") is True


def test_pack_on_builder_outage_is_not_repaired(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule,freeform", "preview timed out after 180 s")
    with c:
        _, evs = _run(c)
        assert not [r for r in events_named(evs, "repair") if r.get("kind") == "concept_render"]
        bad = next(x for x in events_named(evs, "concepts")[0]["concepts"] if x["title"] == "Wedge Robot")
        assert bad["state"] == "failed" and "render_errors" not in bad


def test_pack_on_render_repairs_are_bounded(tmp_path, monkeypatch):
    """A concept that never renders is sent back RENDER_REPAIR_ROUNDS times, then shown as failed."""
    from app.agent.turn import RENDER_REPAIR_ROUNDS

    class StubbornBrain(_RenderBrain):
        async def run(self, turn):
            base, other = self._specs()
            broken = copy.deepcopy(other)
            broken["name"] = "Broken Wedge"
            cs = [{"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
                  {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": broken}]
            for _ in range(6):
                r = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape.", "concepts": cs})
                self.calls.append(r.response)
                if r.terminal:
                    return

    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule,freeform", CSG_ERR)
    svc.brain = StubbornBrain(svc)
    with c:
        _, evs = _run(c)
        reps = [r for r in events_named(evs, "repair") if r.get("kind") == "concept_render"]
        assert len(reps) == RENDER_REPAIR_ROUNDS + 1     # the last one is reported, then the set is shown
        bad = next(x for x in events_named(evs, "concepts")[0]["concepts"] if x["title"] == "Wedge Robot")
        assert bad["state"] == "failed"
        assert svc.previews.count("Broken Wedge") == RENDER_REPAIR_ROUNDS + 1


# ------------------------------------------------------------------------------------------- csg in the web app
STEPS = {"op": "union", "blend": 6, "nodes": [
    {"op": "box", "size": [180, 140, 50], "round": 8},
    {"op": "move", "to": [0, 25, 0], "node": {"op": "box", "size": [180, 90, 90], "round": 8}},
    {"op": "move", "to": [0, 45, 0], "rot": [0, 0, 15], "node": {"op": "box", "size": [180, 50, 130], "round": 8}}]}


def test_scale_csg_scales_lengths_not_turns(monkeypatch):
    import sys

    from app.tweak import scale_csg, scale_spec

    monkeypatch.setitem(sys.modules, "gstudio.csg", None)          # the local rule (engine without the pack)
    t = scale_csg(STEPS, 1.2)
    assert t["blend"] == 7.2 and t["nodes"][0]["size"] == [216, 168, 60] and t["nodes"][0]["round"] == 9.6
    mv = t["nodes"][2]
    assert mv["to"] == [0, 54, 0] and mv["rot"] == [0, 0, 15] and mv["node"]["size"] == [216, 60, 156]
    ex = scale_csg({"op": "extrude", "axis": "x", "points": [[-60, 0], [60, 0], [60, 90]], "h": 190}, 0.9)
    assert ex["axis"] == "x" and ex["points"] == [[-54, 0], [54, 0], [54, 81]] and ex["h"] == 171
    assert STEPS["nodes"][0]["size"] == [180, 140, 50]              # the input is not changed
    sp = scale_spec({"body": {"family": "csg", "csg": STEPS}, "features": [{"at": [10, 40]}]}, 1.1)
    assert sp["body"]["csg"]["nodes"][0]["size"] == [198, 154, 55] and sp["features"][0]["at"] == [11, 44]


def test_csg_change_is_one_line():
    from app.changes import describe

    a = {"body": {"family": "csg", "csg": STEPS, "wall": 2.4}}
    b = copy.deepcopy(a)
    b["body"]["csg"]["nodes"][1]["node"]["size"] = [180, 90, 100]
    b["body"]["csg"]["nodes"][2]["to"] = [0, 40, 0]
    out = describe(a, b)
    assert [x["text"] for x in out] == ["body shape changed"] and out[0]["path"] == "body.csg"


class _BoxesShape:
    """A stand-in for gstudio.csg's compiled shape: a union of axis-aligned boxes standing on z = 0."""

    def __init__(self, boxes):
        self.boxes = boxes
        self.bounds = (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes),
                       max(b[3] for b in boxes), 0.0, max(b[5] for b in boxes))

    def sdf(self, x, y, z):
        inside = any(b[0] <= x <= b[1] and b[2] <= y <= b[3] and b[4] <= z <= b[5] for b in self.boxes)
        return -1.0 if inside else 1.0


def test_placeholder_draws_csg_silhouettes(monkeypatch):
    from app.builder import placeholder as ph

    # steps rising to the back: low front block, tall rear block (a stand-in shape, engine or not)
    monkeypatch.setattr(ph, "_csg_shape", lambda body: _BoxesShape([(-90, 90, -70, 70, 0, 50), (-90, 90, 20, 70, 0, 130)]))
    ph._CSG_CACHE.clear()
    body = {"family": "csg", "csg": STEPS}
    v = ph.csg_views(body)
    side = v["side"]
    front_z = max(z for y, z in side if y < 0)           # the front half of the side view is the low step
    rear_z = max(z for y, z in side if y > 30)
    assert front_z < 60 and rear_z > 120
    assert ph.envelope_mm({"body": body}) == {"w_mm": 180, "d_mm": 140, "h_mm": max(round(z) for _, z in v["front"])}
    png = ph.render_png({"body": body, "colors": {"body": "#E7D7C1"}}, 96)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    ph._CSG_CACHE.clear()


def test_csg_patterns_pass_the_engine_checks():
    """The prompt's csg patterns pass the engine's language and solid checks (skipped until gstudio.csg exists)."""
    import sys

    eng = str(Path(__file__).resolve().parents[3] / "engine")
    if eng not in sys.path:
        sys.path.insert(0, eng)
    csg = pytest.importorskip("gstudio.csg")
    from app.agent.prompt import CSG_PATTERNS, csg_rules

    for name, tree in CSG_PATTERNS:
        assert csg.validate_tree(tree) == [], name
        assert csg.solid_checks(tree, kind="gadget") == [], name
        assert f"  {name}: {json.dumps(tree, separators=(',', ':'))}" in csg_rules()


def test_csg_robot_cues():
    from app.concept_rules import archetype_cues

    head_on_body = {"op": "union", "blend": 8, "nodes": [
        {"op": "box", "size": [120, 110, 90], "round": 12},
        {"op": "move", "to": [0, 0, 85], "node": {"op": "sphere", "r": 50}}]}
    cues = archetype_cues({"body": {"family": "csg", "csg": head_on_body}}, {}, "robot")
    assert cues == {"boxy body", "head-on-body stack"}
    flat = {"op": "extrude", "axis": "x", "h": 190, "points": [[-65, 0], [65, 0], [65, 95], [-65, 35]]}
    assert archetype_cues({"body": {"family": "csg", "csg": flat}}, {}, "robot") == set()


class _CsgInvalidBrain(_RenderBrain):
    async def run(self, turn):
        base, other = self._specs()
        bad = copy.deepcopy(other)
        bad["body"] = {"family": "csg", "csg": {"op": "box"}, "wall": 2.4, "feet": 4}
        r = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape you prefer.", "concepts": [
            {"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
            {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": bad}]})
        self.calls.append(r.response)


@pytest.mark.parametrize("packs,hint", [("sensing,schedule,freeform", True), ("sensing,schedule", False)])
def test_csg_validation_errors_get_the_csg_hint_only_with_the_pack(tmp_path, monkeypatch, packs, hint):
    from app.agent.turn import CSG_HINT

    c, svc = _client(tmp_path, monkeypatch, packs, CSG_ERR)
    svc.brain = _CsgInvalidBrain(svc)
    with c:
        _run(c)
        first = svc.brain.calls[0]
        assert list(first["errors_by_concept"]) == ["Wedge Robot"]
        assert any("csg" in e for e in first["errors"])
        assert (CSG_HINT in first["instruction"]) is hint
        assert first["instruction"].startswith("These concepts did not validate.")


def test_placeholder_with_the_engine_csg():
    """With the engine's gstudio.csg (skipped without it): the stepped pattern's side view rises to the back."""
    import sys

    eng = str(Path(__file__).resolve().parents[3] / "engine")
    if eng not in sys.path:
        sys.path.insert(0, eng)
    pytest.importorskip("gstudio.csg")
    from app.agent.prompt import CSG_PATTERNS
    from app.builder import placeholder as ph

    ph._CSG_CACHE.clear()
    body = {"family": "csg", "csg": dict(CSG_PATTERNS)["stepped organiser rising to the back"]}
    v = ph.csg_views(body)
    front_z = max(z for y, z in v["side"] if y < -30)
    rear_z = max(z for y, z in v["side"] if y > 30)
    assert front_z < 60 and rear_z > 120
    env = ph.envelope_mm({"body": body})
    assert abs(env["w_mm"] - 180) <= 4 and abs(env["d_mm"] - 140) <= 4 and abs(env["h_mm"] - 130) <= 4
    ph._CSG_CACHE.clear()


def test_freeform_layout_rule_compares_same_forms_only():
    from app.concept_rules import check_set

    def c(title, tier, body):
        return {"title": title, "tier": tier, "blurb": "a desk station",
                "spec": {"body": body, "decor": [],
                         "features": [{"module": "LCD_200_ST7789_240X320_WAVESHARE", "face": "front", "at": [0, 60]}]}}

    wedge = {"family": "csg", "csg": {"op": "extrude", "axis": "x", "h": 190, "points": [[-65, 0], [65, 0], [65, 95]]}}
    steps = {"family": "csg", "csg": STEPS}
    blob = {"family": "blob", "primitives": [{"kind": "sphere", "center": [0, 0, 60], "size": [120, 120, 120]}]}
    cs = [c("Wedge", "expected", wedge), c("Steps", "expected", steps), c("Pod", "different", blob),
          c("Pod Two", "different", copy.deepcopy(blob))]
    fails = check_set(cs, None, NAMES, freeform=True, wish="a desk station")
    assert [f["rule"] for f in fails] == ["layout"] and fails[0]["titles"] == ["Pod Two"]
    live = check_set(cs, None, NAMES)                 # pack off: one layout for all four is three copies
    assert set(next(f for f in live if f["rule"] == "layout")["titles"]) == {"Steps", "Pod", "Pod Two"}


class _TwiceBadBrain(_RenderBrain):
    """The wildcard's csg spec fails the check twice; the second repair names it for a family fallback."""

    async def run(self, turn):
        base, other = self._specs()
        bad = copy.deepcopy(other)
        bad["body"] = {"family": "csg", "csg": {"op": "box"}, "wall": 2.4, "feet": 4}
        cs = [{"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
              {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": bad}]
        for _ in range(8):
            r = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape.", "concepts": cs})
            self.calls.append(r.response)
            if r.terminal:
                return
            cs = cs[1:]                          # resend only the failed one, still broken


def test_freeform_names_a_twice_failed_concept_and_has_one_more_round(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule,freeform", CSG_ERR)
    svc.brain = _TwiceBadBrain(svc)
    with c:
        _, evs = _run(c)
        ins = [x.get("instruction") or "" for x in svc.brain.calls]
        assert "Wedge Robot failed twice" not in ins[0] and "Wedge Robot failed twice: give it a silhouette" in ins[1]
        spec_reps = [r for r in events_named(evs, "repair") if not r.get("kind")]
        assert len(spec_reps) == svc.settings.max_repair_rounds + 2    # one more round than live, then shown
        cons = events_named(evs, "concepts")[0]["concepts"]
        assert [x["title"] for x in cons] == ["Desk Robot"]


def test_pack_off_spec_rounds_are_live(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule", CSG_ERR)
    svc.brain = _TwiceBadBrain(svc)
    with c:
        _, evs = _run(c)
        assert not any("failed twice" in (x.get("instruction") or "") for x in svc.brain.calls)
        assert len([r for r in events_named(evs, "repair") if not r.get("kind")]) == svc.settings.max_repair_rounds + 1


def test_unstring_decodes_json_text_in_specs_only():
    from app.agent.turn import _unstring_specs

    args = {"message": "[keep me]", "concepts": [
        {"title": "A", "spec": {"body": {"family": "csg", "csg": '{"op": "box", "size": [100, 110, 60]}'},
                                "features": '[{"id": "screen", "at": [0, 40]}]', "name": "{not json"}},
        {"title": "B", "spec": '{"body": {"family": "blob"}, "decor": "[]"}'}]}
    out = _unstring_specs(args)
    a, b = out["concepts"][0]["spec"], out["concepts"][1]["spec"]
    assert a["body"]["csg"] == {"op": "box", "size": [100, 110, 60]} and a["features"][0]["at"] == [0, 40]
    assert a["name"] == "{not json" and out["message"] == "[keep me]"
    assert b == {"body": {"family": "blob"}, "decor": []}
    assert args["concepts"][0]["spec"]["features"].startswith("[")      # the input is not changed


@pytest.mark.parametrize("packs,decoded", [("sensing,schedule,freeform", True), ("sensing,schedule", False)])
def test_stringified_spec_parts_only_decoded_with_the_pack(tmp_path, monkeypatch, packs, decoded):
    class StringBrain(_RenderBrain):
        async def run(self, turn):
            base, other = self._specs()
            s2 = copy.deepcopy(other)
            s2["features"] = json.dumps(s2["features"])           # the live failure: a list sent as JSON text
            r = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape.", "concepts": [
                {"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
                {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": s2}]})
            self.calls.append(r.response)

    c, svc = _client(tmp_path, monkeypatch, packs, CSG_ERR)
    svc.brain = StringBrain(svc)
    with c:
        _run(c)
        first = svc.brain.calls[0]
        assert (first.get("ok") is True) is decoded
        if not decoded:
            assert any("features" in e for e in first["errors"])


def test_keys_only_objects_are_repaired_where_nothing_is_lost():
    from app.agent.turn import _unstring_specs

    sp = {"electronics": {"core": "X", "power": ["adapter_in_box", "input"]},
          "behaviour": {"template": "sensor_display", "settings": []}}
    out = _unstring_specs({"concepts": [{"title": "A", "spec": sp}]})["concepts"][0]["spec"]
    assert out["electronics"] == {"core": "X"} and out["behaviour"]["settings"] == {}
    keep = {"behaviour": {"settings": ["names_for_audio"]}}          # values would be lost: left for the validator
    assert _unstring_specs({"spec": keep})["spec"] == keep


def test_format_slips_do_not_count_toward_the_family_fallback(tmp_path, monkeypatch):
    class SlipBrain(_RenderBrain):
        async def run(self, turn):
            base, other = self._specs()
            slip = copy.deepcopy(other)
            slip["features"] = {"not": "a list"}              # a format slip, not a design failure
            cs = [{"title": "Desk Robot", "tier": "expected", "blurb": "a robot desk station", "spec": base},
                  {"title": "Wedge Robot", "tier": "wildcard", "blurb": "a wedge robot", "spec": slip}]
            for _ in range(3):
                r = await turn.call_tool("propose_concepts", {"message": "Choose the robot shape.", "concepts": cs})
                self.calls.append(r.response)
                if r.terminal:
                    return
                cs = cs[1:]

    c, svc = _client(tmp_path, monkeypatch, "sensing,schedule,freeform", CSG_ERR)
    svc.brain = SlipBrain(svc)
    with c:
        _run(c)
        assert not any("failed twice" in (x.get("instruction") or "") for x in svc.brain.calls)


def test_csg_patterns_keep_the_floor_rule():
    """Every pattern's lowest piece is at least 100 x 110 mm at the floor (the prompt's own rule; contract v2's table
    rule keeps a rounded piece's bottom flat and square, and 2 x round is kept as a margin for its corner rounds)."""
    from app.agent.prompt import CSG_PATTERNS

    def floor(node, z=0.0):
        """(w, d, round) of the pieces that stand on z = 0 (boxes, cylinders, x-extrudes), recursively."""
        out = []
        op = node.get("op")
        if op == "move":
            return floor(node["node"], z + float(node.get("to", [0, 0, 0])[2]))
        if op in ("union", "intersect"):
            return [f for n in node["nodes"] for f in floor(n, z)]
        if op == "cut":
            return floor(node["node"], z)
        if z > 0:
            return out
        r = float(node.get("round", 0))
        if op == "box":
            out.append((node["size"][0], node["size"][1], r))
        elif op == "cylinder":
            out.append((2 * node["r"], 2 * node["r"], r))
        elif op == "extrude" and node.get("axis") == "x":
            ys = [p[0] for p in node["points"] if p[1] == 0]
            out.append((node["h"], max(ys) - min(ys), r))
        return out

    for name, tree in CSG_PATTERNS:
        fl = floor(tree)
        assert fl and any(w >= 100 + 2 * r and d >= 110 + 2 * r for w, d, r in fl), (name, fl)


def test_csg_patterns_overlap_their_pieces_by_10_mm():
    """The prompt asks for 10 mm of overlap between stacked pieces that share the hollow; its own patterns comply."""
    from app.agent.prompt import CSG_PATTERNS

    tower = dict(CSG_PATTERNS)["tower on a plinth"]
    plinth, body = tower["nodes"]
    z0 = body["to"][2] if body["op"] == "move" else 0.0         # the tower stands on the table through its plinth
    assert plinth["h"] - z0 >= 10
