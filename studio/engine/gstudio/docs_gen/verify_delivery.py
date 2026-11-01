#!/usr/bin/env python3
"""Re-check this delivery without the Gadget Studio engine (Python 3.9+, standard library only).

    python verify_delivery.py            run from the folder that holds manifest.json

Checks
  1. every file in manifest.json exists with the listed bytes and sha256, and no delivered file is unlisted (QB-44);
  2. build.json joins: unique purchased names, wiring parts are purchased items (QB-18), step parts and modelPart
     names exist in cad/components.json (QB-06, QB-29), every step visual exists (QB-34);
  3. checks/delivery-checks.json counts equal the files present now (QB-45);
  4. the guide's "Final checks to record" bullets name exactly the build.json tests[] titles, and manual-summary.json
     counts them (MAJ-19); PROJECT.md marks no shipped file "(not produced)" (MAJ-21);
  5. every digital check names its artifact and no detail is a Python list (MIN-28);
  6. every "seen" price's saved listing snapshot hashes to the sha256 in its receipt (MAJ-25), and a story card that
     says it has audio lists only files that ship (BLK-4).
Exit code 0 when everything holds, 1 otherwise. This proves the files are intact and consistent, nothing physical.
"""
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {"logs", ".gstudio", ".pio", "__pycache__", ".cache", "node_modules", ".git", ".vscode"}
SKIP_TOP = {"export.zip", "manifest.json", "status.json"}
SKIP_REL = {"cad/draft.glb"}
problems, passed = [], []


def ok(cond, what, detail=""):
    (passed if cond else problems).append(what + (f": {detail}" if detail and not cond else ""))


def load(rel, default=None):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def delivered():
    out = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if (len(rel.parts) == 1 and rel.name in SKIP_TOP) or rel.as_posix() in SKIP_REL or rel.name.endswith((".pyc", ".tmp")):
            continue
        # not delivered (package.delivered_files, MAJ-38): cable-route envelopes and the duplicate app image
        if (rel.parent.as_posix() == "cad/components" and rel.name.startswith("REFERENCE_") and rel.suffix == ".brep") \
                or rel.as_posix() == "firmware/flash/firmware.bin":
            continue
        out.append(rel.as_posix())
    return out


man = load("manifest.json")
if not isinstance(man, dict):
    print("manifest.json missing or unreadable")
    sys.exit(1)
listed = {f["path"]: f for f in man.get("files", [])}
bad = [f"{r}: missing" if not (ROOT / r).is_file() else f"{r}: changed" for r, f in listed.items()
       if not (ROOT / r).is_file() or (ROOT / r).stat().st_size != f["bytes"] or sha256(ROOT / r) != f["sha256"]]
ok(not bad, f"{len(listed)} manifest files intact", "; ".join(bad[:10]))
extra = [r for r in delivered() if r not in listed and r != "verify_delivery.py"]
ok(not extra, "no unlisted files", ", ".join(extra[:10]))

b = load("build.json", {}) or {}
comps = load("cad/components.json", {}) or {}
names = [p.get("name") for p in b.get("purchased", [])]
ok(len(names) == len(set(names)), "purchased names are unique")
w = b.get("wiring") or {}
if isinstance(w, dict) and w.get("parts"):
    miss = sorted({str(p.get("item")) for p in w["parts"] if p.get("item") not in set(names)})
    ok(not miss, "every wiring part is a purchased item", ", ".join(miss))
if comps:
    mp = sorted({p.get("modelPart") for p in b.get("purchased", []) if p.get("modelPart") and p.get("modelPart") not in comps})
    ok(not mp, "every modelPart exists in the CAD", ", ".join(mp))
    coupons = {p.stem for p in ROOT.glob("print/TEST_*.stl")}  # fit coupons are STL-only by convention
    sp = sorted({x for s in b.get("steps", []) for x in (s.get("parts") or []) if x not in comps and x not in coupons})
    ok(not sp, "every step part exists in the CAD (or as a TEST_ coupon STL)", ", ".join(sp))
vis = sorted({v["path"] for s in b.get("steps", []) for v in (s.get("visuals") or []) if v.get("path") and not (ROOT / v["path"]).is_file()})
ok(not vis, "every step image exists", ", ".join(vis))

dc = load("checks/delivery-checks.json", {}) or {}
if dc:
    now = {"printFiles": len(list(ROOT.glob("print/PRINT_*.stl"))), "purchasedEntries": len(names),
           "assemblySteps": len(b.get("steps", [])),
           "manualImages": len([p for p in (ROOT / "manual").glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm")])
           if (ROOT / "manual").exists() else 0}
    diff = [f"{k}: file says {dc.get(k)}, found {v}" for k, v in now.items() if k in dc and dc.get(k) != v]
    ok(not diff, "delivery-checks.json counts match the files", "; ".join(diff))

# 4. guide final checks <-> tests[] (both ways), manual-summary count, stale "(not produced)" labels
guide_p = ROOT / "BUILD_GUIDE.md"
tests = [t for t in b.get("tests", []) if isinstance(t, dict)]
if guide_p.exists() and tests:
    g = guide_p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## Final checks to record\s*$(.*?)(?=^## |\Z)", g, re.M | re.S)
    if m:
        bullets = {re.sub(r"\s+", " ", x).strip() for x in re.findall(r"^\s*[-*] \*\*(.+?)(?::\*\*|\*\*:)", m.group(1), re.M)}
        titles = {re.sub(r"\s+", " ", str(t.get("title"))).strip() for t in tests}
        ok(titles <= bullets, "every tests[] record is in the guide's final checks", "; ".join(sorted(titles - bullets)[:10]))
        ok(bullets <= titles, "every final-check bullet is a tests[] record", "; ".join(sorted(bullets - titles)[:10]))
    else:
        ok(False, "the guide has a 'Final checks to record' section")
ms = load("manual/manual-summary.json", {}) or {}
if tests and isinstance(ms.get("tests"), int):
    ok(ms["tests"] == len(tests), "manual-summary tests count equals build.json tests[]", f"{ms['tests']} vs {len(tests)}")
pj = ROOT / "PROJECT.md"
if pj.exists():
    stale = sorted(set(re.findall(r"`([^`]+)` \(not produced\)", pj.read_text(encoding="utf-8", errors="replace"))) & (set(listed) | {"manifest.json"}))
    ok(not stale, "PROJECT.md marks no shipped file '(not produced)'", ", ".join(stale))

# 5. digital check records
dg = load("checks/digital-checks.json", {}) or {}
if dg.get("checks"):
    noart = [c.get("id") for c in dg["checks"] if not c.get("artifact")]
    ok(not noart, "every digital check names its artifact", ", ".join(str(x) for x in noart[:10]))
    pyl = [c.get("id") for c in dg["checks"] if re.search(r"\['[^']*'(, '[^']*')*\]", str(c.get("detail") or ""))]
    ok(not pyl, "no digital check detail is a Python list", ", ".join(str(x) for x in pyl[:10]))

# 6. listing snapshots and the story card
badsnap = []
for r in b.get("purchased", []):
    lst = r.get("listing") or {}
    if r.get("evidence_tier") != "seen":
        continue
    rel, want = lst.get("snapshot"), lst.get("snapshot_sha256")
    rc = (lst.get("receipt") or {}).get("payload") or {}
    try:
        raw = gzip.decompress((ROOT / rel).read_bytes()) if rel else None
    except (OSError, EOFError):
        raw = None
    if raw is None or hashlib.sha256(raw).hexdigest() != want or rc.get("snapshot_sha256") != want or rc.get("price") != r.get("price_inr_seen"):
        badsnap.append(str(r.get("name")))
ok(not badsnap, "every seen price has its listing snapshot, and the receipt names it and the price", ", ".join(badsnap[:10]))
sj = load("firmware/sdcard/stories.json")
if isinstance(sj, dict) and sj.get("audio"):
    sd = ROOT / "firmware" / "sdcard"
    files = [f for st in sj.get("stories", []) for f in st.get("files", [])] + [(sj.get("default_name") or {}).get("file")]
    miss = [f for f in files if f and not (sd / f).is_file()]
    ok(not miss, "every story audio file the card lists ships", ", ".join(miss[:10]))

for line in passed:
    print("ok    " + line)
for line in problems:
    print("FAIL  " + line)
print(f"{len(passed)} passed, {len(problems)} failed. Files and links only; no physical test is implied.")
sys.exit(1 if problems else 0)
