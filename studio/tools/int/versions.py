"""Print per-version facts of one store project (run on the VM): python3 -I tools/int/versions.py <project dir>"""
import glob
import json
import sys

P = sys.argv[1]
p = json.load(open(f"{P}/project.json"))
for v in p["versions"]:
    n = v["n"]
    try:
        st = json.load(open(f"{P}/v{n}/status.json"))
        stages = [(s["id"], s["state"], s.get("seconds") or s.get("elapsed_s") or s.get("duration_s")) for s in st["stages"]]
    except OSError:
        stages = None
    print(n, v["label"][:70], "|", v["source"], v["state"], "brief", v.get("brief_version"), "base", v.get("base_version"))
    print("   ", stages)
cs = json.load(open(f"{P}/v6/cad/cad-summary.json"))
print("v6 recolored", cs.get("recolored"), cs.get("timings", {}).get("total"))
g4 = sorted(glob.glob(f"{P}/v4/pcb/gerbers/*"))
g6 = sorted(glob.glob(f"{P}/v6/pcb/gerbers/*"))
print("gerbers", len(g4), len(g6), all(open(a, "rb").read() == open(b, "rb").read() for a, b in zip(g4, g6)))
print("briefs", [b.get("version") for b in p.get("brief_history") or []])
for n in range(1, 7):
    try:
        pj = json.load(open(f"{P}/v{n}/project.json"))
        print("project.json v%d" % n, pj.get("brief_version"), pj.get("concept_id"))
    except OSError:
        pass
