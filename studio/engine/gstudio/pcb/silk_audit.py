"""Silkscreen audit (REVIEW-1 MAJ-16): which reference designators are printed on the board, and whether every one
the build guide names is among them. Pure Python (reads the .kicad_pcb text), so manual/pipeline checks can import it.

    texts = silk_texts("pcb/backplane.kicad_pcb")              # every visible F.SilkS / B.SilkS string
    words = silk_words(texts)                                  # split into tokens ("J_LED", "LED", "5V", ...)
    missing = missing_designators(guide_md, words, omitted)    # designators the guide names that the silk lacks
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from . import sexpr

# BP1 (the board), U1/A1/S1 (module sockets) and the J_ port ids are the names the guide uses for board positions.
DESIGNATOR_RE = re.compile(r"(?<![A-Za-z0-9_])(BP1|U1|A1|S1|J_[A-Z][A-Z0-9]*)(?![A-Za-z0-9_])")
SILK_LAYERS = {"F.SilkS", "B.SilkS"}


def _hidden(node: list) -> bool:
    for sub in sexpr.walk(node):
        if sexpr.head(sub) == "hide" and len(sub) > 1 and str(sub[1]) == "yes":
            return True
    return any(str(a) == "hide" for a in node)


def silk_texts(pcb: str | Path, layers: Iterable[str] = ("F.SilkS",)) -> list[str]:
    """Visible text strings on the given silk layers: board texts plus footprint reference/value fields."""
    want = set(layers)
    root = sexpr.parse(Path(pcb).read_text())
    out: list[str] = []
    for node in sexpr.walk(root):
        h = sexpr.head(node)
        if h == "gr_text":
            text = str(node[1])
        elif h == "property" and len(node) > 2 and str(node[1]) in ("Reference", "Value"):
            text = str(node[2])
        elif h == "fp_text" and len(node) > 2:
            text = str(node[2])
        else:
            continue
        lay = sexpr.find(node, "layer")
        if not lay or str(lay[1]) not in want or _hidden(node):
            continue
        out.append(text)
    return out


def silk_words(texts: Iterable[str]) -> set[str]:
    return {w for t in texts for w in re.split(r"\s+", t.replace("\\n", " ")) if w}


def guide_designators(text: str) -> set[str]:
    return set(DESIGNATOR_RE.findall(text))


def missing_designators(guide_text: str, words: set[str], omitted: Iterable[str] = ()) -> list[str]:
    """Designators named in the guide that are not printed on the board. Slots this board omits are skipped (the guide
    may list them as 'not fitted'; drawing only populated ports is REVIEW-1 MIN-30)."""
    skip = set(omitted)
    return sorted(d for d in guide_designators(guide_text) if d not in skip and d not in words)


__all__ = ["silk_texts", "silk_words", "guide_designators", "missing_designators", "DESIGNATOR_RE"]
