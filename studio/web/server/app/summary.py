"""Build summary written from the version's files, never by the model, so every number traces to a file.

Reads (all optional, shapes handled defensively): build.json, manifest.json, checks/digital-checks.json,
order/quote.json, status.json, manual/manual-summary.json, firmware/sdcard/stories.json, plus the files the brief
trace needs (see trace.py).
"""
from __future__ import annotations

from typing import Any

PHYSICAL_SHOWN = 3
FAILED_SHOWN = 4
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
PASS = ("passed", "pass", "ok", "true", "done")
WARN = ("warning", "warn", "warnings")
SKIP = ("skipped", "not_run", "n/a", "info", "pending", "not_selected")
STORY_CHECK = "story-audio-present"


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _inr(x: float) -> str:
    s = f"{int(round(x)):,}"
    # Indian grouping: 1,23,456
    digits = str(int(round(x)))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return "₹" + s


def _items(dc: Any) -> list[dict]:
    items: list = []
    if isinstance(dc, dict):
        for k in ("checks", "results", "items"):
            if isinstance(dc.get(k), list):
                items = dc[k]
                break
        else:
            items = [{"name": k, **v} if isinstance(v, dict) else {"name": k, "passed": v}
                     for k, v in dc.items() if isinstance(v, (dict, bool)) and k not in ("limits", "summary", "stages")]
    elif isinstance(dc, list):
        items = dc
    return [it for it in items if isinstance(it, dict)]


def _title(it: dict) -> str:
    return str(it.get("title") or it.get("name") or it.get("id") or "check")


def check_counts(dc: Any) -> dict:
    """Passed, failed and warned kept apart (a warning is not a pass and not a failure).
    {passed, failed: [titles], warned: [titles], total, by_id: {id: status}}"""
    out = {"passed": 0, "failed": [], "warned": [], "total": 0, "by_id": {}}
    for it in _items(dc):
        st = next((it[k] for k in ("status", "passed", "ok", "result") if it.get(k) is not None), None)
        if st is None:
            continue
        if isinstance(st, str):
            low = st.strip().lower()
            if low in SKIP:
                continue
            kind = "passed" if low in PASS else "warned" if low in WARN else "failed"
        else:
            kind = "passed" if bool(st) else "failed"
        out["total"] += 1
        if it.get("id"):
            out["by_id"][str(it["id"])] = kind
        if kind == "passed":
            out["passed"] += 1
        else:
            out[kind].append(_title(it))
    return out


def digital_checks(dc: Any) -> tuple[int, int, list[str]]:
    """(passed, total, failed titles). Warnings count in the total but never as passed."""
    c = check_counts(dc)
    return c["passed"], c["total"], c["failed"]


def checks_sentence(c: dict) -> str:
    """'Digital checks: 83 passed, 4 failed (a; b; c; d), 3 warnings.'"""
    bits = [f"{c['passed']} passed"]
    if c["failed"]:
        shown = c["failed"][:FAILED_SHOWN]
        more = len(c["failed"]) - len(shown)
        bits.append(f"{len(c['failed'])} failed ({'; '.join(shown)}" + (f"; and {more} more" if more else "") + ")")
    else:
        bits.append("0 failed")
    if c["warned"]:
        bits.append(f"{len(c['warned'])} warning{'s' if len(c['warned']) != 1 else ''}")
    return "Digital checks: " + ", ".join(bits) + "."


def price_of(build: dict, quote: Any) -> dict:
    order = build.get("order") if isinstance(build, dict) else None
    inr = _num((order or {}).get("list_price_inr"))
    status = (order or {}).get("status")
    if isinstance(quote, dict):
        if inr is None:
            for k in ("list_price_inr", "list_price_incl_gst", "price_inr", "total_inr"):
                if _num(quote.get(k)) is not None:
                    inr = _num(quote.get(k))
                    break
            if inr is None and isinstance(quote.get("quote"), dict):
                inr = _num(quote["quote"].get("list_price_incl_gst") or quote["quote"].get("list_price_inr"))
        status = status or quote.get("status")
    if str(status or "").upper() == "HELD":
        why = (quote.get("held_because") if isinstance(quote, dict) else None) or (order or {}).get("held_because")
        why_s = "; ".join(str(w.get("message") or w.get("pair") or w) if isinstance(w, dict) else str(w)
                          for w in why[:3]) if isinstance(why, list) and why else ""
        return {"inr": None, "status": "HELD",
                "label": "Price held until the design passes its collision check" + (f" ({why_s})" if why_s else "")}
    if inr is None:
        reason = (order or {}).get("reason") or (quote.get("reason") if isinstance(quote, dict) else None)
        return {"inr": None, "status": status or "NOT_QUOTED",
                "label": "Price not quoted yet" + (f" ({reason})" if reason else "")}
    binding = (status or "").upper() == "BINDING"
    return {"inr": inr, "status": status or "ESTIMATE",
            "label": f"{_inr(inr)} delivered" + ("" if binding else " (estimate, not a binding price)")}


def image_counts(manual_summary: Any, files: list[str]) -> dict:
    """PNG-type images and SVG drawings counted apart, from manual-summary.json (MAJ-19), else the manifest."""
    ms = manual_summary if isinstance(manual_summary, dict) else {}
    png = ms.get("images_png")
    svg = ms.get("images_svg")
    if isinstance(png, list):
        png = len(png)
    if isinstance(svg, list):
        svg = len(svg)
    imgs = ms.get("images") if isinstance(ms.get("images"), list) else None
    if png is None and imgs is not None:
        png = sum(1 for p in imgs if str(p).lower().endswith(IMAGE_EXT))
    if svg is None and imgs is not None:
        svg = sum(1 for p in imgs if str(p).lower().endswith(".svg"))
    source = "manual/manual-summary.json" if (png is not None or svg is not None) else "manifest.json"
    if png is None:
        png = sum(1 for f in files if f.startswith("manual/") and f.lower().endswith(IMAGE_EXT))
    if svg is None:
        svg = sum(1 for f in files if f.startswith("manual/") and f.lower().endswith(".svg"))
    return {"png": int(png or 0), "svg": int(svg or 0), "source": source}


def story_audio_state(spec: Any, stories: Any, counts: dict) -> dict | None:
    """None when the design has no story card. present: True/False from the story-audio-present check, else from
    firmware/sdcard/stories.json (audio flag)."""
    tmpl = ((spec or {}).get("behaviour") or {}).get("template") if isinstance(spec, dict) else None
    if tmpl != "story_player" and not isinstance(stories, dict):
        return None
    st = counts["by_id"].get(STORY_CHECK)
    if st is not None:
        return {"present": st == "passed", "source": f"checks/digital-checks.json {STORY_CHECK}"}
    if isinstance(stories, dict):
        return {"present": bool(stories.get("audio")), "source": "firmware/sdcard/stories.json audio"}
    return {"present": None, "source": "no story index found"}


def decor_adjustment_lines(cad_summary: Any) -> list[str]:
    """REVIEW-1 MAJ-31: the CAD's own record of decor it scaled or clipped (cad-summary decor_adjustments), as sentences."""
    out = []
    for a in ((cad_summary or {}).get("decor_adjustments") or []) if isinstance(cad_summary, dict) else []:
        if isinstance(a, str):
            txt = a
        elif isinstance(a, dict):
            txt = a.get("message") or a.get("say") or a.get("what") or a.get("detail") or ""
            req, built = a.get("requested_mm"), a.get("built_mm")
            if not txt and isinstance(req, list) and isinstance(built, list) and len(req) == 2 and len(built) == 2:
                what = str(a.get("type") or "decor").replace("_", " ")
                txt = (f"the {what} ({a.get('spec_path')}) was {a.get('action') or 'changed'} from {req[0]:g} x {req[1]:g} mm "
                       f"to {built[0]:g} x {built[1]:g} mm" + (f", {a['why']}" if a.get("why") else ""))
            if not txt and a.get("from") and a.get("to"):
                txt = f"{a.get('label') or a.get('item') or 'decor'} changed from {a['from']} to {a['to']}" + (f" ({a['why']})" if a.get("why") else "")
        else:
            continue
        txt = str(txt).strip()
        if txt:
            out.append(("The CAD adjusted the design to fit: " + txt[0].lower() + txt[1:]).rstrip(".") + ".")
    if isinstance(cad_summary, dict) and isinstance(cad_summary.get("recolored"), dict):
        out.append("Colour-only change: the CAD reused the previous version's geometry and print files and recoloured them.")
    return out[:4]


def build_summary(name: str, status: dict | None, build: dict | None, manifest: dict | None, dc: Any,
                  quote: Any, brief_version: int | None, extra: dict | None = None) -> tuple[dict, str, str]:
    """Returns (BuildSummary payload, Markdown chat text, one-line version summary).
    extra (optional): manual_summary, stories, spec, requirements (from trace.requirements)."""
    status = status or {}
    build = build or {}
    extra = extra or {}
    state = status.get("state") or "error"
    made: list[str] = []
    notes: list[str] = []
    printed = [p for p in build.get("printed") or [] if isinstance(p, dict)]
    purchased = [p for p in build.get("purchased") or [] if isinstance(p, dict)]
    steps = [s for s in build.get("steps") or [] if isinstance(s, dict)]
    files = [f.get("path", "") for f in (manifest or {}).get("files") or [] if isinstance(f, dict)]
    imgs = image_counts(extra.get("manual_summary"), files)
    if printed:
        made.append(f"{name}'s printed shell: {len(printed)} print part{'s' if len(printed) != 1 else ''} "
                    "with STEP and STL files")
    if any(f.startswith("pcb/") for f in files) or build.get("pcb"):
        pcb = build.get("pcb") or {}
        if pcb.get("routed"):
            made.append(f"A routed {pcb.get('layers') or 2}-layer backplane PCB with Gerbers")
        elif pcb:
            made.append("A backplane PCB layout (routing not complete)")
    fw = build.get("firmware") or {}
    if fw.get("compiled"):
        made.append("Compiled ESP32-S3 firmware")
    if steps:
        pics = []
        if imgs["png"]:
            pics.append(f"{imgs['png']} image{'s' if imgs['png'] != 1 else ''}")
        if imgs["svg"]:
            pics.append(f"{imgs['svg']} SVG drawing{'s' if imgs['svg'] != 1 else ''}")
        made.append(f"An illustrated build manual: {len(steps)} steps" + (", " + " and ".join(pics) if pics else ""))
    if purchased:
        made.append(f"A parts list of {len(purchased)} purchased items with sources")

    counts = check_counts(dc)
    fw_tests = None
    if fw.get("tests_total"):
        fw_tests = f"{fw.get('tests_passed', 0)}/{fw.get('tests_total')}"
    pcb = build.get("pcb") or {}
    price = price_of(build, quote)
    story = story_audio_state(extra.get("spec"), extra.get("stories"), counts)
    buildable = status.get("buildable")
    reqs = extra.get("requirements") or []
    checks = {"digital_passed": counts["passed"], "digital_total": counts["total"],
              "digital_failed": counts["failed"], "digital_warned": len(counts["warned"]),
              "digital_warnings": counts["warned"],
              "firmware_compiled": bool(fw.get("compiled")), "firmware_tests": fw_tests,
              "pcb_drc_errors": pcb.get("drc_errors"), "pcb_erc_errors": pcb.get("erc_errors"),
              "manual_images": imgs["png"], "manual_svgs": imgs["svg"], "manual_images_source": imgs["source"],
              "story_audio": story}

    tests = [t for t in build.get("tests") or [] if isinstance(t, dict)]
    physical = [{"title": t.get("title", ""), "expected": t.get("expected", "")}
                for t in tests if t.get("kind") == "physical" and t.get("status") != "passed"]
    if build.get("placeholder"):
        notes.append("Development builder: placeholder files; no real build ran for this design.")
    elif status.get("fake"):
        notes.append("Development builder: these files were copied from a pre-built example, not built for this "
                     "design.")

    lines: list[str] = []
    if state == "done":
        lines += [f"- {m}." for m in made] or ["- Build finished."]
        if counts["total"]:
            lines.append("- " + checks_sentence(counts))
        tool_bits = []
        if fw_tests:
            tool_bits.append(f"Firmware unit tests {fw_tests}.")
        if pcb.get("drc_errors") is not None or pcb.get("erc_errors") is not None:
            pb = []
            if pcb.get("drc_errors") is not None:
                pb.append(f"DRC {pcb['drc_errors']} error{'s' if pcb['drc_errors'] != 1 else ''}")
            if pcb.get("erc_errors") is not None:
                pb.append(f"ERC {pcb['erc_errors']} error{'s' if pcb['erc_errors'] != 1 else ''}")
            tool_bits.append("PCB " + ", ".join(pb) + ".")
        if tool_bits:
            lines.append("- " + " ".join(tool_bits))
        if story is not None:
            if story["present"] is False:
                lines.append("- Stories have no audio yet: the story card holds text only. Choose **Generate story "
                             "audio** on the Code tab to add the spoken stories.")
            elif story["present"]:
                lines.append("- Story audio is on the story card.")
        lines.append(f"- {price['label']}.")
        for adj in decor_adjustment_lines(extra.get("cad_summary")):  # MAJ-31: say what the CAD changed to make it fit
            lines.append(f"- {adj}")
        lines += [f"- {n}" for n in notes]
        lines.append("")
        if buildable is False:
            lines.append(str(status.get("verdict_message") or "This version is not buildable yet: the CAD "
                                                                "collision check failed. Do not print or order it."))
        if reqs:
            met = sum(1 for r in reqs if r.get("status") == "implemented")
            nm = [r for r in reqs if r.get("status") == "not_met"]
            line = f"Brief trace: {met} of {len(reqs)} brief items implemented"
            if nm:
                line += "; not met: " + "; ".join(r["item"] for r in nm[:3])
            lines.append(line + ". See Design progress.")
        if counts["total"] and not counts["failed"] and counts["passed"] + len(counts["warned"]) == counts["total"] \
                and buildable is not False and not any(r.get("status") == "not_met" for r in reqs):
            lines.append("Design work is complete against the approved brief"
                         + (f" (v{brief_version})" if brief_version else "") + ", based on the agent's checks"
                         + (f"; {len(counts['warned'])} warning{'s' if len(counts['warned']) != 1 else ''} to review."
                            if counts["warned"] else "."))
        elif counts["failed"]:
            lines.append("Some digital checks failed; see Design progress before building this.")
        if physical:
            lines.append("Physical checks still needed:")
            lines += [f"- {p['title']}" for p in physical[:PHYSICAL_SHOWN]]
            if len(physical) > PHYSICAL_SHOWN:
                k = len(physical) - PHYSICAL_SHOWN
                lines.append(f"Plus {k} further physical check{'s' if k != 1 else ''} under Design progress below.")
        one = ", ".join(x for x in [
            name,
            f"{len(printed)} printed parts" if printed else "",
            f"{len(purchased)} purchased parts" if purchased else "",
            price["label"] if price["inr"] is not None else "",
        ] if x)
    else:
        err = status.get("error") or "the build did not finish"
        failed_stage = next((s for s in status.get("stages") or [] if s.get("state") in ("error", "failed")), None)
        where = f" during {failed_stage.get('label') or failed_stage.get('id')}" if failed_stage else ""
        lines.append(f"The build stopped{where}: {err}")
        lines.append("")
        lines.append("Nothing from this attempt should be ordered or printed. You can ask me to change the design, "
                     "or try the build again.")
        one = f"Build failed{where}"
    payload = {"state": state, "made": made, "checks": checks, "price": price, "physical_checks": physical,
               "notes": notes, "requirements": reqs, "buildable": buildable,
               "verdict_message": status.get("verdict_message") if buildable is False else None}
    return payload, "\n".join(lines).strip(), one
