"""Text helpers for the parent-facing guide: pipeline-word filter (MIN-34), label wrapping (MIN-31), tool lists
(MIN-33), axis wording (MIN-32)."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .geom import AXES

# words that belong to the build pipeline, never to the maker's guide (MIN-34)
INTERNAL = re.compile(r"\b(normali[sz]er|blueprint|catalog(ue)?|slot map|backplane-v\d+|library row|planning range)\b", re.I)


def parent_name(name: str, *, nuts_used: bool = True) -> str:
    """A purchased row's name as the maker should read it: no internal board id, no nuts when none are fitted."""
    s = str(name or "")
    s = re.sub(r"\s*\bbackplane-v\d+\s*\(ours\)", " (made for this design)", s)
    s = re.sub(r"\bbackplane-v\d+\b", "", s)
    if not nuts_used:
        s = re.sub(r"\s*\+\s*ISO\s*4032\s*hex nuts", "", s, flags=re.I)
        s = re.sub(r"\s*(and|\+)\s*hex nuts\b", "", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip()


def parent_note(note: str) -> str:
    """Drop the sentences of a parts note that talk about the pipeline (they go to PARTS_EVIDENCE.md instead)."""
    out = []
    for sent in re.split(r"(?<=\.)\s+", str(note or "").strip()):
        if not sent or not INTERNAL.search(sent):
            out.append(sent)
            continue
        # drop only the clauses (split on top-level ';' or ' (' asides) that talk about the pipeline
        clauses, depth, cur = [], 0, ""
        for ch in sent:
            depth += ch == "("
            depth -= ch == ")"
            if ch == ";" and depth == 0:
                clauses.append(cur)
                cur = ""
            else:
                cur += ch
        clauses.append(cur)
        keep = [c.strip() for c in clauses if c.strip() and not INTERNAL.search(c)]
        keep = [re.sub(r"\s*\([^()]*\b(normali[sz]er|blueprint|catalog(ue)?)\b[^()]*\)", "", c, flags=re.I) for c in keep]
        if keep:
            s = "; ".join(keep).rstrip(".;, ")
            out.append(s + ".")
    return re.sub(r"\s{2,}", " ", " ".join(x for x in out if x)).strip()


def is_parent_safe(text: str) -> bool:
    return not INTERNAL.search(text or "")


_LOWER_AXIS = re.compile(r"(?<![\w])([+-])([xyz])(?![\w])")


def axis_words(text: str) -> str:
    """Rewrite bare lowercase axes ('+x (robot's left)', '-x') into the guide's axis phrases (MIN-32)."""
    def rep(m: re.Match) -> str:
        sign, ax = m.group(1), m.group(2)
        v = [0.0, 0.0, 0.0]
        v["xyz".index(ax)] = 1.0 if sign == "+" else -1.0
        return AXES[(v[0], v[1], v[2])]

    s = _LOWER_AXIS.sub(rep, str(text or ""))
    # the slot data says "robot's left/right"; the axis phrase already says it, so drop the duplicate
    s = re.sub(r"(\((the gadget's (left|right))\))\s*\((the )?robot's (left|right)\)", r"\1", s)
    s = re.sub(r"\(board edge at the robot's (left|right)\)", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def wrap(text: str, width: int = 30, max_lines: int = 3) -> list[str]:
    """Word-wrap a label into at most `max_lines` lines without ever cutting it short with an ellipsis: when the
    text needs more lines the width grows instead (MIN-31)."""
    words = str(text).split()
    if not words:
        return []
    w = width
    while True:
        lines, cur = [], ""
        for x in words:
            if cur and len(cur) + 1 + len(x) > w:
                lines.append(cur)
                cur = x
            else:
                cur = f"{cur} {x}".strip()
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines:
            return lines
        w += 4


def dedupe(items: Iterable[str]) -> list[str]:
    out, seen = [], set()
    for t in items:
        k = re.sub(r"^(a|an|the)\s+", "", t.strip().lower())
        if t.strip() and k not in seen:
            seen.add(k)
            out.append(t.strip()[0].upper() + t.strip()[1:])
    return out


def tools_line(tools: list[str], note: Optional[str] = None) -> str:
    """A step's Tools line from its structured list (MIN-33)."""
    if not tools:
        base = "None."
    else:
        base = ", ".join(t[0].lower() + t[1:] if i else t for i, t in enumerate(tools)) + "."
    return base + (f" {note}" if note else "")
