"""BUILD_GUIDE.md: the maker's manual, generated from the same Step objects as build.json.

Structure (the benchmark's, layered): title and size, safety, how to read the pictures, evidence limits,
parts and tools (printed, purchased, tools, fastener allocation), numbered steps (Checklist ID, Use,
Tools, instructions, Expected result, Stop if, images with captions), wiring, stop points, final checks
(one bullet per tests[] entry, same titles), and the digital evidence with its limits.
"""

from __future__ import annotations

import re
from typing import Optional

from .data import ManualData
from .model import Assembly, bbox_size
from .steps import Namer, Step


def _size_line(A: Assembly) -> str:
    bb = A.overall_bbox
    if not bb:
        return ""
    w, d, h = bbox_size(bb)
    return f"Finished size from the CAD: about **{w:.0f} mm wide × {d:.0f} mm deep × {h:.0f} mm tall**."


def purchase_rows(data: ManualData, A: Assembly) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    if data.purchased():
        for p in data.purchased():
            rows.append((str(p.get("name")), int(p.get("quantity") or 1), str(p.get("notes") or "")))
        return rows
    n = Namer(A)
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
            continue  # already listed from the CAD, with the CAD's own count
        if nm not in seen and not any(nm.lower() in s.lower() or s.lower() in nm.lower() for s in seen):
            seen.add(nm)
            use = str(hw.get("use") or "")
            rows.append((nm, int(hw.get("qty") or 1), (use + "; " if use else "") + "not shown in the CAD"))
    return rows


def fastener_rows(A: Assembly) -> list[tuple[str, str, int]]:
    n = Namer(A)
    rows = []
    for c in A.inserts:
        rows.append(("Heat-set into the printed bosses", n.name(c), n.qty(c)))
    for c in A.cartridge_fasteners:
        rows.append(("Core cartridge to its bosses", n.name(c), n.qty(c)))
    for g in [*A.features, *A.internals]:
        for c in g.fasteners:
            rows.append((f"{(g.label or g.role).capitalize()}", n.name(c), n.qty(c)))
    for c in A.cover_fasteners:
        rows.append(("Closing part", n.name(c), n.qty(c)))
    return rows


def write_guide(data: ManualData, A: Assembly, steps: list[Step], visuals: dict[str, list[dict]], tests: list[dict],
                wiring: dict, summary_hint: Optional[dict] = None) -> str:
    name = data.product_name
    child = bool((data.spec.get("audience") or {}).get("child_directed"))
    tagline = data.spec.get("tagline") or ""
    L: list[str] = []
    L.append(f"# {name}: build guide")
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
             "is on your right when you face its front. A view from the rear or from below says so in its subtitle. "
             "A transparent shell only reveals what is inside; it is not an instruction to print clear plastic. "
             "Parts drawn apart (A panels) show the direction a part is fitted from; the distances are for clarity.")
    L.append("")
    L.append("**Evidence limits:** purchased parts are simplified envelopes from published dimensions; pins, latches, threads "
             "and cable stiffness are simplified or absent. Cable routes are display envelopes. The wiring diagram is drawn from "
             "the saved slot map and electrical data. No picture proves a fit, a connection, retention or safety, and no physical "
             "test is recorded as passed in this guide.")
    L.append("")
    # ---------------------------------------------------------------- parts and tools
    L.append("## Parts and tools")
    L.append("")
    n = Namer(A)
    printed = {str(p.get("file", "")).split("/")[-1].rsplit(".", 1)[0]: p for p in data.printed()}
    if A.prints:
        L.append("Printed parts (files in `print/`, already in print orientation):")
        L.append("")
        L.append("| Part | File | Material | Colour | Notes |")
        L.append("|---|---|---|---|---|")
        colour = (data.spec.get("colors") or {}).get("body_name") or ""
        for c in A.prints:
            p = dict(c.raw.get("print") or {})
            p.update(printed.get(c.name, {}))
            notes = []
            if p.get("grams"):
                notes.append(f"about {round(float(p['grams']))} g (slicer estimate)")
            if p.get("hours"):
                notes.append(f"about {float(p['hours']):.1f} h (slicer estimate)")
            if p.get("orientation_note"):
                notes.append(str(p["orientation_note"]))
            L.append(f"| `{c.name}` | `print/{c.name}.stl` | {p.get('material') or 'see parts list'} | {p.get('color') or colour} | {'; '.join(notes)} |")
        coupons = [p.name for p in data.print_files if p.name.upper().startswith("TEST_")]
        for f in coupons:
            L.append(f"| `{f.rsplit('.', 1)[0]}` | `print/{f}` | same as the shell | any | fit test, not a gadget part |")
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
    tools: list[str] = []
    seen_t: set[str] = set()
    for st in steps:
        for t in re.split(r"[,;.]\s*|\s+\(", st.tools):
            t = t.strip().strip(")").strip()
            low = t.lower()
            if not t or low.startswith(("none", "fingers only", "work on", "adult supervision", "a clear table", "a small parts tray", "the parts list", "or a clean")):
                continue
            key = re.sub(r"^(a|an|the)\s+", "", low)
            if key not in seen_t:
                seen_t.add(key)
                tools.append(t[0].upper() + t[1:])
    if tools:
        L.append("Tools (not part of the kit):")
        L.append("")
        L += [f"- {t}" for t in tools]
        L.append("")
    fr = fastener_rows(A)
    if fr:
        L.append("Fastener allocation:")
        L.append("")
        L.append("| Where | Fastener | Quantity |")
        L.append("|---|---|---:|")
        for where, f, q in fr:
            L.append(f"| {where} | {f} | {q} |")
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
        L.append(f"**Tools:** {s.tools}")
        L.append("")
        L.append(s.body.strip())
        L.append("")
        L.append(f"**Expected result:** {s.expected}")
        L.append("")
        if s.stop:
            L.append(f"**Stop if:** {s.stop}")
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
        L.append(f"- **{ws['title']}** (`{ws['id']}`): {ws['text']}")
    L.append("")
    # ---------------------------------------------------------------- stop points
    L.append("## Stop points and evidence to collect")
    L.append("")
    L.append("Stop at a failed fit, a missing part or anything that needs force. Note the checklist ID and collect:")
    L.append("")
    L.append("- **Print or fit:** the part name, material and printer, the measured hole or gap, and a photo of where it binds.")
    L.append("- **Module or cable:** the exact marking on the module, photos of both ends of the cable, and the port it should go to.")
    L.append("- **Firmware or self-test:** the saved serial monitor output and which module did not respond.")
    L.append("- **Closing:** a photo of the open seam showing what is in the way.")
    L.append("")
    L.append("Do not invent pin assignments, substitute modules, force a closure or leave out a fastener to continue.")
    L.append("")
    # ---------------------------------------------------------------- final checks
    L.append("## Final checks to record")
    L.append("")
    L.append("The following physical checks are actions for you, not passed test results:")
    L.append("")
    for t in tests:
        if t.get("kind") == "physical":
            L.append(f"- **{t['title']}:** {t['expected']}")
    dig = [t for t in tests if t.get("kind") == "digital"]
    if dig:
        L.append("")
        L.append("Digital checks recorded by this build (software and design files only, not the physical gadget):")
        L.append("")
        for t in dig:
            st = {"passed": "passed", "failed": "FAILED", "not_run": "not run"}.get(t.get("status"), t.get("status"))
            ev = f" (`{t['evidence']}`)" if t.get("evidence") else ""
            L.append(f"- **{t['title']}:** {t['expected']} Status: {st}{ev}.")
    L.append("")
    L.append("## Digital evidence and its limits")
    L.append("")
    L.append("`build.json` holds these steps, the wiring connections and the checks above in machine-readable form. "
             "`cad/` holds the geometry the pictures were rendered from; `electrical.json` holds the pin and harness data the "
             "wiring diagram was drawn from; `manual/manual-summary.json` counts the images and confirms every one is used here. "
             "None of this establishes a physical fit, an electrical or safety result, audible volume, surface temperature or stability.")
    L.append("")
    return "\n".join(L)


def guide_final_titles(md: str) -> list[str]:
    sec = md.split("## Final checks to record", 1)
    if len(sec) < 2:
        return []
    body = sec[1].split("\n## ", 1)[0]
    return re.findall(r"^- \*\*(.+?):\*\*", body, re.M)


def guide_images(md: str) -> list[str]:
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", md)
    links = re.findall(r"(?<!!)\[[^\]]*\]\((manual/[^)\s]+)\)", md)
    return imgs + links
