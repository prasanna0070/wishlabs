"""BUILD_GUIDE.md: the maker's manual, generated from the same Step objects as build.json.

Structure (the benchmark's, layered): title and size, a do-not-print banner when a CAD check failed (BLK-7), safety, how
to read the pictures, evidence limits, parts and tools (printed with slicer settings, purchased, tools, fastener
allocation with lengths), numbered steps (Checklist ID, Use, Tools, known issues, instructions, Expected result,
Stop if, images with captions), wiring, stop points, final checks (every tests[] record with its status, failed
first, MAJ-19) and the digital evidence with its limits.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Optional

from .data import ManualData
from .issues import Issues
from .model import Assembly, bbox_size
from .steps import Namer, Step, fastener_size, print_notes, print_settings
from .text import dedupe, parent_name, parent_note

FINAL_HEAD = "## Final checks to record"
EVIDENCE_HEAD = "## Digital evidence and its limits"
BANNER_START = "<!-- known-issues-banner -->"
BANNER_END = "<!-- /known-issues-banner -->"


def _size_line(A: Assembly) -> str:
    bb = A.overall_bbox
    if not bb:
        return ""
    w, d, h = bbox_size(bb)
    return f"Finished size from the CAD: about **{w:.0f} mm wide × {d:.0f} mm deep × {h:.0f} mm tall**."


def purchase_rows(data: ManualData, A: Assembly) -> list[tuple[str, int, str]]:
    n = Namer(A)
    rows: list[tuple[str, int, str]] = []
    if data.purchased():
        for p in data.purchased():
            rows.append((n.row_name(p), int(p.get("quantity") or 1), parent_note(str(p.get("notes") or ""))))
        return rows
    seen = set()
    for c in A.purchases:
        nm = n.name(c)
        if nm in seen:
            continue
        seen.add(nm)
        rows.append((nm, n.qty(c), ""))
    for h in data.harness():
        nm = h.get("cable") or ""
        if nm and nm not in seen:
            same = [x for x in data.harness() if x.get("cable") == nm]
            seen.add(nm)
            rows.append((nm, len(same), "module cable"))
    in_cad = {c.module_id for c in A.purchases if c.module_id}
    for hw in data.hardware():
        nm = str(hw.get("name") or "")
        if not nm or (hw.get("module") or hw.get("id")) in in_cad:
            continue
        if nm not in seen and not any(nm.lower() in s.lower() or s.lower() in nm.lower() for s in seen):
            seen.add(nm)
            use = str(hw.get("use") or "")
            rows.append((parent_name(nm, nuts_used=n.nuts), int(hw.get("qty") or 1), (use + "; " if use else "") + "not shown in the CAD"))
    return rows


def fastener_rows(A: Assembly) -> list[tuple[str, str, Optional[str], int]]:
    """(where, fastener, length, quantity) per kit, from the CAD fastener records (cad/digital-checks.json
    fastener_engagement) when present, else from the CAD components (MAJ-11)."""
    n = Namer(A)
    d = A.data
    recs = []
    for c in d.cad_checks():
        if c.get("id") == "fastener_engagement":
            recs = [s for s in (c.get("data") or {}).get("screws") or [] if isinstance(s, dict)]
    rows: list[tuple[str, str, Optional[str], int]] = []
    if recs:
        groups: "OrderedDict[tuple, int]" = OrderedDict()
        for s in recs:
            head = str(s.get("screw") or "").split(" ", 1)[0]
            key = (str(s.get("group")), head, float(s.get("length_mm") or 0))
            groups[key] = groups.get(key, 0) + 1
        feats = {g.ref: g for g in [*A.features, *A.internals] if g.ref}
        comp_by_group = {c.name.split("#", 1)[0].split("_", 1)[-1]: c for c in A.purchases}
        for (grp, head, L), q in groups.items():
            comp = comp_by_group.get(grp)
            sz = fastener_size(comp) if comp else None
            dia = sz[0] if sz else None
            length = f"{L:g} mm" if L else (f"{sz[1]} mm" if sz else None)
            if head in feats:
                g = feats[head]
                pr = next((p for p in g.prints if re.search(r"clip|clamp|strap|ring", f"{p.name} {p.label}", re.I)), None)
                where = f"{(g.label or g.role)[:1].upper() + (g.label or g.role)[1:]} ({head})" + (f", through the {n.human(pr)[:1].lower() + n.human(pr)[1:]}" if pr else "")
            elif "cartridge" in grp.lower() or head == "cartridge":
                where = "Core cartridge to its bosses"
            elif "cover" in grp.lower() or "plate" in grp.lower():
                where = "Closing part"
            else:
                where = head
            fname = f"{dia} socket head screws" if dia else (n.name(comp) if comp else grp)
            rows.append((where, fname, length, q))
        return rows
    for c in A.cartridge_fasteners:
        sz = fastener_size(c)
        rows.append(("Core cartridge to its bosses", n.name(c), f"{sz[1]} mm" if sz else None, n.qty(c)))
    for g in [*A.features, *A.internals]:
        for c in g.fasteners:
            sz = fastener_size(c)
            rows.append((f"{(g.label or g.role).capitalize()}", n.name(c), f"{sz[1]} mm" if sz else None, n.qty(c)))
    for c in A.cover_fasteners:
        sz = fastener_size(c)
        rows.append(("Closing part", n.name(c), f"{sz[1]} mm" if sz else None, n.qty(c)))
    return rows


def banner_md(issues: Optional[Issues]) -> str:
    if not issues or not issues.banner:
        return ""
    names = ", ".join(f"{r.get('title') or r.get('id')} (`{r.get('id')}`)" for r in issues.failed_cad)
    pairs = "; ".join(f"{a} and {b}" for a, b in (i.parts for i in issues.pairs))
    return (f"{BANNER_START}\n> **Do not print this design yet.** A digital CAD check failed: {names}."
            + (f" Parts involved: {pairs}." if pairs else "")
            + " The steps that print or fit these parts carry a known-issue line and a stop rule. Wait for a revised "
            "design (or ask) before printing or buying parts.\n" + BANNER_END)


def final_section(tests: list[dict]) -> list[str]:
    """Every tests[] record with its status, failed first (MAJ-19)."""
    L = [FINAL_HEAD, ""]
    rank = {"failed": 0, "warning": 1, "not_run": 2, "passed": 3}
    phys = [t for t in tests if t.get("kind") == "physical"]
    dig = [t for t in tests if t.get("kind") != "physical"]
    L.append("The following physical checks are actions for you, not passed test results:")
    L.append("")
    for t in phys:
        L.append(f"- **{t['title']}:** {t['expected']}")
    if dig:
        L.append("")
        L.append("Digital checks recorded by this build (software and design files only, not the physical gadget), failed ones first:")
        L.append("")
        for t in sorted(dig, key=lambda t: rank.get(str(t.get("status")), 2)):
            st = {"passed": "passed", "failed": "FAILED", "not_run": "not run", "warning": "warning"}.get(t.get("status"), t.get("status"))
            ev = f" (`{t['evidence']}`)" if t.get("evidence") else ""
            det = f" {t['detail']}." if t.get("detail") else ""
            exp = str(t.get("expected") or "").strip()
            exp = exp[0].upper() + exp[1:] if exp else ""
            L.append(f"- **{t['title']}:** {exp}{'' if exp.endswith('.') else '.'} Status: {st}{ev}.{det}")
    L.append("")
    return L


def evidence_section() -> list[str]:
    return [EVIDENCE_HEAD, "",
            "`build.json` holds these steps, the wiring connections and the checks above in machine-readable form. "
            "`cad/` holds the geometry the pictures were rendered from; `electrical.json` holds the pin and harness data the "
            "wiring diagram was drawn from; `manual/manual-summary.json` counts the images and confirms every one is used here; "
            "`manual/figures.json` and `manual/render_manual.py` re-render the pictures from `cad/`. "
            "None of this establishes a physical fit, an electrical or safety result, audible volume, surface temperature or stability.",
            ""]


def write_guide(data: ManualData, A: Assembly, steps: list[Step], visuals: dict[str, list[dict]], tests: list[dict],
                wiring: dict, issues: Optional[Issues] = None) -> str:
    name = data.product_name
    child = bool((data.spec.get("audience") or {}).get("child_directed"))
    tagline = data.spec.get("tagline") or ""
    n = Namer(A)
    L: list[str] = []
    L.append(f"# {name}: build guide")
    L.append("")
    b = banner_md(issues)
    if b:
        L.append(b)
        L.append("")
    L.append(" ".join(x for x in [f"{tagline}." if tagline and not tagline.endswith(".") else tagline, _size_line(A),
                                    f"The build has {len(steps)} steps; each one has a checklist ID that stays the same across design versions."] if x))
    L.append("")
    safety = [
        "**Before you start:** an adult assembles and tests this gadget.",
        "Keep screws, inserts, feet and cut cable-tie tails in a tray" + (" and away from children" if child else "") + ".",
        "Disconnect USB-C power before plugging, unplugging or moving any part.",
        "The heat-set insert step uses a hot soldering iron: work in a ventilated place, use its stand, and let parts cool." if A.inserts else "",
        "The USB-C supply stays outside the gadget. Route the cord where nobody trips on it" + (" and away from a bed" if child else "") + ".",
        "Use the gadget on a stable surface with its openings clear.",
    ]
    L.append(" ".join(s for s in safety if s))
    L.append("")
    L.append("## How to read the pictures")
    L.append("")
    L.append("The pictures are rendered from the saved CAD (`cad/components/*.brep`) by a script, so they show this exact design. "
             "They keep **+Z up** with the front at **-Y**. Left and right are the gadget's own: **+X is the gadget's left**, which "
             "is on your right when you face its front. A view from the rear says so in its subtitle: seen from the rear, the "
             "gadget's left (+X) is on your left. A transparent shell only reveals what is inside; it is not an instruction to "
             "print clear plastic. Parts drawn apart (A panels) show the direction a part is fitted from, and the blue arrows "
             "point along that direction; the distances are for clarity.")
    L.append("")
    L.append("**Evidence limits:** purchased parts are simplified envelopes from published dimensions; pins, latches, threads "
             "and cable stiffness are simplified or absent. Cable routes are display envelopes. The wiring diagram is drawn from "
             "the saved backplane and electrical data. No picture proves a fit, a connection, retention or safety, and no physical "
             "test is recorded as passed in this guide.")
    L.append("")
    # ---------------------------------------------------------------- parts and tools
    L.append("## Parts and tools")
    L.append("")
    printed = {}
    for p in data.printed():
        printed[str(p.get("name") or str(p.get("file", "")).split("/")[-1].rsplit(".", 1)[0])] = p
    if A.prints:
        L.append("Printed parts (files in `print/`, already in print orientation; weights, times and supports from the slicer):")
        L.append("")
        L.append("| Part | File | Material | Colour | Weight | Time | Supports | Orientation |")
        L.append("|---|---|---|---|---|---|---|---|")
        colour = (data.spec.get("colors") or {}).get("body_name") or ""
        for c in A.prints:
            p = dict(c.raw.get("print") or {})
            p.update(printed.get(c.name, {}))
            g = p.get("grams")
            wt = "" if g is None else ("<1 g" if float(g) < 1 else f"about {round(float(g))} g")
            hr = f"about {float(p['hours']):.1f} h" if p.get("hours") else ""
            sup = "" if p.get("supports") is None else ("yes" + (f" (about {float(p['support_g']):.1f} g)" if p.get("support_g") else "") if p["supports"] else "none")
            note = (print_notes({"orientation_note": p.get("orientation_note")}) or [""])[-1] if p.get("orientation_note") else ""
            L.append(f"| {n.human(c)} | `print/{c.name}.stl` | {p.get('material') or 'see parts list'} | "
                     f"{p.get('color_name') or colour or p.get('color') or ''} | {wt} | {hr} | {sup} | {note} |")
        for f in [p.name for p in data.print_files if p.name.upper().startswith("TEST_")]:
            st = f.rsplit(".", 1)[0]
            p = printed.get(st, {})
            g = p.get("grams")
            wt = "" if g is None else ("<1 g" if float(g) < 1 else f"about {round(float(g))} g")
            sup = "" if p.get("supports") is None else ("yes" if p["supports"] else "none")
            L.append(f"| Fit coupon `{st}` | `print/{f}` | same as the shell | any | {wt} | "
                     f"{('about %.1f h' % float(p['hours'])) if p.get('hours') else ''} | {sup} | fit test, not a gadget part |")
        L.append("")
        ps = print_settings(data)
        if ps:
            L.append(ps)
            L.append("")
    rows = purchase_rows(data, A)
    if rows:
        L.append("Purchased parts (quantities are items used, not packs to buy; prices and sources are in the parts list):")
        L.append("")
        L.append("| Item | Quantity | Notes |")
        L.append("|---|---:|---|")
        for nm, q, note in rows:
            L.append(f"| {nm} | {q} | {note} |")
        L.append("")
    tools = dedupe([t for st in steps for t in st.tools])
    if tools:
        L.append("Tools (not part of the kit; each step lists the ones it needs):")
        L.append("")
        L += [f"- {t}" for t in tools]
        L.append("")
    fr = fastener_rows(A)
    if fr:
        L.append("Fastener allocation (from the CAD fastener records):")
        L.append("")
        L.append("| Where | Fastener | Length | Quantity |")
        L.append("|---|---|---:|---:|")
        for where, f, length, q in fr:
            L.append(f"| {where} | {f} | {length or 'not recorded'} | {q} |")
        L.append(f"| **Total screws** | | | **{sum(r[3] for r in fr)}** |")
        L.append("")
        ins_q = sum(n.qty(c) for c in A.inserts)
        if ins_q:
            sz = fastener_size(A.inserts[0])
            L.append(f"Heat-set inserts: {ins_q} × " + (f"{sz[0]} × {sz[1]} mm" if sz else n.name(A.inserts[0]))
                     + "; the insert step lists where each one goes.")
            L.append("")
        L.append("Threads are not modeled and no torque value has been established: tighten gently, a little at a time.")
        L.append("")
    # ---------------------------------------------------------------- steps
    L.append("## Assembly steps")
    L.append("")
    for i, s in enumerate(steps, 1):
        L.append(f"### {i}. {s.title}")
        L.append("")
        L.append(f"Checklist ID: `{s.id}`.")
        L.append("")
        L.append(f"**Use:** {s.use}")
        L.append("")
        L.append(f"**Tools:** {s.tools_text}")
        L.append("")
        if s.known:
            for k in s.known:
                L.append(f"> **Known design issue:** {k}.")
            L.append("")
        L.append(s.body.strip())
        L.append("")
        L.append(f"**Expected result:** {s.expected}")
        L.append("")
        if s.stop_text:
            L.append(f"**Stop if:** {s.stop_text}")
            L.append("")
        for v in visuals.get(s.id, []):
            L.append(f"![{v['alt']}]({v['path']})")
            L.append("")
            L.append(f"*{v['caption']}*")
            L.append("")
    # ---------------------------------------------------------------- wiring
    L.append("## Wiring")
    L.append("")
    L.append("The full diagram is `manual/wiring.png` (vector copy: [manual/wiring.svg](manual/wiring.svg)). "
             "In the Build tab the same connections are listed as steps:")
    L.append("")
    for ws in wiring.get("steps", []):
        L.append(f"- **{ws['title']}** (`{ws['id']}`): {parent_name(ws['text'])}")
    L.append("")
    # ---------------------------------------------------------------- stop points
    L.append("## Stop points and evidence to collect")
    L.append("")
    L.append("Stop at a failed fit, a missing part or anything that needs force. Note the checklist ID and collect:")
    L.append("")
    L.append("- **Print or fit:** the part name, material and printer, the measured hole or gap, and a photo of where it binds.")
    L.append("- **Module or cable:** the exact marking on the module, photos of both ends of the cable, and the port it should go to.")
    L.append("- **Firmware or self-test:** the saved serial monitor output, including the self-test summary line, and which module did not respond.")
    L.append("- **Closing:** a photo of the open seam showing what is in the way.")
    L.append("")
    L.append("Do not invent pin assignments, substitute modules, force a closure or leave out a fastener to continue.")
    L.append("")
    L += final_section(tests)
    L += evidence_section()
    return "\n".join(L)


def guide_final_titles(md: str) -> list[str]:
    sec = md.split(FINAL_HEAD, 1)
    if len(sec) < 2:
        return []
    body = sec[1].split("\n## ", 1)[0]
    return re.findall(r"^- \*\*(.+?):\*\*", body, re.M)


def guide_images(md: str) -> list[str]:
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", md)
    links = re.findall(r"(?<!!)\[[^\]]*\]\((manual/[^)\s]+)\)", md)
    return imgs + links
