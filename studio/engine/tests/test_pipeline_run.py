"""The pipeline scheduler with fake stages: ordering, parallel processes, failures, crashes, timeouts, --only,
status.json, per-stage logs, and the real order/checks/docs/package stages on a tiny fake design.

Runs on Linux (forked stage processes), i.e. on the VM / builder.
"""
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from gstudio import pipeline
from gstudio.pipeline import _FallbackContext, build, parse_only

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="stage processes are forked (Linux)")

FAKES = {
    "electrical": """
        import json
        def run(ctx):
            e = {"schema": "gadget-electrical/1", "gpio": {"BTN1": 4, "LED_DIN": 5}, "instances": [],
                 "power": {"ok": True, "total_ma_max": 400, "limit_ma": 1500}}
            (ctx.out / "electrical.json").write_text(json.dumps(e))
            ctx.electrical = e
            return {"message": "2 signals"}
    """,
    "cad": """
        import json, struct, time
        def box(path, sx, sy, sz):
            x0, x1, y0, y1 = -sx/2, sx/2, -sy/2, sy/2
            v = [(x0,y0,0),(x1,y0,0),(x1,y1,0),(x0,y1,0),(x0,y0,sz),(x1,y0,sz),(x1,y1,sz),(x0,y1,sz)]
            f = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
            b = bytearray(80) + struct.pack("<I", len(f))
            for t in f:
                b += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v[i]) for i in t) + b"\\0\\0"
            path.write_bytes(bytes(b))
        def run(ctx):
            ctx.progress("cad", "modelling", 30)
            (ctx.out / "cad").mkdir(exist_ok=True)
            tmp = ctx.out / "cad" / ".draft.glb.tmp"
            tmp.write_bytes(b"glTF-draft-1")
            tmp.replace(ctx.out / "cad" / "draft.glb")
            ctx.say("cad", "Shaped the test body")
            time.sleep(1.3)  # longer than the status throttle and the draft settle time
            (ctx.out / "print").mkdir(exist_ok=True)
            box(ctx.out / "print" / "PRINT_Shell.stl", 60, 50, 40)
            (ctx.out / "cad").mkdir(exist_ok=True)
            comps = {"PRINT_Shell": {"kind": "PRINT", "bbox": [-30, 30, -25, 25, 0, 40], "volume_mm3": 120000.0},
                     "PURCHASE_CoreBoard": {"kind": "PURCHASE", "module_id": "core_mod", "ref": "U1", "bbox": [-10, 10, -5, 5, 2, 8]}}
            (ctx.out / "cad" / "components.json").write_text(json.dumps(comps))
            fw = ctx.out / "firmware"
            fw.mkdir(exist_ok=True)
            (fw / "firmware-merged.bin").write_bytes(bytes([0xE9]) * 64)
            (fw / "web-flasher-manifest.json").write_text(json.dumps({"name": "x", "builds": [{"chipFamily": "ESP32-S3", "parts": [{"path": "firmware-merged.bin", "offset": 0}]}]}))
            ctx.artifacts["cad.components"] = ctx.out / "cad" / "components.json"
            ctx.progress("cad", "exporting", 90)
            return {"message": "1 print part", "checks": [{"id": "valid", "title": "valid solids", "status": True}]}
    """,
    "pcb_fail": """
        from gstudio.errors import StageError
        def run(ctx):
            raise StageError("router left 3 nets unrouted", {"unrouted": 3})
    """,
    "firmware_print": """
        import subprocess, time, sys
        def run(ctx):
            print("hello from python stdout")
            subprocess.run(["sh", "-c", "echo hello from a subprocess; echo err-line 1>&2"])
            time.sleep(0.3)
            return {"message": "compiled", "firmware": {"compiled": True, "tests_passed": 5, "tests_total": 5}}
    """,
    "manual_norun": """
        X = 1
    """,
    "crash": """
        import os, signal
        def run(ctx):
            os.kill(os.getpid(), signal.SIGSEGV)
    """,
    "slow": """
        import subprocess, time
        def run(ctx):
            subprocess.Popen(["sleep", "61.4321"])  # unique marker: tests assert no such grandchild survives
            time.sleep(60)
    """,
    "cad_collide": """
        import json
        from gsfake.cad import box
        def run(ctx):
            (ctx.out / "print").mkdir(exist_ok=True)
            (ctx.out / "cad").mkdir(exist_ok=True)
            box(ctx.out / "print" / "PRINT_Shell.stl", 60, 50, 40)
            comps = {"PRINT_Shell": {"kind": "PRINT", "bbox": [-30, 30, -25, 25, 0, 40], "volume_mm3": 120000.0},
                     "PURCHASE_CoreBoard": {"kind": "PURCHASE", "module_id": "core_mod", "ref": "U1", "bbox": [-10, 10, -5, 5, 2, 8]}}
            (ctx.out / "cad" / "components.json").write_text(json.dumps(comps))
            dc = {"checks": [{"id": "collisions", "title": "no unintended intersection", "status": "failed", "detail": "1 unintended",
                              "data": {"intersections": [{"parts": ["PRINT_Shell", "PURCHASE_CoreBoard"], "intersection_mm3": 12.5}]}}]}
            (ctx.out / "cad" / "digital-checks.json").write_text(json.dumps(dc))
            return {"message": "collides", "checks": dc["checks"]}
    """,
    "cad_fixable": """
        import json
        from gsfake.cad import box
        def run(ctx):
            sp = ctx.spec.to_json_dict(True)
            v = next(f for f in sp["features"] if f["id"] == "light")["at"][1]
            (ctx.out / "print").mkdir(exist_ok=True)
            (ctx.out / "cad").mkdir(exist_ok=True)
            box(ctx.out / "print" / "PRINT_Shell.stl", 60, 50, 40)
            comps = {"PRINT_Shell": {"kind": "PRINT", "bbox": [-30, 30, -25, 25, 0, 40], "volume_mm3": 120000.0},
                     "PURCHASE_CoreBoard": {"kind": "PURCHASE", "module_id": "core_mod", "ref": "U1", "bbox": [-10, 10, -5, 5, 2, 8]},
                     "PRINT_SpeakerRing": {"kind": "PRINT", "feature_id": "speaker", "bbox": [-25, 25, -50, -42, 36.4, 86.1]},
                     "PURCHASE_Led": {"kind": "PURCHASE", "feature_id": "light", "bbox": [-5, 5, -48, -42, v - 3.4, v + 6.6]}}
            (ctx.out / "cad" / "components.json").write_text(json.dumps(comps))
            hit = v - 3.4 < 86.1
            dc = {"checks": [{"id": "collisions", "title": "no unintended intersection", "status": "failed" if hit else "passed",
                              "detail": "x", "data": {"intersections": [{"parts": ["PRINT_SpeakerRing", "PURCHASE_Led"], "intersection_mm3": 31.5}]
                                                      if hit else []}}]}
            (ctx.out / "cad" / "digital-checks.json").write_text(json.dumps(dc))
            ctx.say("cad", f"cad run with light at {v}")
            return {"message": f"light at {v}", "checks": dc["checks"]}
    """,
    "manual_final": """
        import json
        def run(ctx):
            (ctx.out / "BUILD_GUIDE.md").write_text("# Guide\\n\\n## Final checks to record\\n\\n(placeholder)\\n")
            return {"message": "guide", "steps": 1, "images": ["manual/a.png", "manual/w.svg"]}
        def finalize(ctx):
            dc = json.loads((ctx.out / "checks" / "digital-checks.json").read_text())
            b = json.loads((ctx.out / "build.json").read_text())
            lines = "\\n".join(f"- **{t['title']}:** {t.get('status')}" for t in b.get("tests") or [])
            (ctx.out / "BUILD_GUIDE.md").write_text("# Guide\\n\\n## Final checks to record\\n\\n" + lines + "\\n\\n## End\\n")
            return {"message": "finalized", "tests": len(b.get("tests") or []), "checks_seen": len(dc["checks"])}
    """,
    "cad_fail": """
        def run(ctx):
            raise RuntimeError("boolean union failed")
    """,
    "cad_partial": """
        import json
        from gsfake.cad import box
        def run(ctx):
            (ctx.out / "print").mkdir(exist_ok=True)
            box(ctx.out / "print" / "PRINT_Shell.stl", 60, 50, 40)
            raise RuntimeError("export failed after the STL was written")
    """,
}


class FakeLib:
    MODS = {"core_mod": {"id": "core_mod", "name": "Core board", "price_inr_planning": [650, 1300], "requires_soldering": False,
                         "sourcing_in": [{"vendor": "Robu", "url": "https://robu.in/c", "price_inr": 999, "seen_on": "2026-11-01"}]}}

    def get(self, mid):
        return self.MODS[mid]

    def catalog_id_for_price(self, mid):
        return None


SPEC = {"schema": "gadget-spec/1", "name": "Fake Gadget", "slug": "fake-gadget", "audience": {"child_directed": False},
        "colors": {"body": "#8CC9C0", "body_name": "pale teal"}, "behaviour": {"template": "desk_companion", "summary": "test"},
        "electronics": {"core": "core_mod", "power": {"input": "usb_c_5v", "adapter_in_box": False}}, "safety": {"flags": []},
        "derived": {"instances": [{"ref": "U1", "module": "core_mod", "role": "core", "source": "core"}], "harness": [], "hardware": []}}


class FakeSpec:
    def __init__(self, d):
        self.d = d
        self.derived = None

    def to_json_dict(self, include_derived=True):
        return json.loads(json.dumps(self.d))


def fake_context(spec, out, options, on_progress):
    return _FallbackContext(FakeSpec(SPEC), FakeLib(), out, pipeline._public_options(options), on_progress)


@pytest.fixture(scope="module")
def fakes(tmp_path_factory):
    root = tmp_path_factory.mktemp("fakes")
    pkg = root / "gsfake"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, src in FAKES.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src))
    sys.path.insert(0, str(root))
    yield "gsfake"
    sys.path.remove(str(root))


def opts(fakes, **kw):
    mods = {"electrical": f"{fakes}.electrical", "cad": f"{fakes}.cad", "pcb": f"{fakes}.pcb_fail",
            "firmware": f"{fakes}.firmware_print", "manual": f"{fakes}.manual_norun"}
    mods.update(kw.pop("mods", {}))
    return {"_context": fake_context, "stage_modules": mods, "fast": True, "price_check": "off", **kw}


def states(summary):
    return {k: v["state"] for k, v in summary["stages"].items()}


def test_parse_only():
    assert parse_only("cad, pcb") == ["cad", "pcb"]
    assert parse_only(["docs_gen"]) == ["docs"]
    assert parse_only(None) is None
    with pytest.raises(ValueError):
        parse_only("cad,nope")


def test_full_run_with_failures_is_robust(fakes, tmp_path):
    seen = []
    t0 = time.time()
    s = build({}, tmp_path, opts(fakes), lambda st: seen.append(st))
    st = states(s)
    assert st == {"normalize": "done", "electrical": "done", "cad": "done", "pcb": "failed", "firmware": "done",
                  "order": "done", "manual": "skipped", "checks": "done", "finalize": "skipped", "docs": "done", "package": "done"}, s["stages"]["order"]
    assert s["state"] == "error" and "pcb" in s["error"] and "unrouted" in s["error"]
    assert s["stages"]["pcb"]["details"] == {"unrouted": 3}
    assert "no run(ctx)" in s["stages"]["manual"]["message"]
    assert time.time() - t0 < 120

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "error" and status["failed"] == ["pcb"]
    assert {x["id"] for x in status["stages"]} == set(pipeline.STAGE_IDS)
    for x in status["stages"]:
        assert set(x) >= {"id", "label", "state", "started", "ended", "message", "percent"}
    pcts = [x["percent"] for x in seen]
    assert len(seen) >= 20 and pcts == sorted(pcts)
    assert any(any(stg["id"] == "cad" and stg["message"] == "modelling" for stg in x["stages"]) for x in seen)

    # live draft + chat lines (coordinator additions)
    assert status["draft"] == "cad/draft.glb" and status["draft_rev"] >= 1
    texts = [m["text"] for m in status["messages"]]
    assert "Shaped the test body" in texts                         # ctx.say from a child process
    assert any(t.startswith("Backplane PCB failed: router left 3 nets") for t in texts)
    assert any(t.startswith("Firmware compiled") and "5/5 unit tests passed" in t for t in texts)
    assert any(t.startswith("Sliced the print parts") for t in texts)
    assert all(set(m) == {"at", "stage", "text"} for m in status["messages"])

    log = (tmp_path / "logs" / "firmware.log").read_text()
    assert "hello from python stdout" in log and "hello from a subprocess" in log and "err-line" in log
    assert "router left 3 nets unrouted" in (tmp_path / "logs" / "pcb.log").read_text()
    for sid in ("normalize", "electrical", "cad", "order", "checks", "docs", "package"):
        assert (tmp_path / "logs" / f"{sid}.log").exists(), sid

    # outputs from the real order / checks / docs / package stages
    b = json.loads((tmp_path / "build.json").read_text())
    assert b["purchased"][0]["name"] == "Core board" and b["purchased"][0]["modelPart"] == "PURCHASE_CoreBoard"
    assert b["firmware"]["compiled"] is True and b["firmware"]["tests_total"] == 5
    assert any(t["kind"] == "digital" for t in b["tests"])
    q = json.loads((tmp_path / "order" / "quote.json").read_text())
    assert q["delivered_price_inr"]["pilot_one_off"] > 0
    dc = json.loads((tmp_path / "checks" / "digital-checks.json").read_text())
    assert any(c["id"] == "stage:pcb" and c["status"] == "failed" for c in dc["checks"])
    assert any(c["id"] == "cad-bom-join" and c["status"] == "passed" for c in dc["checks"])
    assert any(c["id"] == "volume:PRINT_Shell" and c["status"] == "passed" for c in dc["checks"])  # 60x50x40 = 120000 mm3
    assert dc["all_passed"] is False
    for f in ("PROJECT.md", "PARTS_EVIDENCE.md", "SOFTWARE_SETUP.md", "manifest.json", "export.zip", "checks/delivery-checks.json", "spec.json", "project.json"):
        assert (tmp_path / f).exists(), f
    assert "## Done before delivery" in (tmp_path / "PARTS_EVIDENCE.md").read_text()
    assert "factory_work" in b
    proj = (tmp_path / "PROJECT.md").read_text()
    assert "Frame conventions" in proj and "failed" in proj
    m = json.loads((tmp_path / "manifest.json").read_text())
    from gstudio.package import verify_manifest
    assert verify_manifest(tmp_path, m) == []
    assert not any(f["path"].startswith(("logs/", ".gstudio/")) or f["path"] in ("status.json", "cad/draft.glb") for f in m["files"])
    assert {"firmware/firmware-merged.bin", "firmware/web-flasher-manifest.json"} <= {f["path"] for f in m["files"]}
    assert any(c["id"] == "web-flasher" and c["status"] == "passed" for c in dc["checks"])
    assert b["firmware"]["web_flasher_manifest"] == "firmware/web-flasher-manifest.json"
    assert "Flash from a web browser" in (tmp_path / "SOFTWARE_SETUP.md").read_text()
    assert s["stages"]["package"]["summary"]["build_json_reproducible"] is True
    pk = {c["id"]: c["status"] for c in s["stages"]["package"]["summary"]["checks"]}
    assert pk == {"manifest-verifies": "passed", "zip-verifies": "passed", "build-json-reproducible": "passed",
                  "guide-reproducible": "not_run", "story-audio-in-export": "not_run",
                  "component-sources-in-export": "not_run", "speech-roundtrip-in-export": "not_run", "no-home-paths": "passed",
                  "verify-delivery": "passed"}, s["stages"]["package"]["summary"]["verify_delivery"]
    assert "verify_delivery.py" in {f["path"] for f in m["files"]}


def test_only_runs_selected_plus_inputs(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, only="cad"))
    st = states(s)
    assert st["normalize"] == st["electrical"] == st["cad"] == "done"
    for sid in ("pcb", "firmware", "order", "manual", "checks", "docs", "package"):
        assert st[sid] == "skipped" and s["stages"][sid]["message"] == "not selected (--only)"
    assert s["state"] == "done"
    # a later --only order run reads the STL the earlier cad run left on disk
    s2 = build({}, tmp_path, opts(fakes, only=["order"]))
    assert states(s2)["order"] == "done", s2["stages"]["order"]
    assert (tmp_path / "order" / "quote.json").exists()
    # the cad record from the first run is kept for later docs runs
    assert pipeline.stage_records(tmp_path)["cad"]["state"] == "done"


def test_hard_dependency_failure_skips_dependents_only(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, mods={"cad": f"{fakes}.cad_fail", "manual": f"{fakes}.cad"}))
    st = states(s)
    assert st["cad"] == "failed" and "boolean union failed" in s["stages"]["cad"]["error"]
    assert st["manual"] == "skipped" and "needs cad, which failed" in s["stages"]["manual"]["message"]
    assert st["order"] == "failed" and "parts list written" in s["stages"]["order"]["message"]  # BOM yes, quote no
    assert json.loads((tmp_path / "build.json").read_text())["purchased"]
    assert st["firmware"] == "done" and st["checks"] == st["docs"] == st["package"] == "done"


def test_crash_and_timeout_are_failures(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, only="pcb,firmware", mods={"pcb": f"{fakes}.crash", "firmware": f"{fakes}.slow"},
                                 timeouts={"firmware": 2}))
    assert s["stages"]["pcb"]["state"] == "failed" and "signal 11" in s["stages"]["pcb"]["error"]
    assert s["stages"]["firmware"]["state"] == "failed" and "timed out" in s["stages"]["firmware"]["error"]
    assert s["stages"]["firmware"]["seconds"] < 30


def test_unavailable_stage_is_skipped_with_reason(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, only="pcb", mods={"pcb": f"{fakes}.not_written_yet"}))
    assert s["stages"]["pcb"]["state"] == "skipped" and "not available" in s["stages"]["pcb"]["message"]
    assert s["state"] == "done"


def test_invalid_spec_fails_normalize_and_skips_the_rest(fakes, tmp_path):
    def bad(*a, **k):
        from gstudio.errors import SpecError
        raise SpecError([{"path": "body.depth", "msg": "must be >= 40", "fix": "use 60"}])

    s = build({}, tmp_path, opts(fakes, _context=bad))
    assert s["stages"]["normalize"]["state"] == "failed" and "body.depth" in s["stages"]["normalize"]["error"]
    assert all(v["state"] == "skipped" for k, v in s["stages"].items() if k != "normalize")
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "error"


def test_real_example_validates_when_foundation_is_ready():
    ex = Path(__file__).resolve().parents[2] / "examples" / "bedtime-robot" / "spec.json"
    if not ex.exists():
        pytest.skip("examples/bedtime-robot/spec.json not written yet")
    try:
        import gstudio.library  # noqa: F401
        import gstudio.normalize  # noqa: F401
    except ImportError:
        pytest.skip("foundation library/normalize not available yet")
    r = pipeline.validate(json.loads(ex.read_text()))
    assert r["ok"], r["errors"]
    assert r["normalized"]["derived"]["instances"]


def test_full_rebuild_moves_stale_outputs_aside(fakes, tmp_path):
    """A full run must not price or ship an earlier run's print files when CAD fails now."""
    build({}, tmp_path, opts(fakes))                                     # leaves print/PRINT_Shell.stl
    assert (tmp_path / "print" / "PRINT_Shell.stl").exists()
    s = build({}, tmp_path, opts(fakes, mods={"cad": f"{fakes}.cad_fail"}))
    assert s["stages"]["order"]["state"] == "failed" and "CAD stage failed in this build" in s["stages"]["order"]["error"]
    assert not (tmp_path / "print" / "PRINT_Shell.stl").exists()
    assert (tmp_path / ".gstudio" / "previous" / "print" / "PRINT_Shell.stl").exists()
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert not any(f["path"].startswith(("print/", "cad/")) for f in m["files"])


def test_sigterm_stops_stage_processes_and_grandchildren(fakes, tmp_path):
    """The builder kills the engine on timeout: no stage process or tool it started may survive."""
    import os
    import signal
    import subprocess

    ex = Path(__file__).resolve().parents[2] / "examples" / "bedtime-robot" / "spec.json"
    if not ex.exists():
        pytest.skip("needs a real spec for normalize")
    (tmp_path / "opts.json").write_text(json.dumps({"stage_modules": {"firmware": f"{fakes}.slow", "cad": f"{fakes}.slow",
                                                                     "pcb": f"{fakes}.slow"}, "only": ["firmware", "pcb"]}))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(pipeline.__file__).resolve().parents[1]), sys.path[0]]))
    env["PYTHONPATH"] = os.pathsep.join([env["PYTHONPATH"]] + [p for p in sys.path if p.endswith("fakes0") or "fakes" in p])
    proc = subprocess.Popen([sys.executable, "-m", "gstudio", "build", str(ex), str(tmp_path / "out"), "--options-file",
                             str(tmp_path / "opts.json"), "--quiet"], env=env, start_new_session=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        time.sleep(0.2)
        st = json.loads((tmp_path / "out" / "status.json").read_text()) if (tmp_path / "out" / "status.json").exists() else {}
        if sum(1 for x in st.get("stages", []) if x["state"] == "running") >= 2:
            break
    time.sleep(0.5)
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(30)
    assert proc.returncode == 143, proc.stderr.read().decode()[-2000:]
    st = json.loads((tmp_path / "out" / "status.json").read_text())
    assert st["state"] == "error" and "terminated" in st["error"]
    assert {x["id"]: x["state"] for x in st["stages"]}["firmware"] == "failed"
    def alive():
        return (subprocess.run(["pgrep", "-f", "sleep 61.4321"], capture_output=True, text=True).stdout.split()
                + subprocess.run(["pgrep", "-f", str(tmp_path / "out")], capture_output=True, text=True).stdout.split())
    for _ in range(25):  # processes get SIGKILL at the latest 5 s after SIGTERM
        if not alive():
            break
        time.sleep(0.2)
    assert not alive(), subprocess.run(["ps", "-o", "pid,pgid,stat,cmd", "-p", ",".join(alive())], capture_output=True, text=True).stdout


def test_partial_files_of_a_failed_cad_are_never_priced(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, mods={"cad": f"{fakes}.cad_partial"}))
    assert s["stages"]["cad"]["state"] == "failed"
    assert s["stages"]["order"]["state"] == "failed" and "partial and unvalidated" in s["stages"]["order"]["error"]
    assert not (tmp_path / "order" / "quote.json").exists()
    b = json.loads((tmp_path / "build.json").read_text())
    assert b["purchased"] and b["order"]["status"] == "NOT_QUOTED"
    assert "partial files" in (tmp_path / "PROJECT.md").read_text()


# ---------------------------------------------------------------- REVIEW-1: MAJ-6 gate, MAJ-19 finalize, MIN-21


def test_maj6_forced_collision_is_not_buildable_and_the_quote_is_held(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, mods={"cad": f"{fakes}.cad_collide", "pcb": f"{fakes}.firmware_print"}))
    assert states(s)["cad"] == "done" and states(s)["order"] == "done"
    assert s["buildable"] is False and s["verdict"] == "not_buildable"
    assert "PRINT_Shell / PURCHASE_CoreBoard (12.5 mm3)" in s["verdict_message"] and "No automatic repair" in s["verdict_message"]
    q = json.loads((tmp_path / "order" / "quote.json").read_text())
    assert q["status"] == "HELD" and q["delivered_price_inr"] is None and q["held_because"][0]["parts"] == ["PRINT_Shell", "PURCHASE_CoreBoard"]
    b = json.loads((tmp_path / "build.json").read_text())
    assert b["order"]["status"] == "HELD" and b["order"]["list_price_inr"] is None
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["buildable"] is False and "Not buildable" in st["verdict_message"]
    assert any("Quote held" in m["text"] for m in st["messages"])
    assert "Not buildable yet" in (tmp_path / "PROJECT.md").read_text()
    assert json.loads((tmp_path / ".gstudio" / "gate.json").read_text())["buildable"] is False


def _spec_ctx(spec, out, options, on_progress):
    d = spec if isinstance(spec, dict) and spec.get("features") else dict(SPEC, features=[
        {"id": "speaker", "module": "core_mod", "face": "front", "at": [0, 56]}, {"id": "light", "module": "core_mod", "face": "front", "at": [0, 85]}])
    return _FallbackContext(FakeSpec(d), FakeLib(), out, pipeline._public_options(options), on_progress)


def test_maj6_one_repair_round_moves_the_feature_and_rebuilds_the_cad(fakes, tmp_path):
    o = opts(fakes, mods={"cad": f"{fakes}.cad_fixable", "pcb": f"{fakes}.firmware_print"})
    o["_context"] = _spec_ctx
    s = build({}, tmp_path, o)
    assert states(s)["cad"] == "done", s["stages"]["cad"]
    rep = s["auto_repair"]
    assert rep["feature"] == "light" and rep["away_from"] == "speaker" and rep["direction"] == "up"
    assert rep["moved_mm"] == pytest.approx((86.1 - 81.6) + 2.0, abs=0.05) and rep["to_at"] == [0, pytest.approx(91.5, abs=0.05)]
    assert s["buildable"] is True and "after one automatic repair" in s["verdict_message"]
    sp = json.loads((tmp_path / "spec.json").read_text())
    assert next(f for f in sp["features"] if f["id"] == "light")["at"][1] == pytest.approx(91.5, abs=0.05)
    assert json.loads((tmp_path / "project.json").read_text())["auto_repair"]["feature"] == "light"
    texts = [m["text"] for m in json.loads((tmp_path / "status.json").read_text())["messages"]]
    assert any(t.startswith("Repair round:") for t in texts) and any("light at 91.5" in t for t in texts)
    assert json.loads((tmp_path / "order" / "quote.json").read_text())["status"] != "HELD"


def test_maj6_no_repair_when_switched_off(fakes, tmp_path):
    o = opts(fakes, mods={"cad": f"{fakes}.cad_fixable", "pcb": f"{fakes}.firmware_print"}, auto_repair=False)
    o["_context"] = _spec_ctx
    s = build({}, tmp_path, o)
    assert s["buildable"] is False and s["auto_repair"] is None and "switched off" in s["verdict_message"]


def test_maj19_finalize_runs_after_checks_and_before_docs_and_package(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, mods={"manual": f"{fakes}.manual_final", "finalize": f"{fakes}.manual_final",
                                              "pcb": f"{fakes}.firmware_print"}))
    st = states(s)
    assert st["finalize"] == "done" and st["checks"] == "done" and st["docs"] == "done", s["stages"]["finalize"]
    recs = pipeline.stage_records(tmp_path)
    assert recs["checks"]["ended"] <= recs["finalize"]["started"] and recs["finalize"]["ended"] <= recs["docs"]["started"]
    b = json.loads((tmp_path / "build.json").read_text())
    guide = (tmp_path / "BUILD_GUIDE.md").read_text()
    assert all(f"- **{t['title']}:**" in guide for t in b["tests"])          # final checks match tests[] (incl. checks' rows)
    pk = {c["id"]: c for c in s["stages"]["package"]["summary"]["checks"]}
    assert pk["guide-reproducible"]["status"] == "passed", pk["guide-reproducible"]
    assert pk["verify-delivery"]["status"] == "passed", s["stages"]["package"]["summary"]["verify_delivery"]
    texts = [m["text"] for m in json.loads((tmp_path / "status.json").read_text())["messages"]]
    assert any("1 PNG illustrations rendered from the CAD and 1 SVG diagram" in t for t in texts)


def test_min21_a_changed_tests_status_makes_build_json_not_reproducible(fakes, tmp_path):
    s = build({}, tmp_path, opts(fakes, mods={"pcb": f"{fakes}.firmware_print"}))
    assert s["stages"]["package"]["summary"]["build_json_reproducible"] is True
    b = json.loads((tmp_path / "build.json").read_text())
    t = next(t for t in b["tests"] if t.get("source") == "checks")
    t["status"] = "passed" if t["status"] != "passed" else "failed"
    (tmp_path / "build.json").write_text(json.dumps(b))
    s2 = build({}, tmp_path, opts(fakes, only="package", mods={"pcb": f"{fakes}.firmware_print"}))
    ps = s2["stages"]["package"]["summary"]
    assert ps["build_json_reproducible"] is False and any("tests" in p for p in ps["problems"])
