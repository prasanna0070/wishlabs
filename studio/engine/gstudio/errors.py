"""Engine exceptions (owned by foundation).

StageError  - a stage failed hard. `details` is JSON-able and goes into status.json.
SpecError   - the design spec is invalid. `errors` is a list of short, actionable items that the
              LLM agent reads and repairs. Each item is a dict {"path", "msg", "fix"?}; plain strings
              are accepted and wrapped.
"""

from __future__ import annotations

from typing import Any, Iterable


class GStudioError(Exception):
    """Base class for every engine error."""


class StageError(GStudioError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}

    def to_dict(self) -> dict:
        return {"error": self.message, "details": self.details}


def _as_item(e: Any) -> dict:
    if isinstance(e, dict):
        item = {"path": str(e.get("path", "")), "msg": str(e.get("msg", e.get("message", "")))}
        if e.get("fix"):
            item["fix"] = str(e["fix"])
        if e.get("code"):
            item["code"] = str(e["code"])
        return item
    return {"path": "", "msg": str(e)}


def format_error(item: dict) -> str:
    path = item.get("path") or "spec"
    s = f"{path}: {item.get('msg', '')}"
    if item.get("fix"):
        s += f" Fix: {item['fix']}"
    return s


class SpecError(GStudioError):
    """Invalid design spec. `errors` is a list of {"path", "msg", "fix"?} dicts."""

    def __init__(self, errors: Iterable[Any]):
        items = [_as_item(e) for e in errors] or [{"path": "", "msg": "invalid spec"}]
        self.errors: list[dict] = items
        super().__init__(self.as_text())

    def as_text(self, limit: int = 25) -> str:
        lines = [format_error(e) for e in self.errors[:limit]]
        if len(self.errors) > limit:
            lines.append(f"... and {len(self.errors) - limit} more errors")
        return "\n".join(lines)

    def messages(self) -> list[str]:
        return [format_error(e) for e in self.errors]

    def to_dict(self) -> dict:
        return {"error": "invalid spec", "errors": self.errors}


__all__ = ["GStudioError", "StageError", "SpecError", "format_error"]
