#!/usr/bin/env python3
"""Fetch vendor listings and reference documents into library/references/ with sha256 (foundation; run on the VM).

    python3 -I library/tools/fetch_listings.py fetch  <jobs.json>   # [{id, url, kind: shopify|html|pdf|search}], writes snapshots
    python3 -I library/tools/fetch_listings.py index                # rebuild library/references/index.json from the files

Every snapshot is the raw response body, saved as library/references/<dir>/<id>.<ext>, and recorded in index.json with its
url, http status, bytes, sha256 and fetch time (UTC, ISO 8601). Nothing is parsed or rewritten here: the parsed facts go to
library/sourcing-updates.json, which names the snapshot each fact came from. Polite: one request every 4 s, one retry after
a 429 (Retry-After or 65 s). Pure standard library.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
REFS = LIB / "references"
INDEX = REFS / "index.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
EXT = {"shopify": "js", "html": "html", "pdf": "pdf", "search": "json", "json": "json", "txt": "txt", "dxf": "dxf", "page": "html"}
DIRS = {"shopify": "listings", "html": "listings", "search": "searches", "pdf": "sources", "json": "sources", "txt": "sources", "dxf": "sources",
        "page": "sources"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, r.read(), now()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(float(e.headers.get("Retry-After") or 65))
                continue
            return e.code, e.read() if hasattr(e, "read") else b"", now()
        except Exception as e:  # noqa: BLE001 - record and continue
            return 0, str(e).encode(), now()
    return 0, b"", now()


def load_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {"schema": "gadget-references/1", "files": []}


def save_index(ix: dict) -> None:
    ix["files"].sort(key=lambda f: f["path"])
    INDEX.write_text(json.dumps(ix, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch(jobs_path: str) -> int:
    jobs = json.loads(Path(jobs_path).read_text(encoding="utf-8"))
    ix = load_index()
    by_path = {f["path"]: f for f in ix["files"]}
    bad = 0
    for k, j in enumerate(jobs):
        kind = j.get("kind", "html")
        rel = f"{DIRS.get(kind, 'sources')}/{j['id']}.{EXT.get(kind, 'bin')}"
        status, body, at = get(j["url"])
        ok = status == 200 and len(body) > 200 and b"local_rate_limited" not in body[:100]
        if ok:
            p = REFS / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
            by_path[rel] = {"path": rel, "url": j["url"], "kind": kind, "http_status": status, "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(), "fetched_at": at, "used_by": j.get("used_by", []),
                            "covers": j.get("covers", [])}
        else:
            bad += 1
        print(f"{'ok ' if ok else 'BAD'} {status} {len(body):>8} {rel}  {j['url']}", flush=True)
        if k < len(jobs) - 1:
            time.sleep(float(j.get("delay", 4)))
    ix["files"] = list(by_path.values())
    save_index(ix)
    return 1 if bad else 0


def index() -> int:
    ix = load_index()
    known = {f["path"]: f for f in ix["files"]}
    for p in sorted(REFS.rglob("*")):
        if p.is_dir() or p.name == "index.json":
            continue
        rel = str(p.relative_to(REFS))
        body = p.read_bytes()
        e = known.setdefault(rel, {"path": rel, "url": None, "kind": "file", "fetched_at": None, "used_by": [], "covers": []})
        e["bytes"], e["sha256"] = len(body), hashlib.sha256(body).hexdigest()
    ix["files"] = [f for f in known.values() if (REFS / f["path"]).exists()]
    save_index(ix)
    print(f"index: {len(ix['files'])} files")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        sys.exit(fetch(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == "index":
        sys.exit(index())
    print(__doc__)
    sys.exit(2)
