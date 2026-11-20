"""Brief trace (MAJ-37): every approved brief item (functions, details, size, power) gets a row with a status and
the evidence that backs it, taken from the version's own files. Nothing here is written by the model.

status: implemented (digitally traced to a part, a test or a measurement; not a physical test),
        not_met (the files contradict the brief), unverified (no automatic evidence; check it yourself).
"""
from __future__ import annotations

import re
from typing import Any

SIZE_TOL = 0.05

# role -> (words that name it in a brief item, firmware unit-test name pattern)
ROLES: dict[str, tuple[tuple[str, ...], str]] = {
    "mic": (("microphone", "mic ", "voice", "listen", "record", "say a", "says a", "tell"),
            r"hold_records|mic_conversion|parent_web_mode_never_opens_the_mic"),
    "speaker": (("speaker", "story", "stories", "audio", "sound", "play", "read aloud", "narrat"),
                r"tap_plays|story_end|wav_header|volume_curve"),
    "button": (("button", "tap", "press", "control"), r"tap_is_press|button_fsm|long_press"),
    "display": (("screen", "display", "eyes", "animated face"), r"face_blinks|scenes"),
    "led": (("light", "glow", "lamp", "led"), r"night_light|indicator"),
    "storage": (("remember", "memory", "favourite", "favorite", "saved", "keeps", "storage", "stored", "kept"),
                r"saved_name_survives_restart|power_cut_mid_write|clip_favourites"),
}
ROLE_ORDER = ("mic", "display", "led", "button", "storage", "speaker")
STORAGE_ROLES = ("storage",)


def _v(x: Any) -> str:
    return str(x.get("value") or "") if isinstance(x, dict) else str(x or "")


def fw_results(text: str | None) -> dict[str, str]:
    """firmware/test-results.txt -> {test_name: PASS|FAIL|...}"""
    out: dict[str, str] = {}
    for m in re.finditer(r"(test_[A-Za-z0-9_]+):(PASS|FAIL|IGNORE|SKIP)", text or ""):
        out[m.group(1)] = m.group(2)
    return out


def roles_for(title: str, value: str) -> list[str]:
    def found(text: str) -> list[str]:
        low = f" {text.lower()} "
        return [r for r in ROLE_ORDER if any(w in low for w in ROLES[r][0])]

    return found(title) or found(value)


def _instances(electrical: Any, role: str) -> list[dict]:
    inst = (electrical or {}).get("instances") if isinstance(electrical, dict) else None
    return [i for i in inst or [] if isinstance(i, dict) and str(i.get("role")) == role]


def cad_size(components: Any) -> dict | None:
    """Overall size of the printed parts as they stand on the table (z from 0), from cad/components.json."""
    if not isinstance(components, dict):
        return None
    boxes = [c.get("bbox") for k, c in components.items()
             if isinstance(c, dict) and (str(k).startswith("PRINT_") or c.get("kind") == "PRINT")
             and isinstance(c.get("bbox"), list) and len(c["bbox"]) == 6]
    if not boxes:
        return None
    x0, x1 = min(b[0] for b in boxes), max(b[1] for b in boxes)
    y0, y1 = min(b[2] for b in boxes), max(b[3] for b in boxes)
    z1 = max(b[5] for b in boxes)
    return {"w_mm": round(x1 - x0, 1), "d_mm": round(y1 - y0, 1), "h_mm": round(z1, 1),
            "source": "cad/components.json (union of PRINT_* boxes, height from the table)"}


def _size_row(brief: dict, cad: dict | None) -> dict | None:
    size = brief.get("size") if isinstance(brief.get("size"), dict) else None
    if not size or not size.get("w_mm"):
        return None
    item = f"Size: {size.get('value') or ''}".strip()
    if cad is None:
        return {"item": item, "kind": "size", "status": "unverified", "evidence": "No CAD output in this version.",
                "paths": []}
    axes = [("W", "w_mm"), ("D", "d_mm"), ("H", "h_mm")]
    if size.get("scope") == "body":
        axes = axes[:2]
    bad, ok = [], []
    for lab, k in axes:
        b, c = size.get(k), cad.get(k)
        if not isinstance(b, (int, float)) or not c:
            continue
        rel = abs(c - b) / c
        (bad if rel > SIZE_TOL else ok).append(f"{lab} brief {round(b)} vs CAD {round(c)} mm")
    ev = "; ".join(bad or ok)
    if size.get("scope") == "body":
        ev += f"; height not compared (the brief gives the body only; CAD overall height {round(cad['h_mm'])} mm)"
    return {"item": item, "kind": "size", "status": "not_met" if bad else "implemented",
            "evidence": ev + f" (within {int(SIZE_TOL * 100)}%)" * (not bad), "paths": [cad["source"]]}


def _role_evidence(role: str, electrical: Any, tests: dict[str, str], story: dict | None,
                   build: dict) -> tuple[str, list[str], list[str]]:
    """(status, evidence bits, paths) for one role."""
    ev, paths = [], []
    status = "implemented"
    if role in STORAGE_ROLES:
        inst = _instances(electrical, "storage")
        if inst:
            ev += [f"{i.get('ref')} {i.get('module')} on {i.get('slot') or 'the backplane'}" for i in inst[:1]]
            paths.append("electrical.json")
    else:
        inst = _instances(electrical, role)
        if not inst:
            return "not_met", [f"no {role} module in electrical.json"], ["electrical.json"]
        ev += [f"{i.get('ref')} {i.get('module')} on {i.get('slot') or 'the backplane'}" for i in inst[:2]]
        paths.append("electrical.json")
    pat = re.compile(ROLES[role][1])
    hits = [(n, r) for n, r in tests.items() if pat.search(n)]
    if hits:
        fails = [n for n, r in hits if r == "FAIL"]
        if fails:
            status = "not_met"
            ev.append("firmware test failed: " + ", ".join(fails[:2]))
        else:
            ev.append("firmware test " + ", ".join(f"{n} PASS" for n, _ in hits[:2]))
        paths.append("firmware/test-results.txt")
    elif role in STORAGE_ROLES and not inst:
        return "unverified", ["no storage part or memory test found"], []
    if role == "speaker" and story is not None and story.get("present") is False:
        status = "not_met"
        ev.append("stories have no audio yet (" + str(story.get("source")) + ")")
        paths.append("firmware/sdcard/stories.json")
    return status, ev, paths


def _detail_row(d: dict, quote: Any, build: dict, stories: Any) -> dict | None:
    topic = str(d.get("topic") or "")
    item = f"{topic}: {_v(d)}".strip(": ")
    low = (topic + " " + _v(d)).lower()
    if "country" in low or "sourcing" in low:
        n = len([p for p in build.get("purchased") or [] if isinstance(p, dict)])
        if isinstance(quote, dict) and n:
            cur = quote.get("currency") or ("INR" if any(k.endswith("_inr") for k in quote) else None)
            if cur == "INR" or "india" in low:
                return {"item": item, "kind": "detail", "status": "implemented",
                        "evidence": f"{n} purchased rows with sources; quote priced in INR",
                        "paths": ["build.json", "order/quote.json"]}
    if "language" in low and isinstance(stories, dict) and stories.get("language"):
        lang = str(stories["language"])
        ok = lang.lower().startswith("en") and ("english" in low or lang.lower() in low)
        return {"item": item, "kind": "detail", "status": "implemented" if ok else "unverified",
                "evidence": f"story pack language {lang}", "paths": ["firmware/sdcard/stories.json"]}
    return None


def _printed_row(item: str, kind: str, roles: list[str]) -> dict:
    return {"item": item, "kind": kind, "status": "unverified",
            "evidence": f"A printed object has no electronics, so the words that suggest a {' or '.join(roles[:2])} here "
                        "describe its shape or decor; check them against the build files.", "paths": []}


def requirements(brief: dict | None, *, electrical: Any = None, build: Any = None, components: Any = None,
                 fw_text: str | None = None, story: dict | None = None, stories: Any = None,
                 quote: Any = None, printed: bool = False) -> list[dict]:
    """[{item, kind, status, evidence, paths}] in brief order: functions, size, power, details. `printed` (spec kind
    "printed", pack `printed`): the object has no electronics, so words like "eyes", "playful" or "light blue" describe
    its shape or decor, not a module; such items are unverified (check them yourself), never "not met" for a missing
    module."""
    if not isinstance(brief, dict):
        return []
    build = build if isinstance(build, dict) else {}
    tests = fw_results(fw_text)
    rows: list[dict] = []
    for fx in brief.get("functions") or []:
        if not isinstance(fx, dict):
            continue
        title, value = str(fx.get("title") or ""), _v(fx)
        item = f"{title}: {value}".strip(": ")
        roles = roles_for(title, value)
        if roles and printed:
            rows.append(_printed_row(item, "function", roles))
            continue
        if not roles:
            rows.append({"item": item, "kind": "function", "status": "unverified",
                         "evidence": "No automatic evidence for this item; check it against the build files.",
                         "paths": []})
            continue
        status, ev, paths = "implemented", [], []
        for r in roles[:2]:
            st, e, pth = _role_evidence(r, electrical, tests, story, build)
            ev += e
            paths += [p for p in pth if p not in paths]
            if st == "not_met":
                status = "not_met"
            elif st == "unverified" and status == "implemented" and len(roles) == 1:
                status = "unverified"
        rows.append({"item": item, "kind": "function", "status": status, "evidence": "; ".join(ev), "paths": paths})
    sr = _size_row(brief, cad_size(components))
    if sr:
        rows.append(sr)
    pw = brief.get("power")
    if _v(pw):
        inst = _instances(electrical, "power_in")
        if inst:
            rows.append({"item": f"Power: {_v(pw)}", "kind": "power", "status": "implemented",
                         "evidence": f"{inst[0].get('ref')} {inst[0].get('module')} (USB-C 5 V input)",
                         "paths": ["electrical.json"]})
        else:
            rows.append({"item": f"Power: {_v(pw)}", "kind": "power", "status": "unverified",
                         "evidence": "No power input found in electrical.json.", "paths": []})
    for d in brief.get("details") or []:
        if not (isinstance(d, dict) and _v(d)):
            continue
        row = _detail_row(d, quote, build, stories)
        if row is None:
            item = f"{d.get('topic') or ''}: {_v(d)}".strip(": ")
            roles = roles_for(str(d.get("topic") or ""), _v(d))
            if roles and printed:
                row = _printed_row(item, "detail", roles)
            elif roles:
                status, ev, paths = "implemented", [], []
                for r in roles[:2]:
                    st, e, pth = _role_evidence(r, electrical, tests, story, build)
                    ev += e
                    paths += [x for x in pth if x not in paths]
                    status = "not_met" if st == "not_met" else (status if st == "implemented" else "unverified")
                row = {"item": item, "kind": "detail", "status": status, "evidence": "; ".join(ev), "paths": paths}
            else:
                row = {"item": item, "kind": "detail", "status": "unverified",
                       "evidence": "No automatic evidence for this item; check it against the build files.",
                       "paths": []}
        rows.append(row)
    return rows
