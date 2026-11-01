"""REVIEW-1 web-backend items through the API (scripted agent, fake builder): BLK-4 story-audio route, MAJ-38
export extras, MIN-37 partial tweak plan, MAJ-36 brief revisions, MAJ-8 nudges, MAJ-33 name clips, MIN-42 and
MAJ-28 message payloads, MAJ-34 scoped edits."""
from __future__ import annotations

import array
import copy
import io
import json
import math
import wave
import zipfile
from pathlib import Path

from conftest import events_named, sse
from test_api_flow import PROMPT, _benchmark_to_ready, _new

FX = Path(__file__).parent / "fixtures" / "e2e"


def _store(authed):
    return authed.app.state.svc.store


def test_clarify_layout_and_concept_set_check(authed):
    pid = _new(authed)
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"text": PROMPT})
    ch = events_named(evs, "choices")[0]["message"]
    for q in ch["payload"]["questions"]:
        assert f"**{q['question']}**\n{q['consequence']}" in ch["text"]          # MIN-42
    qs = ch["payload"]["questions"]
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat",
              json={"answers": [{"question_id": qs[0]["id"], "value": "5 yo old"},
                                {"question_id": qs[1]["id"], "value": "yes"}]})
    msg = events_named(evs, "concepts")[0]["message"]
    sc = msg["payload"]["set_check"]
    assert msg["payload"]["subject"] == "robot"
    # the scripted set reuses one layout, so the rules ask for repairs, then the set is shown with the failures kept
    assert sc["ok"] is False and {"layout"} <= {f["rule"] for f in sc["failures"]}
    assert len(events_named(evs, "repair")) >= 1


def test_blk4_story_audio_route(authed):
    pid = _benchmark_to_ready(authed)
    spec = authed.get(f"/api/projects/{pid}/v/1/files/spec.json").json()
    assert spec["behaviour"]["template"] == "story_player"
    st1 = authed.get(f"/api/projects/{pid}/v/1/status").json()
    assert st1["options"]["stories_audio"] is True                              # default for story_player
    r = authed.post(f"/api/projects/{pid}/versions/1/story-audio")
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["version"]["n"] == 2 and j["version"]["source"] == "story_audio" and j["version"]["base_version"] == 1
    assert j["plan"]["mode"] == "partial" and "firmware" in j["plan"]["only"]
    assert not {"cad", "pcb", "order", "manual"} & set(j["plan"]["only"])
    sse(authed, "GET", f"/api/projects/{pid}/stream")
    st = authed.get(f"/api/projects/{pid}/v/2/status").json()
    assert st["options"]["stories_audio"] is True and st["options"]["only"] == j["plan"]["only"]
    inp = _store(authed).read_json(f"projects/{pid}/inputs/v2.json")
    assert inp["seed_from"] == 1 and inp["options"]["stories_audio"] is True
    # a version that already has audio is not rebuilt; a design without a story card is refused
    _store(authed).write_json(f"projects/{pid}/v2/firmware/sdcard/stories.json", {"audio": True})
    r = authed.post(f"/api/projects/{pid}/versions/2/story-audio")
    assert r.status_code == 200 and r.json()["already"]
    s3 = copy.deepcopy(spec)
    s3["behaviour"]["template"] = "ambient_light"
    _store(authed).write_json(f"projects/{pid}/inputs/v1.json", {"spec": s3})
    assert authed.post(f"/api/projects/{pid}/versions/1/story-audio").status_code == 409
    assert authed.post(f"/api/projects/{pid}/versions/9/story-audio").status_code == 404


def test_maj38_export_has_concept_references_and_project_record(authed):
    pid = _benchmark_to_ready(authed)
    p = authed.get(f"/api/projects/{pid}").json()
    cid = p["brief"]["concept_id"]
    man = authed.get(f"/api/projects/{pid}/v/1/files/manifest.json").json()
    paths = {f["path"] for f in man["files"]}
    assert f"references/concept-{cid}-render.png" in paths and "references/concept.json" in paths
    ex = authed.get(f"/api/projects/{pid}/v/1/export")
    z = zipfile.ZipFile(io.BytesIO(ex.content))
    names = z.namelist()
    top = names[0].split("/")[0]
    assert f"{top}/references/concept-{cid}-render.png" in names and f"{top}/references/concept.json" in names
    assert "project-record.json" in names and f"{top}/project-record.json" not in names     # outside the manifest
    rec = json.loads(z.read("project-record.json"))
    assert rec["exported_version"] == 1 and rec["chat"][0]["text"] == PROMPT
    assert rec["brief_history"][0]["version"] == 1 and rec["versions"][0]["label"]
    inner = json.loads(z.read(f"{top}/manifest.json"))
    assert {f["path"] for f in inner["files"]} == paths
    cj = json.loads(z.read(f"{top}/references/concept.json"))
    assert cj["id"] == cid and cj["tier"] and "AI image" in cj["note"]


def test_min37_colour_tweak_reuses_pcb_and_firmware_and_maj36_brief_v2(authed):
    pid = _benchmark_to_ready(authed)
    svc = authed.app.state.svc
    spec = svc.store.read_json(f"projects/{pid}/inputs/v1.json")["spec"]
    el = svc.bridge.electrical_for(spec)
    assert el is not None, "engine electrical not importable"
    svc.store.write_json(f"projects/{pid}/v1/electrical.json", el)
    body = {"patch": {"colors": {"body": "#9CC3E6", "body_name": "sky blue"}}}
    r = authed.post(f"/api/projects/{pid}/tweak", json={**body, "validate_only": True})
    plan = r.json()["plan"]
    assert plan["mode"] == "partial" and plan["skip"] == ["pcb", "firmware"] and plan["colour_only"]
    assert "pcb" not in plan["only"] and "cad" in plan["only"]
    assert r.json()["check_note"].startswith("Check runs the layout rules")
    r = authed.post(f"/api/projects/{pid}/tweak", json=body)
    assert r.status_code == 202 and r.json()["plan"]["mode"] == "partial"
    sse(authed, "GET", f"/api/projects/{pid}/stream")
    st = authed.get(f"/api/projects/{pid}/v/2/status").json()
    assert st["options"]["only"] == plan["only"] and st["options"]["recolor_only"] is True
    p = authed.get(f"/api/projects/{pid}").json()
    v2 = next(v for v in p["versions"] if v["n"] == 2)
    assert v2["brief_version"] == 2
    assert any(c.startswith("body colour ") and c.endswith("→#9CC3E6") for c in v2["changes"]), v2["changes"]
    assert [b["version"] for b in p["brief_history"]] == [1, 2]
    b2 = p["brief_history"][1]
    ch = b2["details"][-1]
    assert ch["topic"] == "Change in v2" and ch["source"] == "user" and ch["evidence"].startswith("Direct edit v2:")
    assert b2["spec_hash"].startswith("sha256:") and b2["auto_for_version"] == 2
    # a feature move changes the wiring lengths or not; either way the plan says why
    r = authed.post(f"/api/projects/{pid}/tweak", json={"patch": {"body": {"edge_round": 10}}, "validate_only": True})
    assert r.json()["plan"]["reason"]


def test_maj8_nudges_from_the_base_cad():
    from app.plan import base_nudges

    spec = json.loads((FX / "v3-spec.json").read_text())
    nud = base_nudges(json.loads((FX / "v3-cad-digital-checks.json").read_text()),
                      json.loads((FX / "v3-components.json").read_text()), spec, None, 3)
    led = next(n for n in nud if set(n["parts"]) == {"PRINT_SpeakerRing", "PURCHASE_Led"})
    assert led["feature"] == "status_light" and led["direction"] == "up" and led["move_mm"] == 7
    assert led["message"].startswith("Night light touches the speaker clamp ring")
    moved = copy.deepcopy(spec)
    i = next(k for k, f in enumerate(moved["features"]) if f["id"] == "status_light")
    moved["features"][i]["at"] = [moved["features"][i]["at"][0], moved["features"][i]["at"][1] + 8]
    again = base_nudges(json.loads((FX / "v3-cad-digital-checks.json").read_text()),
                        json.loads((FX / "v3-components.json").read_text()), spec, moved, 3)
    assert next(n for n in again if set(n["parts"]) == {"PRINT_SpeakerRing", "PURCHASE_Led"})["resolved"]


def _sine_wav(seconds=0.6, rate=24000):
    pcm = array.array("h", [0] * int(0.2 * rate) + [int(9000 * math.sin(i / 7)) for i in range(int(seconds * rate))]
                      + [0] * int(0.3 * rate))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def test_maj33_name_clip_endpoints(authed, client):
    pid = _benchmark_to_ready(authed)
    calls = []
    authed.app.state.svc.names._synth = lambda text, style, voice, model: calls.append((text, voice)) or _sine_wav()
    r = authed.post(f"/api/projects/{pid}/name-audio", json={"name": "Chhota Bheem"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav", r.text
    assert r.headers["x-gs-slug"] == "chhota_bheem" and len(r.content) <= 64 * 1024
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getnframes() < 0.75 * w.getframerate()
    assert calls == [("Chhota Bheem", "Achernar")]
    authed.post(f"/api/projects/{pid}/name-audio", json={"name": "Chhota Bheem"})
    assert len(calls) == 1                                                  # in-memory cache
    assert authed.post(f"/api/projects/{pid}/name-audio", json={"name": "<script>"}).status_code == 422
    tok = authed.post(f"/api/projects/{pid}/device-token").json()["token"]
    assert authed.post(f"/api/projects/{pid}/device-token").json()["token"] == tok
    authed.post("/api/logout")
    client.cookies.clear()
    assert client.post(f"/api/projects/{pid}/name-audio", json={"name": "Anya"}).status_code == 401
    r = client.post(f"/api/device/{tok}/name-audio", json={"name": "Anya"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*" and r.content[:4] == b"RIFF"
    assert client.options(f"/api/device/{tok}/name-audio").status_code == 204
    assert client.post("/api/device/d_nope/name-audio", json={"name": "Anya"}).status_code == 404
    from conftest import KEY
    client.post("/api/login", json={"key": KEY})
    share = client.post(f"/api/projects/{pid}/share").json()["token"]
    assert "device" not in client.get(f"/api/share/{share}").json()


def test_maj34_scoped_edit_stops_on_second_out_of_scope_change(authed):
    from app.agent.turn import ToolResult  # noqa: F401

    pid = _benchmark_to_ready(authed)
    svc = authed.app.state.svc

    class Sloppy:
        kind = "test"

        def __init__(self):
            self.calls = 0

        async def run(self, turn):
            base = svc.store.read_json(f"projects/{pid}/inputs/v1.json")["spec"]
            for _ in range(3):
                s = copy.deepcopy(base)
                s["body"]["edge_round"] = (s["body"].get("edge_round") or 8) + 4      # never asked for
                i = next(k for k, d in enumerate(s["decor"]) if d["type"] in ("ear", "mouth", "face_plate"))
                s["decor"][i]["size"] = [x + 4 for x in s["decor"][i]["size"]]
                self.calls += 1
                res = await turn.call_tool("update_design", {"spec": s, "change_summary": "Rounder ears",
                                                             "message": "I've made the ears bigger and rounder."})
                if res.terminal:
                    return

    brain = Sloppy()
    svc.brain = brain
    comps = svc.store.read_json(f"projects/{pid}/v1/cad/components.json")
    shell = next(k for k, v in comps.items() if v.get("spec_path") == "body")
    evs = sse(authed, "POST", f"/api/projects/{pid}/parts/{shell}/edit", json={"instruction": "make it rounder"})
    assert brain.calls == 2 and not events_named(evs, "build.started")
    last = [d["message"] for d in events_named(evs, "message.done") if d["message"]["role"] == "assistant"][-1]
    assert "which you did not ask for" in last["text"] and "Should I change that too?" in last["text"]
    assert len(authed.get(f"/api/projects/{pid}").json()["versions"]) == 1


def test_maj34_chat_edit_lists_exact_changes(authed):
    pid = _benchmark_to_ready(authed)
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"text": "Make it pink please"})
    started = events_named(evs, "build.started")[0]
    assert started["version"]["label"].startswith("Edit: body colour ") and "→#F2B5C4" in started["version"]["label"]
    assert started["version"]["changes"] and all("also changed" not in c for c in started["version"]["changes"])


def test_maj1_safety_rule_in_prompt(tmp_path):
    from app.agent.prompt import PromptBuilder
    from app.config import Settings
    from app.engine_bridge import EngineBridge

    from conftest import make_settings
    s: Settings = make_settings(tmp_path)
    text = PromptBuilder(EngineBridge(s), s).system_prompt()
    assert '"parent_web"' in text and "child_voice_clip" in text and "CHILD_VOICE_STORED" in text
    assert "raw audio is never stored, and nothing" not in text           # the old promise is gone
    assert "moon, owl, cloud, bear, bean" not in text and "5 yo old" not in text
    assert "keep the subject the user named" in text.lower()


def test_maj33_story_builds_carry_the_name_clip_url(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from conftest import KEY, make_settings

    s = make_settings(tmp_path, public_url="https://studio.example.test")
    with TestClient(create_app(s)) as c:
        c.post("/api/login", json={"key": KEY})
        pid = _benchmark_to_ready(c)
        st = c.get(f"/api/projects/{pid}/v/1/status").json()
        tok = c.get(f"/api/projects/{pid}").json()
        assert "device" in tok and tok["device"]["token"].startswith("d_")
        assert st["options"]["name_audio_url"] == f"https://studio.example.test/api/device/{tok['device']['token']}/name-audio"
        assert c.post(f"/api/projects/{pid}/device-token").json()["token"] == tok["device"]["token"]


def test_fit_rules_tell_the_agent_about_the_night_light_ring(tmp_path):
    from app.engine_bridge import EngineBridge

    from conftest import make_settings
    br = EngineBridge(make_settings(tmp_path))
    ring = getattr(br.norm_mod, "NIGHT_LIGHT_RING", None) if br.complete else None
    text = br.fit_rules_text()
    if ring:
        assert ring in text and "NIGHT_USE" in text
    else:
        assert "Night light" not in text


def test_min43_more_concepts_after_a_build_and_min45_waits(authed):
    pid = _benchmark_to_ready(authed)
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"action": "more_concepts"})
    cons = events_named(evs, "concepts")[0]["concepts"]
    assert len(cons) == 6
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"action": "select_concept", "concept_id": cons[1]["id"]})
    assert events_named(evs, "brief")
    p = authed.get(f"/api/projects/{pid}").json()
    assert p["stage"] == "brief" and p["brief"]["concept_id"] == cons[1]["id"]
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"action": "approve_brief"})
    assert events_named(evs, "result")[0]["version"]["n"] == 2
    row = next(r for r in authed.get("/api/projects").json()["projects"] if r["id"] == pid)
    assert isinstance(row["last_build_seconds"], int) and isinstance(row["last_concepts_seconds"], int)


def test_maj36_hash_basis_and_min37_pcb_reuse(authed):
    pid = _benchmark_to_ready(authed)
    p = authed.get(f"/api/projects/{pid}").json()
    v1 = p["versions"][0]
    assert v1["spec_hash"] == p["brief"]["spec_hash"]                 # same basis: the input spec
    assert v1["output_spec_hash"].startswith("sha256:")
    r = authed.post(f"/api/projects/{pid}/tweak", json={"patch": {"body": {"edge_round": 10}}})
    assert r.status_code == 202
    sse(authed, "GET", f"/api/projects/{pid}/stream")
    st = authed.get(f"/api/projects/{pid}/v/2/status").json()
    assert st["options"]["pcb_reuse_from"].endswith(f"/projects/{pid}/v1")


def test_maj8_engine_nudges_mapped():
    from app.config import Settings
    from app.engine_bridge import EngineBridge
    from app.plan import from_engine

    spec = json.loads((FX / "v3-spec.json").read_text())
    rows = EngineBridge(Settings()).nudges_from_cad(spec, json.loads((FX / "v3-components.json").read_text()),
                                                    json.loads((FX / "v3-cad-digital-checks.json").read_text()))
    if rows is None:
        return                                   # engine without nudges_from_cad: base_nudges covers it
    nud = from_engine(rows, spec, None, 3)
    led = next(n for n in nud if n["feature"] == "status_light")
    assert led["direction"] == "up" and led["move_mm"] >= 5 and "Night light" in led["message"]


def test_prompt_shows_several_example_forms_and_keys_are_sanitised(tmp_path):
    from app.agent.prompt import MAX_EXAMPLES, PromptBuilder
    from app.agent.turn import _plain
    from app.engine_bridge import EngineBridge

    from conftest import make_settings
    s = make_settings(tmp_path)
    pb = PromptBuilder(EngineBridge(s), s)
    paths = pb.example_paths()
    assert 1 <= len(paths) <= MAX_EXAMPLES and paths[0].parent.name == "bedtime-robot"
    text = pb.system_prompt()
    assert text.count("\n## ") >= min(len(paths), 2) and "Range widely within the subject" in text
    assert _plain({"center\"": [1, 2, 3], " size ": [4, 5, 6]}) == {"center": [1, 2, 3], "size": [4, 5, 6]}


def test_min37_time_estimate_uses_measured_stage_times_only():
    from app.plan import reuse_plan, wall_estimate

    v3 = {"stages": [{"id": i, "state": "done", "seconds": s} for i, s in
                     (("normalize", .2), ("electrical", 0), ("cad", 96.9), ("pcb", 57.1), ("firmware", 4.2),
                      ("order", 9.0), ("manual", 20.9), ("checks", 1.6), ("docs", 0), ("package", 3.8))]}
    full = wall_estimate({x["id"]: x["seconds"] for x in v3["stages"]}, {x["id"] for x in v3["stages"]})
    assert full == 132.4                                    # cad 97 -> order -> manual is the critical path
    partial_only = {"stages": [{"id": "firmware", "state": "done", "seconds": 14.7}]}
    ids = tuple(x["id"] for x in v3["stages"])
    el = {"nets": 1}
    plan = reuse_plan(ids, {"colors": {"body": "#1"}}, {"colors": {"body": "#2"}}, el, el, [partial_only], 2)
    assert plan["mode"] == "partial" and plan["expected_seconds"] is None        # cad never measured: no guess
    plan = reuse_plan(ids, {"colors": {"body": "#1"}}, {"colors": {"body": "#2"}}, el, el, [partial_only, v3], 2)
    # pcb (57 s) runs beside cad, so skipping it does not shorten the wall time: MIN-37 needs a CAD recolour path
    assert plan["expected_seconds"] == 132.4 and plan["full_seconds"] == 132.4
