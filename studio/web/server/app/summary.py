"""Build summary written from the version's files, never by the model, so every number traces to a file.

Reads (all optional, shapes handled defensively): build.json, manifest.json, checks/digital-checks.json,
order/quote.json, status.json.
"""
from __future__ import annotations

from typing import Any

PHYSICAL_SHOWN = 3


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


def digital_checks(dc: Any) -> tuple[int, int, list[str]]:
    """(passed, total, failed titles) from a digital-checks.json of unknown exact shape."""
    items: list = []
    if isinstance(dc, dict):
        for k in ("checks", "results", "items"):
            if isinstance(dc.get(k), list):
                items = dc[k]
                break
        else:
            items = [{"name": k, **v} if isinstance(v, dict) else {"name": k, "passed": v}
                     for k, v in dc.items() if isinstance(v, (dict, bool)) and k not in ("limits", "summary")]
    elif isinstance(dc, list):
        items = dc
    passed, failed = 0, []
    total = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        st = next((it[k] for k in ("status", "passed", "ok", "result") if it.get(k) is not None), None)
        if isinstance(st, str):
            low = st.lower()
            if low in ("skipped", "not_run", "n/a", "info", "pending"):
                continue
            ok = low in ("passed", "pass", "ok", "true", "done", "warning", "warn")
        elif st is None:
            continue
        else:
            ok = bool(st)
        total += 1
        if ok:
            passed += 1
        else:
            failed.append(str(it.get("title") or it.get("name") or it.get("id") or "check"))
    return passed, total, failed


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
    if inr is None:
        reason = (order or {}).get("reason") or (quote.get("reason") if isinstance(quote, dict) else None)
        return {"inr": None, "status": status or "NOT_QUOTED",
                "label": "Price not quoted yet" + (f" ({reason})" if reason else "")}
    binding = (status or "").upper() == "BINDING"
    return {"inr": inr, "status": status or "ESTIMATE",
            "label": f"{_inr(inr)} delivered" + ("" if binding else " (estimate, not a binding price)")}


def build_summary(name: str, status: dict | None, build: dict | None, manifest: dict | None, dc: Any,
                  quote: Any, brief_version: int | None) -> tuple[dict, str, str]:
    """Returns (BuildSummary payload, Markdown chat text, one-line version summary)."""
    status = status or {}
    build = build or {}
    state = status.get("state") or "error"
    made: list[str] = []
    notes: list[str] = []
    printed = [p for p in build.get("printed") or [] if isinstance(p, dict)]
    purchased = [p for p in build.get("purchased") or [] if isinstance(p, dict)]
    steps = [s for s in build.get("steps") or [] if isinstance(s, dict)]
    files = [f.get("path", "") for f in (manifest or {}).get("files") or [] if isinstance(f, dict)]
    images = [f for f in files if f.startswith("manual/") and f.lower().endswith((".png", ".jpg", ".webp"))]
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
        made.append(f"An illustrated build manual: {len(steps)} steps"
                    + (f", {len(images)} images" if images else ""))
    if purchased:
        made.append(f"A parts list of {len(purchased)} purchased items with sources")

    passed, total, failed = digital_checks(dc)
    fw_tests = None
    if fw.get("tests_total"):
        fw_tests = f"{fw.get('tests_passed', 0)}/{fw.get('tests_total')}"
    pcb = build.get("pcb") or {}
    price = price_of(build, quote)
    checks = {"digital_passed": passed, "digital_total": total, "digital_failed": failed,
              "firmware_compiled": bool(fw.get("compiled")), "firmware_tests": fw_tests,
              "pcb_drc_errors": pcb.get("drc_errors"), "pcb_erc_errors": pcb.get("erc_errors")}

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
        check_bits = []
        if total:
            check_bits.append(f"{passed} of {total} digital checks passed"
                              + (f"; not passed: {'; '.join(failed[:3])}" + (f" and {len(failed) - 3} more" if
                                                                             len(failed) > 3 else "") if failed else ""))
        if fw_tests:
            check_bits.append(f"firmware unit tests {fw_tests}")
        if pcb.get("drc_errors") is not None:
            check_bits.append(f"PCB DRC errors: {pcb.get('drc_errors')}")
        if check_bits:
            lines.append("- " + "; ".join(check_bits) + ".")
        lines.append(f"- {price['label']}.")
        lines += [f"- {n}" for n in notes]
        lines.append("")
        if total and not failed and passed == total:
            lines.append("Design work is complete against the approved brief"
                         + (f" (v{brief_version})" if brief_version else "") + ", based on the agent's checks.")
        elif failed:
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
               "notes": notes}
    return payload, "\n".join(lines).strip(), one
