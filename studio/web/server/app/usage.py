"""Per-turn token and cost logging. Token counts come from the model's usage metadata. Costs are ESTIMATES from
configured per-unit rates (env MODEL_PRICES_JSON overrides); they are not billing records."""
from __future__ import annotations

import json
import os
from typing import Any

# USD per 1M tokens (in, out) and per generated image. Placeholder rates: unverified for these exact model ids;
# set MODEL_PRICES_JSON from the Vertex AI pricing page before relying on them.
DEFAULT_PRICES: dict[str, dict] = {
    "gemini-3.1-pro-preview": {"in": 2.0, "out": 12.0},
    "gemini-3.5-flash": {"in": 0.5, "out": 3.0},
    "gemini-3-pro-image": {"in": 2.0, "out": 12.0, "image": 0.134},
}
BASIS = ("Token counts from Vertex AI usage metadata. cost_usd_est uses configured per-unit rates (MODEL_PRICES_JSON; "
         "defaults are unverified placeholders), not billing data.")


def prices() -> dict[str, dict]:
    raw = os.environ.get("MODEL_PRICES_JSON")
    if raw:
        try:
            return {**DEFAULT_PRICES, **json.loads(raw)}
        except json.JSONDecodeError:
            pass
    return DEFAULT_PRICES


def record_from_response(model: str, resp: Any, kind: str = "text", seconds: float | None = None,
                         images: int = 0) -> dict:
    um = getattr(resp, "usage_metadata", None)
    g = lambda a: int(getattr(um, a, 0) or 0) if um is not None else 0  # noqa: E731
    rec = {"model": model, "kind": kind, "input_tokens": g("prompt_token_count"),
           "output_tokens": g("candidates_token_count"), "thinking_tokens": g("thoughts_token_count"),
           "cached_tokens": g("cached_content_token_count"), "images": images,
           "seconds": round(seconds, 2) if seconds is not None else None}
    rec["cost_usd_est"] = cost(rec)
    return rec


def cost(rec: dict) -> float:
    p = prices().get(rec.get("model", ""), {})
    c = rec.get("input_tokens", 0) / 1e6 * p.get("in", 0.0)
    c += (rec.get("output_tokens", 0) + rec.get("thinking_tokens", 0)) / 1e6 * p.get("out", 0.0)
    c += rec.get("images", 0) * p.get("image", 0.0)
    return round(c, 6)


KEYS = ("input_tokens", "output_tokens", "thinking_tokens", "cached_tokens", "images", "cost_usd_est")


def sum_records(recs: list[dict]) -> dict:
    out = {k: 0 for k in KEYS}
    for r in recs:
        for k in KEYS:
            out[k] += r.get(k) or 0
    out["cost_usd_est"] = round(out["cost_usd_est"], 6)
    return out


def apply_turn(p: dict, turn_rec: dict, keep: int = 200) -> None:
    u = p.get("usage") or {"basis": BASIS, "turns": [], "totals": {**{k: 0 for k in KEYS}, "turns": 0}}
    p["usage"] = u
    u["basis"] = BASIS
    u["turns"] = (u.get("turns") or [])[-(keep - 1):] + [turn_rec]
    t = u.setdefault("totals", {})
    for k in KEYS:
        t[k] = round((t.get(k) or 0) + (turn_rec.get(k) or 0), 6)
    t["turns"] = int(t.get("turns") or 0) + 1
