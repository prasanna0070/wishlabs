"""VOICE lint (MIN-42): the assistant never opens with an exclamation and uses no exclamation marks at all.
An opening sentence that ends in '!' is dropped when more text follows ("Great! I'll plan..." -> "I'll plan...")
and turned into a plain sentence otherwise; any other sentence-final '!' becomes a full stop."""
from __future__ import annotations

import re

_FIRST = re.compile(r"^\s*([^\n]*?[.!?])(?=\s|$)")
_BANG = re.compile(r"!+(?=\s|$|[\"')\]*_])")


def opens_with_exclamation(text: str) -> bool:
    m = _FIRST.match(text or "")
    return bool(m and m.group(1).endswith("!"))


HYPE = re.compile(r"\b(love|great|amazing|wonderful|awesome|happy|excited|perfect|fantastic|delighted|lovely|"
                  r"sure|absolutely|wow|hooray|yay|exciting|fun)\b", re.I)


def _filler(sentence: str) -> bool:
    """An enthusiasm opener with no information: short ('Great!') or hype words ('I would love to ...!')."""
    return len(sentence.split()) <= 2 or bool(HYPE.search(sentence))


def lint(text: str | None) -> str:
    t = (text or "").strip()
    if not t:
        return t
    m = _FIRST.match(t)
    if m and m.group(1).endswith("!"):
        rest = t[m.end():].lstrip()
        if rest and _filler(m.group(1)):
            t = rest
        else:
            t = m.group(1).rstrip("!").rstrip() + "." + t[m.end():]
    t = _BANG.sub(".", t)
    t = re.sub(r"\.\.(?=\s|$)", ".", t)
    return t[:1].upper() + t[1:] if t else t
