"""Real-engine check of the version routes (run on the VM, BUILDER=local): BLK-4 story audio, MAJ-38 export extras,
MIN-37 colour tweak plan, MAJ-36 brief revision, MAJ-20/MAJ-37 summaries.

  python scripts/live_versions.py <engine_build_out> <concept_dir> <out_dir> [--skip-tweak]

v1 is the given engine build (copied), with its story card made text-only to stand for a version built without
audio. Then: POST /versions/1/story-audio (v2, firmware-only rerun) and POST /tweak with a colour-only patch (v3).
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
KEY = "live-key-123"


def drain(c, url: str) -> None:
    """Follow the project event stream until the server closes it (every unfinished version finalized)."""
    with c.stream("GET", url) as r:
        for _ in r.iter_lines():
            pass


def zipnames(store_root: Path, pid: str, n: int) -> list[str]:
    return zipfile.ZipFile(store_root / f"projects/{pid}/v{n}/export.zip").namelist()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_out")
    ap.add_argument("concept_dir")
    ap.add_argument("out")
    ap.add_argument("--skip-tweak", action="store_true")
    ap.add_argument("--tweak-plan-only", action="store_true", help="ask for the colour tweak plan, do not build it")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    root = out / "store"
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.projects import make_message
    from app.store import LocalStore
    from app.util import now_iso, spec_hash

    st = LocalStore(root)
    s = Settings(store_dir=root, builder="local", agent="scripted", access_key=KEY, photos=False, static_dir=None,
                 cookie_secure=False)
    report: dict = {}
    with TestClient(create_app(s)) as c:
        c.post("/api/login", json={"key": KEY})
        pid = c.post("/api/projects", json={"name": "Story audio check"}).json()["id"]
        src = Path(a.build_out)
        spec = json.loads((src / "spec.json").read_text())
        spec.pop("derived", None)
        cid = "c_benchmark"
        cdir = Path(a.concept_dir)
        for name in ("render.png", "photo.png", "preview.glb"):
            if (cdir / name).exists():
                st.write_bytes(f"projects/{pid}/concepts/{cid}/{name}", (cdir / name).read_bytes())
        brief = {"version": 1, "concept_id": cid, "title": spec.get("name"), "spec": spec, "spec_hash": spec_hash(spec),
                 "functions": [{"title": "Bedtime stories", "value": "Tap the button for a story through the speaker",
                                "source": "user", "evidence": "You wrote: \"tells my daughter bedtime stories\""},
                               {"title": "Favourites", "value": "Remembers her favourite characters",
                                "source": "user", "evidence": "You wrote: \"remembers her favorite characters\""}],
                 "details": [], "size": {"w_mm": 142, "d_mm": 110, "h_mm": 179, "scope": "shell",
                                         "value": "About 142 W x 110 D x 179 H mm (shell with ears, without feet)"},
                 "power": {"value": "USB-C 5 V", "source": "suggested", "evidence": "Default"},
                 "approved": True, "created": now_iso()}

        def seed(p):
            p["concepts"] = [{"id": cid, "title": spec.get("name"), "tier": "expected", "blurb": spec.get("tagline"),
                              "spec": spec, "state": "ready", "render_url": f"/api/projects/{pid}/concepts/{cid}/render.png",
                              "photo_url": f"/api/projects/{pid}/concepts/{cid}/photo.png"}]
            p["selected_concept"] = cid
            p["brief"], p["brief_history"] = brief, [brief]
            p["chat"] = [make_message("user", "text", "I want a cute robot that tells my daughter bedtime stories and "
                                                      "remembers her favorite characters.")]
            p["versions"] = [{"n": 1, "created": now_iso(), "label": "Engine build", "source": "build", "state": "queued",
                              "summary": "", "spec_hash": spec_hash(spec), "brief_version": 1, "base_version": None,
                              "percent": 0, "error": None, "started": now_iso(), "ended": None, "finalized": False,
                              "thumb": None}]
            p["current_version"], p["stage"] = 1, "building"
        st.update_json(f"projects/{pid}/project.json", lambda p: seed(p) or p)
        shutil.copytree(src, root / f"projects/{pid}/v1", dirs_exist_ok=True)
        stp = root / f"projects/{pid}/v1/status.json"
        st1 = json.loads(stp.read_text())
        report["v1_engine_state"] = st1.get("state")
        report["v1_engine_failed_stages"] = [x["id"] for x in st1.get("stages", []) if x.get("state") == "failed"]
        if st1.get("state") != "done":    # stands in for a finished version (route needs state done)
            st1["state"] = "done"
            stp.write_text(json.dumps(st1))
        sd = root / f"projects/{pid}/v1/firmware/sdcard"
        idx = json.loads((sd / "stories.json").read_text())
        report["v1_engine_audio"] = idx.get("audio")
        for sub in ("stories", "names"):
            shutil.rmtree(sd / sub, ignore_errors=True)
        idx.update(audio=False, voice=None, tts_model=None, audio_format=None)
        (sd / "stories.json").write_text(json.dumps(idx, indent=1))
        st.write_json(f"projects/{pid}/inputs/v1.json", {"spec": spec, "brief": brief, "source": "build"})
        from app import export_extras
        export_extras.seed_references(st, pid, 1, {"id": cid, "title": spec.get("name"), "tier": "expected",
                                                   "blurb": spec.get("tagline")})
        t0 = time.time()
        c.get(f"/api/projects/{pid}")                       # reconcile -> finalize v1 (text-only card)
        report["v1_finalize_seconds"] = round(time.time() - t0, 1)
        names1 = zipnames(root, pid, 1)
        report["v1_zip_after_finalize"] = {"record": "project-record.json" in names1,
                                           "references": [n for n in names1 if "/references/concept" in n]}
        p = c.get(f"/api/projects/{pid}").json()
        res1 = next(m for m in p["chat"] if m["kind"] in ("result", "error"))
        report["v1_result_text"] = res1["text"]
        report["v1_requirements"] = res1["payload"]["summary"]["requirements"]
        t0 = time.time()
        r = c.post(f"/api/projects/{pid}/versions/1/story-audio")
        report["story_audio_http"] = r.status_code
        report["story_audio_plan"] = r.json().get("plan")
        drain(c, f"/api/projects/{pid}/stream")
        report["story_audio_seconds"] = round(time.time() - t0, 1)
        v2 = root / f"projects/{pid}/v2"
        idx2 = json.loads((v2 / "firmware/sdcard/stories.json").read_text())
        files = [f for s_ in idx2.get("stories", []) for f in s_.get("files", [])] + [idx2["default_name"]["file"]]
        names = zipnames(root, pid, 2)
        top = names[0].split("/")[0]
        status2 = json.loads((v2 / "status.json").read_text())
        dc2 = json.loads((v2 / "checks/digital-checks.json").read_text())
        sap = next((x for x in dc2.get("checks", []) if x.get("id") == "story-audio-present"), None)
        man2 = json.loads((v2 / "manifest.json").read_text())
        vd = subprocess.run([sys.executable, "-I", str(v2 / "verify_delivery.py")], capture_output=True, text=True,
                            cwd=str(v2))
        p = c.get(f"/api/projects/{pid}").json()
        res2 = [m for m in p["chat"] if m["kind"] == "result"][-1]
        report.update({
            "v2_stage_states": {x["id"]: x["state"] for x in status2.get("stages", [])},
            "v2_stage_seconds": {x["id"]: x.get("seconds") for x in status2.get("stages", [])},
            "v2_audio": idx2.get("audio"), "v2_voice": idx2.get("voice"), "v2_tts_model": idx2.get("tts_model"),
            "v2_files_listed": len(files),
            "v2_files_on_card": sum((v2 / "firmware/sdcard" / f).exists() for f in files),
            "v2_files_in_zip": sum(f"{top}/firmware/sdcard/{f}" in names for f in files),
            "v2_story_audio_check": sap,
            "v2_manifest_references": [f["path"] for f in man2["files"] if f["path"].startswith("references/")],
            "v2_zip_has_record": "project-record.json" in names,
            "v2_zip_references": [n for n in names if "/references/concept" in n],
            "v2_zip_bytes": (v2 / "export.zip").stat().st_size,
            "v2_verify_delivery": vd.stdout.strip().splitlines()[-1:] + [ln for ln in vd.stdout.splitlines() if ln.startswith("FAIL")],
            "v2_result_text": res2["text"],
            "v2_requirements": res2["payload"]["summary"]["requirements"],
        })
        if not a.skip_tweak:
            body = {"patch": {"colors": {"body": "#9CC3E6", "body_name": "sky blue"}}, "base_version": 2}
            pl = c.post(f"/api/projects/{pid}/tweak", json={**body, "validate_only": True}).json()
            report["tweak_plan"] = pl.get("plan")
            report["tweak_nudges"] = pl.get("nudges")
            if a.tweak_plan_only:
                report["pid"] = pid
                (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
                print(json.dumps(report, indent=1, default=str)[:12000])
                return 0
            t0 = time.time()
            r = c.post(f"/api/projects/{pid}/tweak", json=body)
            report["tweak_http"] = r.status_code
            drain(c, f"/api/projects/{pid}/stream")
            report["tweak_seconds"] = round(time.time() - t0, 1)
            v3 = root / f"projects/{pid}/v3"
            st3 = json.loads((v3 / "status.json").read_text())
            report["v3_stage_states"] = {x["id"]: x["state"] for x in st3.get("stages", [])}
            report["v3_stage_seconds"] = {x["id"]: x.get("seconds") for x in st3.get("stages", [])}
            p = c.get(f"/api/projects/{pid}").json()
            report["v3_label"] = p["versions"][-1]["label"]
            report["v3_changes"] = p["versions"][-1].get("changes")
            report["brief_history"] = [(b["version"], b.get("auto_for_version"), (b.get("size") or {}).get("value"),
                                        [d for d in b.get("details") or [] if str(d.get("topic", "")).startswith("Change")])
                                       for b in p["brief_history"]]
            report["v3_result_text"] = [m for m in p["chat"] if m["kind"] == "result"][-1]["text"]
        report["pid"] = pid
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str)[:12000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
