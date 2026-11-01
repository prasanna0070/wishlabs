"""End-to-end API flow with the scripted agent and the fake builder: the benchmark conversation, build, files,
edits, tweak, undo/redo, restore, order request, share and checklist."""
from __future__ import annotations

import io
import zipfile

from conftest import events_named, sse

PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."


def _new(authed):
    r = authed.post("/api/projects", json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _benchmark_to_ready(authed):
    pid = _new(authed)
    # 1. prompt -> questions with consequences
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"text": PROMPT})
    names = [e for e, _ in evs]
    assert names[0] == "turn.start" and names[-1] == "done" and "project" in names
    ch = events_named(evs, "choices")[0]["message"]
    qs = ch["payload"]["questions"]
    assert 1 <= len(qs) <= 3 and all(q["consequence"] and len(q["options"]) >= 2 for q in qs)
    assert "microphone" in ch["text"]
    assert ch["payload"]["defaults"] and "India" in ch["text"]
    assert events_named(evs, "project.renamed")[0]["name"] == "Story Time Bot"
    seqs = [d["seq"] for _, d in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # 2. answers -> concepts
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat",
              json={"answers": [{"question_id": qs[0]["id"], "value": "5 yo old"},
                                {"question_id": qs[1]["id"], "value": "yes"}]})
    user = events_named(evs, "turn.start")[0]["user_message"]
    assert user["text"] == "My choices:\n5 yo old, yes"
    cons = events_named(evs, "concepts")[0]["concepts"]
    assert len(cons) == 6 and all(c["state"] == "ready" and c["render_url"] for c in cons)
    assert [c["tier"] for c in cons] == ["expected", "expected", "different", "different", "wildcard", "wildcard"]
    tiers = events_named(evs, "concepts")[0]["message"]["payload"]["tiers"]
    assert {k: len(v) for k, v in tiers.items()} == {"expected": 2, "different": 2, "wildcard": 2}
    assert events_named(evs, "concepts.progress")
    png = authed.get(cons[0]["render_url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert "immutable" in png.headers["cache-control"]
    glb = authed.get(cons[0]["glb_url"])
    assert glb.content[:4] == b"glTF"
    # 3. pick a concept -> brief
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat",
              json={"action": "select_concept", "concept_id": cons[0]["id"]})
    assert events_named(evs, "turn.start")[0]["user_message"]["text"] == f"My choices:\n{cons[0]['title']}"
    brief = events_named(evs, "brief")[0]["brief"]
    assert brief["version"] == 1 and brief["functions"] and brief["parts"] and brief["size"]["w_mm"]
    assert brief["privacy_safety"]["value"] and brief["limits"] and not brief["approved"]
    assert brief["purpose"]["source"] == "user" and brief["power"]["source"] == "suggested"
    assert all(f["source"] in ("user", "suggested") and f["evidence"] for f in brief["functions"])
    assert brief["size"]["source"] == "suggested" and brief["parts"][0]["evidence"]
    # MAJ-28: the subject the user named comes first, quoted from the prompt; then the model's details
    assert brief["details"][0] == {"topic": "Subject", "value": "A robot", "source": "user",
                                   "evidence": 'You wrote: "a cute robot"'}
    assert brief["details"][1]["topic"] == "Sourcing country"
    p = authed.get(f"/api/projects/{pid}").json()
    assert p["stage"] == "brief" and p["selected_concept"] == cons[0]["id"]
    # 4. Build this -> progress -> result
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"action": "approve_brief"})
    assert events_named(evs, "build.started")[0]["version"]["n"] == 1
    progress = events_named(evs, "build.progress")
    assert progress and all("messages" in d["status"] and "draft_url" in d["status"] for d in progress)
    lines = events_named(evs, "build.message")
    assert len(lines) >= 8 and all(d["text"] for d in lines)
    res = events_named(evs, "result")[0]
    assert res["version"]["state"] == "done" and res["message"]["kind"] == "result"
    assert "Physical checks still needed:" in res["message"]["text"]
    final = events_named(evs, "project")[-1]["project"]
    assert final["stage"] == "ready" and final["current_version"] == 1 and final["brief"]["approved"]
    assert final["versions"][0]["brief_version"] == 1
    assert final["name"] == "Story Time Bot"
    st = authed.get(f"/api/projects/{pid}/v/1/status").json()
    assert st["draft_url"] is None and st["messages"]          # finished: the draft is no longer offered
    assert authed.get(f"/api/projects/{pid}/v/1/files/cad/draft.glb").content[:4] == b"glTF"
    assert authed.head(f"/api/projects/{pid}/v/1/files/build.json").status_code == 200
    return pid


def test_benchmark_flow_and_files(authed):
    pid = _benchmark_to_ready(authed)
    base = f"/api/projects/{pid}/v/1"
    b = authed.get(f"{base}/files/build.json")
    assert b.status_code == 200 and b.json()["schema"] == "gadget-build/1"
    assert "immutable" in b.headers["cache-control"]
    etag = b.headers["etag"]
    assert authed.get(f"{base}/files/build.json", headers={"If-None-Match": etag}).status_code == 304
    g = authed.get(f"{base}/files/cad/assembly.glb")
    assert g.status_code == 200 and g.headers["content-type"] == "model/gltf-binary" and g.content[:4] == b"glTF"
    st = authed.get(f"{base}/status")
    assert st.json()["state"] == "done" and st.headers["cache-control"] == "no-store"
    assert authed.get(f"{base}/files/%2e%2e/%2e%2e/project.json").status_code == 404
    assert authed.get(f"{base}/files/a/%2e%2e/%2e%2e/%2e%2e/inputs/v1.json").status_code == 404
    assert authed.get(f"{base}/files/nope.txt").status_code == 404
    ex = authed.get(f"{base}/export")
    assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(ex.content)).namelist()
    assert any(n.endswith("/build.json") for n in names)
    d = authed.get(base).json()
    assert d["n"] == 1 and d["status"]["state"] == "done" and d["files"]
    lst = authed.get("/api/projects").json()["projects"]
    assert lst[0]["id"] == pid and lst[0]["thumb_url"]


def test_edit_tweak_undo_restore(authed):
    pid = _benchmark_to_ready(authed)
    # chat edit -> update_design -> v2
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"text": "Make it pink please"})
    assert [d for d in events_named(evs, "tool.start") if d["name"] == "update_design"]
    assert events_named(evs, "result")[0]["version"]["n"] == 2
    spec2 = authed.get(f"/api/projects/{pid}/v/2/files/spec.json").json()
    assert spec2["colors"]["body"] == "#F2B5C4"
    # tweak validate_only, bad tweak, good tweak -> v3
    r = authed.post(f"/api/projects/{pid}/tweak", json={"patch": {"body": {"edge_round": 10}}, "validate_only": True})
    assert r.status_code == 200 and r.json()["ok"]
    r = authed.post(f"/api/projects/{pid}/tweak", json={"patch": {"features": {"no_such": {"at": [0, 1]}}}})
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"]
    r = authed.post(f"/api/projects/{pid}/tweak",
                    json={"patch": {"colors": {"body": "#9CC3E6", "body_name": "sky blue"}}, "scale": 1.05})
    assert r.status_code == 202, r.text
    assert r.json()["version"]["n"] == 3
    evs = sse(authed, "GET", f"/api/projects/{pid}/stream")
    assert events_named(evs, "result")[0]["version"]["n"] == 3
    p = authed.get(f"/api/projects/{pid}").json()
    tw = [m for m in p["chat"] if m["kind"] == "tweak"][0]
    assert tw["role"] == "user" and tw["payload"]["version"] == 3 and "sky blue" in tw["text"]
    assert p["current_version"] == 3 and p["versions"][2]["source"] == "tweak"
    # preview (no version)
    r = authed.post(f"/api/projects/{pid}/preview", json={"patch": {"body": {"edge_round": 9}}})
    assert r.status_code == 200 and r.json()["png_url"]
    assert authed.get(r.json()["png_url"]).content[:4] == b"\x89PNG"
    assert authed.post(f"/api/projects/{pid}/preview", json={"patch": {"body": {"edge_round": 9}}}).json()["cached"]
    # undo / redo / set current
    assert authed.post(f"/api/projects/{pid}/undo").json()["current_version"] == 2
    assert authed.post(f"/api/projects/{pid}/undo").json()["current_version"] == 1
    assert authed.post(f"/api/projects/{pid}/undo").json()["current_version"] == 1
    assert authed.post(f"/api/projects/{pid}/redo").json()["current_version"] == 2
    assert authed.put(f"/api/projects/{pid}/current", json={"n": 3}).json()["current_version"] == 3
    assert authed.put(f"/api/projects/{pid}/current", json={"n": 99}).status_code == 404
    # restore v1 -> v4 copies outputs
    r = authed.post(f"/api/projects/{pid}/versions/1/restore")
    assert r.status_code == 201 and r.json()["n"] == 4 and r.json()["restored_from"] == 1
    sse(authed, "GET", f"/api/projects/{pid}/stream")
    p = authed.get(f"/api/projects/{pid}").json()
    v4 = [v for v in p["versions"] if v["n"] == 4][0]
    assert v4["state"] == "done" and p["current_version"] == 4
    assert authed.get(f"/api/projects/{pid}/v/4/files/spec.json").json() == \
        authed.get(f"/api/projects/{pid}/v/1/files/spec.json").json()


def test_order_share_checklist(authed, client):
    pid = _benchmark_to_ready(authed)
    r = authed.post(f"/api/projects/{pid}/order-request", json={"pincode": "0123", "quantity": 1})
    assert r.status_code == 422
    r = authed.post(f"/api/projects/{pid}/order-request", json={"pincode": "500032", "quantity": 2, "note": "hi"})
    assert r.status_code == 201
    j = r.json()
    assert j["request"]["status"] == "requested" and j["request"]["quote"] == j["quote"]
    assert "not an order" in j["request"]["status_text"]
    assert authed.get(f"/api/projects/{pid}").json()["order_requests"][0]["pincode"] == "500032"
    # checklist
    r = authed.patch(f"/api/projects/{pid}/checklist", json={"assembly": {"print-shell": True}})
    assert r.json()["checklist"]["assembly"] == {"print-shell": True}
    # share
    sh = authed.post(f"/api/projects/{pid}/share").json()
    tok = sh["token"]
    assert sh["url"] == f"/studio/s/{tok}"           # the SPA lives under /studio (wish.plugbook.si/studio)
    assert authed.post(f"/api/projects/{pid}/share").json()["token"] == tok
    authed.post("/api/logout")
    client.cookies.clear()
    sp = client.get(f"/api/share/{tok}")
    assert sp.status_code == 200
    spj = sp.json()
    assert spj["read_only"] and "order_requests" not in spj and "share" not in spj
    c0 = spj["concepts"][0]
    assert c0["render_url"].startswith(f"/api/share/{tok}/")
    assert client.get(c0["render_url"]).status_code == 200
    assert client.get(f"/api/share/{tok}/v/1/files/build.json").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 401
    from conftest import KEY
    client.post("/api/login", json={"key": KEY})
    assert client.delete(f"/api/projects/{pid}/share").status_code == 200
    assert client.get(f"/api/share/{tok}").status_code == 404


def test_turn_conflict_and_bad_actions(authed):
    pid = _new(authed)
    r = authed.post(f"/api/projects/{pid}/chat", json={"action": "approve_brief"})
    assert r.status_code == 422
    r = authed.post(f"/api/projects/{pid}/chat", json={"text": ""})
    assert r.status_code == 422
    r = authed.post(f"/api/projects/{pid}/chat", json={"action": "select_concept", "concept_id": "c_nope"})
    assert r.status_code == 404
    store = authed.app.state.svc.store
    from app.util import now_iso

    store.update_json(f"projects/{pid}/project.json", lambda p: p.update(turn={"id": "t_x", "started": now_iso()}))
    r = authed.post(f"/api/projects/{pid}/chat", json={"text": "hello"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_project_crud(authed):
    pid = _new(authed)
    assert authed.patch(f"/api/projects/{pid}", json={"name": "Lullaby Pod"}).json()["name"] == "Lullaby Pod"
    assert authed.patch(f"/api/projects/{pid}", json={"mode": "fast"}).json()["mode"] == "fast"
    assert authed.patch(f"/api/projects/{pid}", json={"mode": "x"}).status_code == 422
    assert authed.get("/api/projects/p_doesnotexist").status_code == 404
    assert authed.delete(f"/api/projects/{pid}").status_code == 200
    assert authed.get(f"/api/projects/{pid}").status_code == 404
    assert pid not in [p["id"] for p in authed.get("/api/projects").json()["projects"]]
    assert authed.get("/api/nope").status_code == 404


def test_part_edit_and_skip_paths(authed):
    pid = _benchmark_to_ready(authed)
    comps = authed.get(f"/api/projects/{pid}/v/1/files/cad/components.json").json()
    comp = next(k for k, v in comps.items() if str(v.get("spec_path", "")).startswith("features["))
    path = comps[comp]["spec_path"]
    idx = int(path[len("features["):-1])
    before = authed.get(f"/api/projects/{pid}/v/1/files/spec.json").json()["features"][idx]["at"]
    evs = sse(authed, "POST", f"/api/projects/{pid}/parts/{comp}/edit", json={"instruction": "move it up a bit"})
    um = events_named(evs, "turn.start")[0]["user_message"]
    assert um["payload"]["action"] == "edit_part" and um["payload"]["spec_path"] == path
    res = events_named(evs, "result")[0]
    assert res["version"]["n"] == 2 and res["version"]["label"].startswith("Edit ")
    after = authed.get(f"/api/projects/{pid}/v/2/files/spec.json").json()["features"][idx]["at"]
    assert after[1] == before[1] + 5 and after[0] == before[0]
    r = authed.post(f"/api/projects/{pid}/parts/NOPE_Thing/edit", json={"instruction": "x"})
    assert r.status_code == 404
    r = authed.post(f"/api/projects/{pid}/parts/{comp}/edit", json={})
    assert r.status_code == 422


def test_skip_questions_and_continue_without_image(authed):
    pid = _new(authed)
    sse(authed, "POST", f"/api/projects/{pid}/chat", json={"text": PROMPT})
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"answers": [], "skipped": True})
    assert events_named(evs, "turn.start")[0]["user_message"]["text"] == "My choices:\nUse your suggestions"
    cons = events_named(evs, "concepts")[0]["concepts"]
    evs = sse(authed, "POST", f"/api/projects/{pid}/chat", json={"action": "skip_concepts"})
    assert events_named(evs, "turn.start")[0]["user_message"]["text"] == "My choices:\nContinue without an image"
    p = authed.get(f"/api/projects/{pid}").json()
    assert p["selected_concept"] == cons[0]["id"] and p["stage"] == "brief" and p["brief"]


def test_enrich_status_draft_and_lines(tmp_path):
    from app.builds import enrich_status
    from app.store import LocalStore

    st = LocalStore(tmp_path)
    running = {"state": "running", "stages": [{"id": "cad", "label": "Shell and CAD", "state": "running"}]}
    e = enrich_status(st, "p_x", 1, running)
    assert e["draft_url"] is None and e["messages"][0]["text"] == "Working on shell and cad." and e["messages"][0]["derived"]
    st.write_bytes("projects/p_x/v1/cad/draft.glb", b"glTF....")
    assert enrich_status(st, "p_x", 1, running)["draft_url"].startswith("/api/projects/p_x/v/1/files/cad/draft.glb?rev=")
    b = enrich_status(st, "p_x", 1, {**running, "draft": "cad/draft.glb", "draft_rev": 7,
                                     "messages": [{"at": "t", "stage": "cad", "text": "Modelled the shell."}]})
    assert b["draft_url"].endswith("?rev=7") and b["messages"] == [{"text": "Modelled the shell.", "ts": "t", "stage": "cad"}]
    assert enrich_status(st, "p_x", 1, {"state": "done"})["draft_url"] is None


def test_maj35_decor_pick_is_scoped_to_its_spec_path(authed):
    """REVIEW-1 MAJ-35 (integration): the viewer selects PRINT_Shell#decor[i]; the edit turn is scoped to decor[i] with its label."""
    import urllib.parse

    pid = _benchmark_to_ready(authed)
    store = authed.app.state.svc.store
    key = f"projects/{pid}/v1/cad/components.json"
    comps = store.read_json(key) or {}
    shell = next((k for k in comps if k.startswith("PRINT_Shell")), "PRINT_Shell")
    comps.setdefault(shell, {"kind": "PRINT", "label": "main shell"})
    comps[shell]["decor"] = [{"name": f"{shell}#decor[0]", "spec_path": "decor[0]", "label": "face plate"},
                             {"name": f"{shell}#decor[1]", "spec_path": "decor[1]", "label": "ears"}]
    store.write_json(key, comps)
    name = urllib.parse.quote(f"{shell}#decor[1]", safe="")
    evs = sse(authed, "POST", f"/api/projects/{pid}/parts/{name}/edit", json={"instruction": "make them rounder"})
    um = events_named(evs, "turn.start")[0]["user_message"]
    assert um["payload"]["action"] == "edit_part"
    assert um["payload"]["spec_path"] == "decor[1]" and um["payload"]["label"] == "ears"
    assert um["text"].startswith("Edit ears")
