"""Pack words (catalog plan 0.5.5, A-hooks): a library row's optional `manual` block names its part in the guide.

A pack row (library/packs/<pack>.json, merged into library/modules.json) may carry

    "manual": {
      "word": "soil probe",          # REQUIRED short name: step titles ("Fit the soil probe") and matching its CAD parts
      "noun": "soil probe",          # noun used in sentences; default = word
      "part_kind": "component",      # wiring parts table kind: controller | board | component | power (the output
                                     # contract's four values, docs/ref/benchmark-output-contract.md); default board
      "mount_type": "cable exit",    # how it is held: a library mount type (panel_snap, panel, holes, screws, adhesive,
                                     # clip, cradle, press_fit, header, none), "cable exit" (the part stays outside and its
                                     # lead passes a strain-relieved exit) or a free phrase; default = the row's mount
      "face_part": "probe",          # optional: the part of it that faces out ("probe toward the front"); default "front"
      "selftest": "the soil reading changes when the probe touches damp soil",   # optional self-test expectation
      "care": "Keep the electronics end of the probe above the soil.",           # optional handling line in its fit step
      "fit": "Pass the probe lead through the cable exit from outside."           # optional: replaces the generic seat line
    }

Words must be plain user-facing text: no part ids, and no numbers that are not in the row's own data (the guide never
states a number it cannot trace). The block is read before the built-in role tables (model.ROLE_NOUN / ROLE_WORDS,
steps.FACE_PART / SELFTEST_CHECK, wiring.PART_KIND, steps.mount_kind); a role with no block keeps its built-in words.

Scope: `learn(data)` collects the blocks of the rows one design uses and makes them the active vocabulary for the
current thread/context (a ContextVar), so one design's words never leak into another build in the same process.
Pure Python: no CadQuery, VTK or engine imports (the manual imports it before any heavy module).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Optional

FIELDS = ("word", "noun", "part_kind", "mount_type", "face_part", "selftest", "care", "fit")
PART_KINDS = ("controller", "board", "component", "power")  # benchmark-output-contract.md wiring parts[].kind

_ACTIVE: ContextVar[dict] = ContextVar("gstudio_manual_words", default={})


def block(row: Any) -> dict:
    """The normalized `manual` block of a library row ({} when the row has none or it has no word)."""
    if not isinstance(row, dict):
        return {}
    raw = row.get("manual")
    if not isinstance(raw, dict):
        return {}
    out = {k: " ".join(str(raw[k]).split()) for k in FIELDS if isinstance(raw.get(k), (str, int, float)) and str(raw[k]).strip()}
    if not out.get("word"):
        if not out.get("noun"):
            return {}
        out["word"] = out["noun"]
    out["word"] = out["word"].lower()
    out.setdefault("noun", out["word"])
    if out.get("part_kind") and out["part_kind"].lower() not in PART_KINDS:
        out["part_kind"] = "board"  # keep the wiring table inside its contract
    elif out.get("part_kind"):
        out["part_kind"] = out["part_kind"].lower()
    return out


def row_role(row: Any) -> Optional[str]:
    """The canonical role of a row that has a manual block, else None."""
    if not block(row):
        return None
    r = row.get("role") if isinstance(row, dict) else None
    return str(r).lower() if r else None


def learn(data: Any, extra_rows: Iterable[dict] = ()) -> dict:
    """Collect {role: block} from the rows this design uses (electrical/derived instances and CAD components) and make
    it the active vocabulary. Returns the vocabulary. Never raises: a broken row just has no words."""
    vocab: dict[str, dict] = {}
    rows: list[dict] = list(extra_rows)
    try:
        mids = []
        for inst in getattr(data, "instances", None) or []:
            if isinstance(inst, dict) and inst.get("module"):
                mids.append(inst["module"])
        for c in (getattr(data, "comps", None) or {}).values():
            if getattr(c, "module_id", None):
                mids.append(c.module_id)
        seen = set()
        for mid in mids:
            if mid in seen:
                continue
            seen.add(mid)
            try:
                rows.append(data.lib.get(mid))
            except Exception:
                continue
    except Exception:
        pass
    for row in rows:
        b = block(row)
        role = row_role(row)
        if b and role and role not in vocab:
            vocab[role] = b
    _ACTIVE.set(vocab)
    return vocab


def activate(vocab: Optional[dict]) -> None:
    """Set the active vocabulary directly ({role: block}); tests and callers that already hold the blocks."""
    _ACTIVE.set(dict(vocab or {}))


def active() -> dict:
    return _ACTIVE.get()


def word(role: Optional[str], field: str, default: Any = None) -> Any:
    """One field of the active block for `role`, else default."""
    if not role:
        return default
    v = (_ACTIVE.get().get(str(role).lower()) or {}).get(field)
    return v if v else default


class RoleTable(dict):
    """A built-in role table (role -> word) that answers first from the active pack words for that role.

    Call sites keep their `TABLE.get(role, default)` / `role in TABLE` / `TABLE[role]` form. Iteration, len and items
    stay the built-in table only, so fuzzy word matching (model.role_of) never sees pack words."""

    def __init__(self, base: dict, field: str, wrap=None):
        super().__init__(base)
        self.field = field
        self.wrap = wrap

    def _pack(self, key: Any) -> Any:
        if not isinstance(key, str):
            return None
        v = word(key, self.field)
        if v is None:
            return None
        return self.wrap(v, dict.get(self, key)) if self.wrap else v

    def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        v = self._pack(key)
        if v is not None:
            return v
        return dict.get(self, key, default)

    def __getitem__(self, key: Any) -> Any:
        v = self._pack(key)
        if v is not None:
            return v
        return dict.__getitem__(self, key)

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self, key) or self._pack(key) is not None


def words_list(v: str, base: Optional[list]) -> list[str]:
    """ROLE_WORDS wrap: the pack word in front of any built-in words for that role."""
    return [v] + [w for w in (base or []) if w != v]
