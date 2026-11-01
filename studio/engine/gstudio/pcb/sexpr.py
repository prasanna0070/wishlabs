"""Minimal S-expression reader/writer for KiCad files (symbols, schematics, footprints).

Nodes are Python lists. Unquoted atoms are `str`; quoted strings are `Q` (a `str` subclass), so a round
trip keeps KiCad's quoting. Numbers stay as atoms (strings) when parsed; when building nodes, Python
ints/floats are formatted with `num()`.
"""

from __future__ import annotations

from typing import Any, Iterator


class Q(str):
    """A quoted string atom."""


def num(v: float | int) -> str:
    """Format a number the way KiCad does: up to 4 decimals, no trailing zeros, no '-0'."""
    if isinstance(v, bool):  # pragma: no cover - defensive
        raise TypeError("bool is not a number here")
    if isinstance(v, int):
        return str(v)
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def _tokens(text: str) -> Iterator[Any]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(" or c == ")":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n:
                d = text[j]
                if d == "\\" and j + 1 < n:
                    e = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(e, e))
                    j += 2
                    continue
                if d == '"':
                    break
                buf.append(d)
                j += 1
            yield Q("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield text[i:j]
            i = j


def parse(text: str) -> list:
    """Parse one top-level S-expression."""
    stack: list[list] = []
    root: list | None = None
    for tok in _tokens(text):
        if tok == "(" and not isinstance(tok, Q):
            node: list = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif tok == ")" and not isinstance(tok, Q):
            node = stack.pop()
            if not stack:
                root = node
                break
        else:
            if not stack:
                raise ValueError("atom outside an expression")
            stack[-1].append(tok)
    if root is None:
        raise ValueError("unbalanced S-expression")
    return root


def _atom(a: Any) -> str:
    if isinstance(a, Q):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, (int, float)):
        return num(a)
    return str(a)


def _is_flat(node: list) -> bool:
    return all(not isinstance(x, list) for x in node)


def dumps(node: Any, indent: int = 0) -> str:
    """Serialize with tab indentation. Short all-atom lists stay on one line."""
    if not isinstance(node, list):
        return _atom(node)
    pad = "\t" * indent
    if _is_flat(node):
        return pad + "(" + " ".join(_atom(a) for a in node) + ")"
    head: list[str] = []
    rest: list = []
    for k, x in enumerate(node):
        if isinstance(x, list):
            rest = node[k:]
            break
        head.append(_atom(x))
    out = [pad + "(" + " ".join(head)]
    for x in rest:
        if isinstance(x, list):
            # keep tiny leaf lists like (xy 1 2) or (at 1 2 0) inline only when parent is a pts list
            out.append(dumps(x, indent + 1))
        else:
            out.append("\t" * (indent + 1) + _atom(x))
    out.append(pad + ")")
    return "\n".join(out)


# ----------------------------------------------------------------------------- tree helpers

def head(node: Any) -> str | None:
    return node[0] if isinstance(node, list) and node and not isinstance(node[0], list) else None


def find(node: list, name: str) -> list | None:
    for x in node:
        if isinstance(x, list) and x and x[0] == name:
            return x
    return None


def find_all(node: list, name: str) -> list[list]:
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def walk(node: list) -> Iterator[list]:
    yield node
    for x in node:
        if isinstance(x, list):
            yield from walk(x)


def deep_copy(node: Any) -> Any:
    if isinstance(node, list):
        return [deep_copy(x) for x in node]
    if isinstance(node, Q):
        return Q(str(node))
    return node


__all__ = ["Q", "num", "parse", "dumps", "head", "find", "find_all", "walk", "deep_copy"]
