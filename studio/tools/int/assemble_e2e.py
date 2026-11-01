"""Copy one e2e run into the benchmark layout of _out/e2e/ (run on the VM from studio/):
    python3 -I tools/int/assemble_e2e.py <store dir> <pid> <client dir> <out dir>
-> <out>/{v1,v2,v3,...} project.json chat.json concepts/ (+ concepts.json) phases.json client-log/
"""
import json
import shutil
import sys
from pathlib import Path

store, pid, client, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
proj_dir = store / "projects" / pid
out.mkdir(parents=True, exist_ok=True)
p = json.loads((proj_dir / "project.json").read_text())
for v in p.get("versions") or []:
    src = proj_dir / f"v{v['n']}"
    if src.is_dir():
        dst = out / f"v{v['n']}"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=False)
shutil.copyfile(proj_dir / "project.json", out / "project.json")
(out / "chat.json").write_text(json.dumps({"project_id": p.get("id"), "name": p.get("name"), "chat": p.get("chat")}, indent=1))
cdst = out / "concepts"
shutil.rmtree(cdst, ignore_errors=True)
cdst.mkdir()
rows = []
for c in p.get("concepts") or []:
    src = proj_dir / "concepts" / c["id"]
    if src.is_dir():
        shutil.copytree(src, cdst / c["id"])
    sp = c.get("spec") or {}
    rows.append({"id": c["id"], "title": c.get("title"), "tier": c.get("tier"), "blurb": c.get("blurb"), "state": c.get("state"),
                 "warnings": c.get("warnings"), "photo_note": c.get("photo_note"), "set": c.get("set") or c.get("turn_id"),
                 "family": (sp.get("body") or {}).get("family"),
                 **{k: f"concepts/{c['id']}/{f}" for k, f in (("photo", "photo.png"), ("render", "render.png"), ("glb", "preview.glb"))
                    if (src / f).exists()}})
(cdst / "concepts.json").write_text(json.dumps(rows, indent=1))
st = client / "state.json"
if st.exists():
    d = json.loads(st.read_text())
    d.pop("cookie", None)
    (out / "phases.json").write_text(json.dumps(d, indent=1))
cl = out / "client-log"
shutil.rmtree(cl, ignore_errors=True)
shutil.copytree(client / "log", cl)
for f in client.glob("log-*.out"):
    shutil.copyfile(f, cl / f.name)
print(json.dumps({"versions": [v["n"] for v in p.get("versions") or []], "concepts": len(rows), "out": str(out)}))
