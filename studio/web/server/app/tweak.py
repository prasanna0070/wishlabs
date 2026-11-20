"""Direct parameter editing: JSON merge patch (RFC 7386) with id-keyed feature merges and index-keyed decor
merges, plus a uniform body scale. Pure functions."""
from __future__ import annotations

import copy
from typing import Any


class TweakError(ValueError):
    pass


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def apply_tweak(spec: dict, patch: dict | None, scale: float | None = None) -> dict:
    patch = copy.deepcopy(patch or {})
    if not isinstance(patch, dict):
        raise TweakError("patch must be a JSON object")
    feats_patch = patch.pop("features", None) if isinstance(patch.get("features"), dict) else None
    decor_patch = patch.pop("decor", None) if isinstance(patch.get("decor"), dict) else None
    for k in ("schema",):
        if k in patch:
            raise TweakError(f"{k} cannot be changed")
    out = merge_patch(spec, patch)
    if feats_patch is not None:
        feats = out.get("features") or []
        by_id = {f.get("id"): i for i, f in enumerate(feats)}
        removed = set()
        for fid, fp in feats_patch.items():
            if fid not in by_id:
                raise TweakError(f"features: no feature with id '{fid}'")
            if fp is None:
                removed.add(by_id[fid])
            elif isinstance(fp, dict):
                if "id" in fp and fp["id"] != fid:
                    raise TweakError("features: a feature id cannot be renamed by a tweak")
                feats[by_id[fid]] = merge_patch(feats[by_id[fid]], fp)
            else:
                raise TweakError(f"features.{fid}: must be an object or null")
        out["features"] = [f for i, f in enumerate(feats) if i not in removed]
    if decor_patch is not None:
        decor = out.get("decor") or []
        removed = set()
        for key, dp in decor_patch.items():
            try:
                i = int(key)
            except (TypeError, ValueError):
                raise TweakError(f"decor: '{key}' is not a list index") from None
            if not 0 <= i < len(decor):
                raise TweakError(f"decor: index {i} is out of range (0..{len(decor) - 1})")
            if dp is None:
                removed.add(i)
            elif isinstance(dp, dict):
                decor[i] = merge_patch(decor[i], dp)
            else:
                raise TweakError(f"decor.{i}: must be an object or null")
        out["decor"] = [d for i, d in enumerate(decor) if i not in removed]
    if scale is not None and scale != 1:
        out = scale_spec(out, scale)
    return out


def _r(x: float) -> float:
    return round(float(x), 2)


def _pt(p, s):
    return [_r(p[0] * s), _r(p[1] * s)] if isinstance(p, (list, tuple)) and len(p) == 2 else p


def scale_spec(spec: dict, s: float) -> dict:
    if not (0.7 <= float(s) <= 1.4):
        raise TweakError("scale must be between 0.7 and 1.4")
    out = copy.deepcopy(spec)
    body = out.get("body") or {}
    if body.get("outline"):
        body["outline"] = [_pt(p, s) for p in body["outline"]]
    if body.get("profile"):
        body["profile"] = [_pt(p, s) for p in body["profile"]]
    if body.get("depth") is not None:
        body["depth"] = _r(body["depth"] * s)
    if body.get("edge_round") is not None:
        body["edge_round"] = _r(body["edge_round"] * s)
    if isinstance(body.get("csg"), dict):                  # pack freeform: every length in the csg tree
        body["csg"] = scale_csg(body["csg"], s)
    for pr in body.get("primitives") or []:
        for k in ("center", "size"):
            if isinstance(pr.get(k), list):
                pr[k] = [_r(v * s) for v in pr[k]]
        if pr.get("radius") is not None:
            pr["radius"] = _r(pr["radius"] * s)
    for d in out.get("decor") or []:
        if d.get("at"):
            d["at"] = _pt(d["at"], s)
        if d.get("size"):
            d["size"] = _pt(d["size"], s)
        if d.get("height") is not None:
            d["height"] = _r(d["height"] * s)
    for f in out.get("features") or []:
        if f.get("at"):
            f["at"] = _pt(f["at"], s)
        g = f.get("grille")
        if isinstance(g, dict):
            for k in ("w", "h"):
                if g.get(k) is not None:
                    g[k] = _r(g[k] * s)
    return out


# csg node keys that are lengths (contract _notes/freeform-contract.md section 3); rot and axis are not
CSG_LENGTHS = ("size", "round", "r", "h", "r1", "r2", "R", "points", "to", "blend")


def scale_csg(node: Any, s: float) -> Any:
    """A csg tree with every length times s: the engine's gstudio.csg.scale_tree when it is there, else the same rule
    here (sizes, radii, heights, rounds, blends, outline points and move offsets; turns and axes stay)."""
    try:
        from gstudio import csg as _csg  # noqa: PLC0415 - the engine owns the language

        fn = getattr(_csg, "scale_tree", None)
        if callable(fn):
            t = copy.deepcopy(node)
            r = fn(t, s)
            return t if r is None else r
    except Exception:  # noqa: BLE001 - engine without the pack: scale here
        pass
    return _scale_node(copy.deepcopy(node), s)


def _scale_node(node: Any, s: float) -> Any:
    if isinstance(node, list):
        return [_scale_node(n, s) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k in ("node", "nodes", "minus"):
            out[k] = _scale_node(v, s)
        elif k in CSG_LENGTHS:
            out[k] = _scale_len(v, s)
        else:
            out[k] = v
    return out


def _scale_len(v: Any, s: float) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return _r(v * s)
    if isinstance(v, list):
        return [_scale_len(x, s) for x in v]
    return v


def describe_tweak(patch: dict | None, scale: float | None) -> str:
    bits: list[str] = []
    p = patch or {}
    if isinstance(p.get("colors"), dict):
        names = p["colors"].get("body_name")
        bits.append(f"colour to {names}" if names else "colours")
    if isinstance(p.get("body"), dict):
        b = p["body"]
        if "edge_round" in b:
            bits.append(f"edge roundness to {b['edge_round']} mm")
        other = [k for k in b if k != "edge_round"]
        if other:
            bits.append("body " + ", ".join(other))
    if isinstance(p.get("features"), dict):
        for fid, fp in p["features"].items():
            bits.append(f"removed {fid}" if fp is None else f"moved {fid}" if isinstance(fp, dict) and "at" in fp
                        else f"changed {fid}")
    elif "features" in p:
        bits.append("features")
    if isinstance(p.get("decor"), dict):
        for k, dp in p["decor"].items():
            bits.append(f"removed decor item {k}" if dp is None else f"changed decor item {k}")
    elif "decor" in p:
        bits.append("decor")
    for k in p:
        if k not in ("colors", "body", "features", "decor"):
            bits.append(k)
    if scale and scale != 1:
        bits.append(f"size x{scale:g}")
    return "Tweaked " + (", ".join(bits) if bits else "the design")
