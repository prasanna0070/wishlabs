"""Orderable PCBA data (REVIEW-1 MAJ-22): a pinned maker, MPN and LCSC id for every BOM line, and a populated-board
(PCBA) cost model the order stage can price BP1 from.

Part identities come from `parts.json` (each read on its LCSC or JLCPCB page; see `url`, `checked_on`), unless
backplane-v1 `parts[]` carries its own `mpn`/`lcsc`/`maker` for that ref, which wins. Prices are planning ranges with
their basis stated; nothing here is a quote.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

PARTS_FILE = Path(__file__).with_name("parts.json")


@lru_cache(maxsize=1)
def parts_db() -> dict:
    return json.loads(PARTS_FILE.read_text())


def identity(part: Any) -> Optional[dict]:
    """{key, maker, mpn, lcsc, url, jlc_library, checked_on, desc, unit_usd_planning, source} or None (not pinned)."""
    db = parts_db()
    bom = getattr(part, "bom", {}) or {}
    if bom.get("bp_mpn"):
        return {"key": None, "maker": bom.get("bp_maker") or "", "mpn": bom["bp_mpn"], "lcsc": bom.get("bp_lcsc") or "",
                "url": bom.get("bp_url") or "", "jlc_library": None, "checked_on": bom.get("bp_checked_on") or "",
                "desc": "", "unit_usd_planning": None, "source": "backplane-v1 parts[]"}
    key = bom.get("part_key") or db["by_footprint"].get(getattr(part, "footprint", ""))
    if not key or key not in db["parts"]:
        return None
    row = dict(db["parts"][key])
    row.update(key=key, checked_on=row.get("checked_on") or db["checked_on"], source="gstudio/pcb/parts.json")
    return row


def is_smt(footprint: str) -> bool:
    return not any(k in footprint for k in ("PinSocket", "JST", "PinHeader"))


# --------------------------------------------------------------------------- PCBA cost model


def _rng(lo: float, hi: float) -> dict:
    return {"low": round(lo, 4), "high": round(hi, 4)}


def pcba_cost(lines: list[dict], joints: dict, design_id: str, batches: tuple[int, ...] = (5, 100)) -> dict:
    """Cost of a populated BP1 per board, for a pilot batch and a volume batch.

    lines: [{refs, qty, mpn, lcsc, jlc_library, unit_usd_planning, fitting}] (one per bom.csv part line);
    joints: {"smt": n, "manual": n} solder joints per board, counted from the routed board's pads."""
    db = parts_db()
    asm, bare, ship, fx = db["assembly"], db["bare_board"], db["shipping_to_india"], db["usd_inr"]
    missing = [ln["refs"] for ln in lines if not ln.get("mpn") or not ln.get("unit_usd_planning")]
    comp_lo = sum(ln["qty"] * ln["unit_usd_planning"][0] for ln in lines if ln.get("unit_usd_planning"))
    comp_hi = sum(ln["qty"] * ln["unit_usd_planning"][1] for ln in lines if ln.get("unit_usd_planning"))
    ext_known = sum(1 for ln in lines if ln.get("fitting") == "SMT" and ln.get("jlc_library") == "extended")
    ext_maybe = sum(1 for ln in lines if ln.get("fitting") == "SMT" and ln.get("jlc_library") not in ("basic", "extended"))
    smt_j, man_j = int(joints.get("smt", 0)), int(joints.get("manual", 0))
    out_batches = {}
    for n in batches:
        fixed_lo = asm["setup_usd"] + asm["stencil_usd"] + (asm["hand_soldering_labor_usd_per_order"] if man_j else 0.0) \
            + asm["extended_part_fee_usd"] * ext_known
        fixed_hi = fixed_lo + asm["extended_part_fee_usd"] * ext_maybe
        per_order_board = bare["usd_per_order_planning"]
        board_lo = per_order_board[0] / bare["order_qty"] * n
        board_hi = per_order_board[1] / bare["order_qty"] * n
        var = n * (smt_j * asm["smt_usd_per_joint"] + man_j * asm["manual_usd_per_joint"])
        lo = board_lo + n * comp_lo + var + fixed_lo + ship["usd_per_order_planning"][0]
        hi = board_hi + n * comp_hi + var + fixed_hi + ship["usd_per_order_planning"][1]
        out_batches[str(n)] = {
            "boards": n,
            "usd_per_board": _rng(lo / n, hi / n),
            "inr_per_board": {"low": round(lo / n * fx["low"]), "high": round(hi / n * fx["high"])},
            "parts_usd": {"bare_board": _rng(board_lo / n, board_hi / n), "components": _rng(comp_lo, comp_hi),
                          "assembly": _rng(var / n + fixed_lo / n, var / n + fixed_hi / n),
                          "shipping_to_india": _rng(ship["usd_per_order_planning"][0] / n,
                                                    ship["usd_per_order_planning"][1] / n)},
        }
    return {
        "schema": "gadget-pcba/1",
        "design_id": design_id,
        "status": "ESTIMATE",
        "status_label": "Planning estimate: no fab quote taken",
        "what": "BP1 backplane PCBA: bare board, every bom.csv component, SMT and manual through-hole assembly, "
                "shipping to India",
        "includes_usb_c_receptacle": any("J_PWR" in ln["refs"].split() for ln in lines),
        "excludes": [ship["excludes"], "GST (the order stage applies it)"],
        "complete": not missing,
        "missing": missing,
        "joints_per_board": {"smt": smt_j, "manual": man_j,
                             "basis": "pad count of the routed board: SMD pads -> SMT joints, plated through-hole pads of "
                                      "connectors and sockets -> manual joints"},
        "unique_extended_lines": {"confirmed": ext_known, "unconfirmed": ext_maybe},
        "batches": out_batches,
        "assumptions": {
            "assembly": {k: asm[k] for k in ("service", "setup_usd", "stencil_usd", "smt_usd_per_joint",
                                             "manual_usd_per_joint", "hand_soldering_labor_usd_per_order",
                                             "extended_part_fee_usd", "extended_fee_basis", "source", "page_updated",
                                             "checked_on")},
            "bare_board": bare,
            "shipping_to_india": {k: ship[k] for k in ("usd_per_order_planning", "basis")},
            "usd_inr": fx,
            "component_prices": "planning ranges per part (parts.json unit_usd_planning); LCSC prices were not readable "
                                "on 2026-11-01",
        },
        "lines": lines,
    }


__all__ = ["parts_db", "identity", "is_smt", "pcba_cost"]
