"""PROJECT.md: the design record. Overview, frame conventions, rebuild commands, status and honest limits."""
from __future__ import annotations

from pathlib import Path

from .util import Bundle, exists, inr, link, size_from_components, table

STAGE_ORDER = ("normalize", "electrical", "cad", "pcb", "firmware", "order", "manual", "checks", "finalize", "docs", "package")
STAGE_NAMES = {"normalize": "Design check", "electrical": "Wiring and pin map", "cad": "Shell and CAD", "pcb": "Backplane PCB",
               "firmware": "Firmware", "order": "Slicing and price", "manual": "Build manual", "checks": "Digital checks",
               "finalize": "Final guide checks", "docs": "Project documents", "package": "Package and export"}


def _count(out: Path, pattern: str) -> int:
    return len(list(out.glob(pattern)))


def _electrical_note(b: Bundle) -> str:
    s = b.summary("electrical")
    if b.state("electrical") != "done" or not s:
        return f"{b.state('electrical')}: {b.message('electrical')}"
    slots = s.get("slots_used") or []
    return f"{s.get('instances', '?')} module instances on {len(slots)} backplane slots, {s.get('nets', '?')} nets"


def power_text(b: Bundle, pw: dict) -> str:
    """MAJ-5: the supply by rating (5 V 3 A), whether it ships in the box, and the BIS CRS requirement; the current
    figures with the limit they are compared with."""
    rows = b.build.get("purchased") or []
    psu = next((r for r in rows if str(r.get("role") or "") == "adapter" and r.get("in_box")), None)
    pb = next((c for c in (b.digital or {}).get("checks") or [] if c.get("id") == "power-budget"), None)
    rv = next((c for c in (b.digital or {}).get("checks") or [] if c.get("id") == "rail-voltage"), None)
    d = (pb or {}).get("data") or {}
    supply = (f"a 5 V 3 A USB-C adapter ships in the box ({psu['name']})" if psu else
              "the buyer supplies a 5 V 3 A USB-C adapter (none in the box)")
    cap = pw.get("total_ma_max_capped", pw.get("total_ma_max"))
    unc = pw.get("total_ma_max") if "total_ma_max_capped" in pw else None
    lim = d.get("limit_ma", pw.get("limit_ma"))
    basis = d.get("basis") or "the PTC fuse budget"
    t = (f"Power: 5 V USB-C input; {supply}. A mains adapter sold in India must carry BIS CRS registration (an R-number on its "
         "label); check the label, the listing does not show one. "
         f"Worst-case current {cap} mA with the firmware caps" + (f" ({unc} mA without them)" if unc is not None else "")
         + f" against {lim} mA, the {basis} ({'within' if (pb or {}).get('status') == 'passed' or (pb is None and pw.get('ok')) else 'NOT within'} budget).")
    if rv and (rv.get("data") or {}).get("rail_min_v") is not None:
        r = rv["data"]
        t += (f" The +5V rail is calculated at {r['rail_min_v']} V at that peak against {r.get('required_min_v')} V needed by "
              f"{r.get('required_by')} ({rv.get('status')}).")
    return t + " These figures are calculated from module data and assumptions, not measured. A computer USB port is not a substitute."


def render(b: Bundle) -> str:
    out, sp, pj = b.out, b.spec, b.project
    L: list[str] = []
    L.append(f"# {sp.get('name', 'Gadget')}")
    L.append("")
    if sp.get("tagline"):
        L.append(f"{sp['tagline']}.".replace("..", "."))
        L.append("")
    aud = sp.get("audience") or {}
    col = sp.get("colors") or {}
    bv = pj.get("brief_version")
    brief_line = (f"Approved brief version {bv}." if bv is not None else
                  "Brief recorded with this build." if pj.get("brief") else "No approved brief was recorded with this build.")
    L.append(f"Version {pj.get('version') if pj.get('version') is not None else 'n/a'}, engine {pj.get('engine_version', '?')}, "
             f"built {pj.get('created', '?')}. {brief_line}"
             + (f" Selected concept `{pj['concept_id']}`." if pj.get("concept_id") else ""))
    L.append("")
    who = (f"Made for a child of about {aud.get('age_years')} years; an adult builds, sets up and supervises it." if aud.get("child_directed")
           else "General audience.")
    L.append(f"{who} Region: India (local sourcing and INR prices). Spec hash `{pj.get('spec_hash', '?')[:16]}`.")
    L.append("")
    L.append(f"Colours: body {col.get('body_name') or ''} `{col.get('body', '?')}`, accent `{col.get('accent', '?')}`, detail `{col.get('detail', '?')}`. "
             "One print colour per part; colour names are for the maker, not a paint specification.")
    beh = sp.get("behaviour") or {}
    if beh.get("summary"):
        L += ["", f"Behaviour (`{beh.get('template')}` template): {beh['summary']}"]
    rep = pj.get("auto_repair")
    if isinstance(rep, dict):
        L += ["", f"Automatic repair in this build: {rep.get('why')}. `spec.json` is the repaired design; "
                  f"{'the collision check passes after it' if not rep.get('after') else 'collisions remain after it'}."]
    gate = (b.digital or {}) and next((c for c in (b.digital or {}).get("checks") or [] if c.get("id") in ("collisions", "cad-collisions")), None)
    if gate and gate.get("status") == "failed":
        L += ["", f"**Not buildable yet.** The CAD shows parts colliding ({gate.get('detail')}). Do not print or order this version; "
                  "the quote is held until the design is revised."]

    # ---- deliverables
    L += ["", "## What this version contains", ""]
    stl = _count(out, "print/PRINT_*.stl")
    imgs = _count(out, "manual/*.png")
    st = b.build.get("steps") or []
    fw = b.build.get("firmware") or {}
    pcb = b.build.get("pcb") or {}
    rows = [
        ["Normalized design spec", link("spec.json", out), "every part comes from the module library"],
        ["Electrical pin map and harness", link("electrical.json", out), _electrical_note(b)],
        ["CAD assembly", link("cad/assembly.step", out) + ", " + link("cad/assembly.glb", out),
         (b.summary("cad").get("say") if b.state("cad") == "done" else None) or f"{b.state('cad')}: {b.message('cad')}"],
        ["Editable CAD source", link("cad/gadget_cad.py", out), "reads `cad/params.json`; runs without the engine" if exists(out, "cad/gadget_cad.py") else ""],
        ["Print files", f"`print/` ({stl} part file(s))", (b.message("order") or "") if stl else "none"],
        ["Backplane PCB", link("pcb/backplane.kicad_pcb", out) + ", " + link("pcb/backplane-gerbers.zip", out),
         (f"DRC errors {pcb.get('drc_errors', 'n/a')}, ERC errors {pcb.get('erc_errors', 'n/a')}, "
          f"{'fully routed' if pcb.get('routed') else str(pcb.get('unrouted', '?')) + ' connection(s) unrouted'}")
         if pcb and b.state("pcb") == "done" else f"{b.state('pcb')}: {b.message('pcb')}"],
        ["Firmware", link("firmware/platformio.ini", out) + ", " + link("firmware/firmware.bin", out),
         (("compiled" if fw.get("compiled") else "NOT compiled") + (f", unit tests {fw.get('tests_passed')}/{fw.get('tests_total')} passed"
                                                                     if fw.get("tests_total") is not None else ", no unit-test results"))
         if fw and b.state("firmware") == "done" else f"{b.state('firmware')}: {b.message('firmware')}"],
        ["Build manual", link("BUILD_GUIDE.md", out) + f", `manual/` ({imgs} image(s))", f"{len(st)} steps" if st else b.message("manual")],
        ["Parts, sources and evidence", link("build.json", out) + ", " + link("PARTS_EVIDENCE.md", out), f"{len(b.build.get('purchased') or [])} purchased rows"],
        ["Delivered price", link("order/quote.json", out), (b.quote or {}).get("status_label", "not quoted")],
        ["Software setup", link("SOFTWARE_SETUP.md", out), ""],
        ["Checks", link("checks/digital-checks.json", out) + ", " + link("checks/delivery-checks.json", out), ""],
        ["Manifest and export", "`manifest.json`, `export.zip`", "every delivered file with bytes and sha256"],
    ]
    if sp.get("kind") == "printed":  # catalog package A: a printed-only object has no electronics deliverables
        rows = [r for r in rows if r[0] not in ("Electrical pin map and harness", "Backplane PCB", "Firmware", "Software setup")]
        L += ["This is a printed-only design: it has no electronics, so there is no wiring, backplane PCB, firmware or software "
              "setup in this version.", ""]
    L.append(table(["Artefact", "Files", "Notes"], rows))

    # ---- frame
    size = size_from_components(b.components) or list((b.derived.get("size_mm") or [])) or None
    tipped = [p for p in b.build.get("printed") or [] if isinstance(p, dict) and p.get("tipped")]
    L += ["", "## Frame conventions", "",
          "- Units are millimetres. +Z is up and the gadget stands on the table plane Z=0.",
          "- The front faces -Y. +X is the viewer's right when looking at the front, which is the gadget's own left. "
          "Rear views mirror left and right.",
          "- The origin is the centre of the footprint at the table plane.",
          f"- Overall size: {size[0]:.0f} W x {size[1]:.0f} D x {size[2]:.0f} H mm" + (" (CAD assembly bounding box, cable envelopes excluded)." if b.components else " (normalizer estimate; CAD not available).")
          if size else "- Overall size: not available (no CAD output).",
          ("- Print files in `print/` are already in print orientation, resting on Z=0; the CAD assembly keeps world coordinates."
           if not tipped else
           "- Print files in `print/` rest on Z=0 as the CAD exported them; the CAD assembly keeps world coordinates. "
           + ", ".join(f"`{p.get('name')}`" for p in tipped) + " does not fit the printer that way and prints on its side: "
           + "; ".join(f"{p.get('name')}: {(p.get('print_orientation') or {}).get('label')}, rotated "
                       + ", then ".join(f"{int(v):+d} deg about {ax}" for ax, v in (
                           ("X", (p.get("print_orientation") or {}).get("rotate_x_deg")),
                           ("Y", (p.get("print_orientation") or {}).get("rotate_y_deg")),
                           ("Z", (p.get("print_orientation") or {}).get("rotate_z_deg"))) if v)
                       for p in tipped)
           + " (see `print/slice-stats.json`)."),
          "- Component names carry their role: `PRINT_` printed, `PURCHASE_` bought, `REFERENCE_` display-only (cables, keep-outs), "
          "`TEST_` fit coupon (print only). The names join `cad/components.json`, `build.json` parts and the manual steps."]

    # ---- hardware
    d = b.derived
    insts = [i for i in d.get("instances") or [] if isinstance(i, dict)]
    if insts:
        harness = {h.get("ref"): h for h in d.get("harness") or [] if isinstance(h, dict)}
        L += ["", "## Hardware", ""]
        L.append(table(["Ref", "Module", "Role", "Feature", "Backplane slot", "Cable"],
                       [[i.get("ref"), i.get("name") or i.get("module"), i.get("role"), i.get("feature") or "", i.get("slot") or "",
                         (harness.get(i.get("ref")) or {}).get("cable", "")] for i in insts]))
        gpio = (b.electrical or {}).get("gpio") or {}
        if gpio:
            L += ["", "GPIO numbers are fixed by the standard backplane (from `electrical.json`):", ""]
            L.append(table(["Signal", "ESP32-S3 GPIO"], [[k, v] for k, v in gpio.items()]))
        pw = (b.electrical or {}).get("power") or {}
        if pw:
            L += ["", power_text(b, pw)]

    # ---- rebuild
    L += ["", "## Rebuild commands", "", "Run from the folder that holds this file. The full pipeline needs the Wish Labs engine "
          "(Python 3.12, CadQuery 2.8, KiCad 9, PlatformIO 6.2, PrusaSlicer 2.7):", "", "```sh",
          "python verify_delivery.py                               # re-check every hash and link (standard library only)",
          "python -m gstudio validate spec.json",
          "python -m gstudio build spec.json out/                  # everything",
          "python -m gstudio build spec.json out/ --only cad,order # one stage plus its inputs"]
    cad_cmd = b.summary("cad").get("rebuild") or b.summary("cad").get("command")
    if exists(out, "cad/gadget_cad.py"):
        L += ["", "# CAD without the engine (reads cad/params.json, writes STEP, STL, BREP and JSON)",
              cad_cmd if isinstance(cad_cmd, str) else "python cad/gadget_cad.py"]
    if exists(out, "print/slice.ini"):
        first = sorted(out.glob("print/PRINT_*.stl"))
        L += ["", "# re-slice a print file with the exact profile used for the price",
              f"prusa-slicer --export-gcode --load print/slice.ini --output {first[0].stem if first else 'PRINT_Part'}.gcode "
              f"print/{first[0].name if first else 'PRINT_Part.stl'}"]
    if exists(out, "pcb/backplane.kicad_pcb"):
        L += ["", "# backplane PCB checks and fabrication files (KiCad 9)",
              "kicad-cli pcb drc --format json --output pcb/drc.json pcb/backplane.kicad_pcb",
              "kicad-cli sch erc --format json --output pcb/erc.json pcb/backplane.kicad_sch",
              "kicad-cli pcb export gerbers --output pcb/gerbers/ pcb/backplane.kicad_pcb",
              "kicad-cli pcb export drill --output pcb/gerbers/ pcb/backplane.kicad_pcb"]
    if exists(out, "firmware/platformio.ini"):
        L += ["", "# firmware (PlatformIO): compile, run the native unit tests, flash over USB",
              "cd firmware && pio run && pio test -e native && pio run -t upload"]
    L += ["```"]

    # ---- price
    q = b.quote
    L += ["", "## Price and lead time", ""]
    if q and q.get("status") == "HELD":
        L.append(f"**{q.get('status_label')}.** No price is offered for this version: "
                 + "; ".join(q.get("reasons") or []) + ".")
        pt = q.get("parts_total_inr") or {}
        if pt.get("one_off") is not None:
            L += ["", f"Parts alone would be about {inr(pt['one_off'])} for one unit ({pt.get('basis', '')})."]
    elif q:
        dp, lt = q["delivered_price_inr"], q["lead_time_days"]
        L.append(f"**{q['status_label']}.** Delivered price including GST: **{inr(dp['pilot_one_off'])}** for a one-off pilot unit today, "
                 f"**{inr(dp['steady_state'])}** at a steady 100+ units a month. Lead time estimate {lt['low']}-{lt['high']} days for the one-off "
                 f"({lt['steady_state']['low']}-{lt['steady_state']['high']} days at steady state). Status `{q['status']}`, never binding:")
        L.append("")
        L += [f"- {r}" for r in q.get("reasons") or []]
        pt = q.get("parts_total_inr") or {}
        inb = q.get("inbound_shipping") or {}
        if pt.get("one_off") is not None:
            L += ["", f"Parts total for one unit: {inr(pt['one_off'])} (the same figure as `PARTS_EVIDENCE.md` and the Parts tab)"
                      + (f"; inbound shipping from {inb.get('vendor_count')} vendor(s) adds about {inr(inb.get('one_off_inr'))} to the one-off "
                         "(assumed fees)" if inb.get("vendor_count") else "") + "."]
        L += ["", "All four scenarios, p5/p50/p95 costs and every assumption are in `order/quote.json`; part prices are in `PARTS_EVIDENCE.md`."]
    else:
        L.append(f"Not quoted in this version: {b.message('order') or 'the order stage did not run'}.")

    # ---- stage status
    L += ["", "## Build status", ""]
    rows = []
    for sid in STAGE_ORDER:
        if sid in ("docs", "package"):
            rows.append([STAGE_NAMES[sid], "this file" if sid == "docs" else "after this file", "",
                         "see manifest.json" if sid == "package" else ""])
            continue
        r = b.records.get(sid) or {}
        sec = r.get("seconds")
        rows.append([STAGE_NAMES[sid], r.get("state", "not run"),
                     f"{sec:.0f} s" if isinstance(sec, (int, float)) else "", (r.get("message") or "")[:160]])
    L.append(table(["Stage", "State", "Time", "Note"], rows))
    L.append("")
    bad = [STAGE_NAMES[sid] for sid in STAGE_ORDER if (b.records.get(sid) or {}).get("state") in ("failed", "skipped")
           and (b.records.get(sid) or {}).get("skip_kind") != "not_selected"]
    if bad:
        L.append(f"Not complete in this version: {', '.join(bad)}. A failed stage may have left partial files; they are unvalidated "
                 "and must not be used to build or order. A skipped stage produced nothing. No output was substituted for them.")
    else:
        L.append("Every stage completed.")

    # ---- checks and limits
    dg = b.digital or {}
    s = dg.get("summary") or {}
    L += ["", "## Checks and limits", ""]
    if s:
        L.append(f"Digital checks: {s.get('passed', 0)} passed, {s.get('failed', 0)} failed, {s.get('warning', 0)} warnings, "
                 f"{s.get('not_run', 0)} not run (`checks/digital-checks.json`). Each check names the stage and file it read.")
        bad = [c for c in dg.get("checks") or [] if c.get("status") == "failed"]
        if bad:
            L += ["", "Failed digital checks:", ""] + [f"- {c['title']}: {c.get('detail', '')}" for c in bad[:20]]
    phys = b.physical_tests()
    L += ["", "Physical checks still needed (actions for the maker or the factory, not passed results):", ""]
    if phys:
        L += [f"- {t.get('title')}: {t.get('expected', '')}" for t in phys]
    else:
        L.append("- The build manual did not list its physical checks in this version; treat every function as untested on real hardware.")
    L += ["", "Limits:", ""] + [f"- {x}" for x in dg.get("limits") or []]
    if aud.get("child_directed"):
        L += ["- This is a design for a child's use. It has not been assessed against India's toy quality-control order (BIS), "
              "small-parts, surface-temperature or child-data rules. An adult assembles it, keeps small parts away from the child "
              "and supervises use."]
    L.append("")
    return "\n".join(L)
