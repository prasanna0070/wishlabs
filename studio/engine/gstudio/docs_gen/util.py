"""Shared readers and formatting for the generated project documents."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def inr(x: Any) -> str:
    """₹ with Indian digit grouping (1,23,456), as in sim/run_sim.py."""
    if x is None:
        return "n/a"
    v = round(float(x))
    n = str(abs(v))
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        n = head + "," + tail
    return ("-" if v < 0 else "") + "₹" + n


def md_escape(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def table(head: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(md_escape(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def size_from_components(comps: dict) -> list[float] | None:
    boxes = [c.get("bbox") for n, c in (comps or {}).items()
             if isinstance(c, dict) and not n.startswith("REFERENCE_") and isinstance(c.get("bbox"), (list, tuple)) and len(c["bbox"]) == 6]
    if not boxes:
        return None
    lo = [min(b[i] for b in boxes) for i in (0, 2, 4)]
    hi = [max(b[i] for b in boxes) for i in (1, 3, 5)]
    return [round(hi[i] - lo[i], 1) for i in range(3)]


def exists(out: Path, rel: str) -> bool:
    return (out / rel).exists()


# written by the package stage, after the documents: never "(not produced)" (MAJ-21)
PACKAGE_OUTPUTS = ("checks/delivery-checks.json", "manifest.json", "export.zip", "verify_delivery.py")


def link(rel: str, out: Path) -> str:
    if (out / rel).exists():
        return f"`{rel}`"
    if rel in PACKAGE_OUTPUTS:
        return f"`{rel}` (written at packaging)"
    return f"`{rel}` (not produced)"


def slug_words(s: str) -> str:
    return re.sub(r"[_-]+", " ", s or "").strip()


class Bundle:
    """Everything the documents read, loaded once."""

    def __init__(self, out: Path, spec: dict, records: dict):
        self.out = out
        self.spec = spec
        self.records = records
        self.project = read_json(out / "project.json", {}) or {}
        self.build = read_json(out / "build.json", {}) or {}
        self.quote = read_json(out / "order" / "quote.json", None)
        self.slice = read_json(out / "print" / "slice-stats.json", None)
        self.digital = read_json(out / "checks" / "digital-checks.json", None)
        self.electrical = read_json(out / "electrical.json", None)
        self.components = read_json(out / "cad" / "components.json", {}) or {}

    def state(self, sid: str) -> str:
        return (self.records.get(sid) or {}).get("state", "not run")

    def summary(self, sid: str) -> dict:
        s = (self.records.get(sid) or {}).get("summary")
        return s if isinstance(s, dict) else {}

    def message(self, sid: str) -> str:
        return str((self.records.get(sid) or {}).get("message") or "")

    @property
    def derived(self) -> dict:
        return self.spec.get("derived") or {}

    def physical_tests(self) -> list[dict]:
        return [t for t in self.build.get("tests") or [] if isinstance(t, dict) and t.get("kind") == "physical"]
