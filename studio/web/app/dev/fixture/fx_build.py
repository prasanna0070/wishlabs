"""build.json content written by hand for the fixture: assembly steps, wiring view, physical tests, brief."""
from __future__ import annotations

CAM = {
    "front": {"position": [330, -430, 300], "target": [0, 0, 88]},
    "rear_open": {"position": [150, 270, 210], "target": [0, 14, 22]},
    "rear_open_left": {"position": [-150, 260, 200], "target": [0, 14, 24]},
    "front_low_inside": {"position": [-160, 220, 175], "target": [0, -44, 34]},
    "button_inside": {"position": [150, 160, 180], "target": [0, -38, 68]},
    "top_inside": {"position": [-200, 210, 270], "target": [-12, -50, 86]},
    "cables": {"position": [-230, 260, 250], "target": [0, -14, 46]},
    "rear_closed": {"position": [330, 430, 220], "target": [0, 20, 76]},
    "exploded": {"position": [560, 600, 470], "target": [0, 95, 115]},
}

CABLES = ["REFERENCE_CableButton", "REFERENCE_CableMic", "REFERENCE_CableLed", "REFERENCE_CableSpeaker"]


def _ins(use: list[str], tools: list[str], body: str) -> str:
    return "**Use:** " + ", ".join(use) + "\n**Tools:** " + ", ".join(tools) + "\n\n" + body


def steps() -> list[dict]:
    S = [
        ("print-parts", "Print the shell, cover and speaker ring",
         _ins(["PRINT_Shell x1", "PRINT_RearCover x1", "PRINT_SpeakerRing x1", "TEST_ButtonFit x1"],
              ["FDM printer (250 x 210 x 210 mm bed)", "PETG, pale teal", "flush cutters"],
              "Print `TEST_ButtonFit` first and press the arcade button into it. Then print the shell front face down "
              "with supports from the build plate only, the rear cover outer face down, and the speaker ring flat. "
              "Remove the supports under the rounded front edge."),
         "Three clean parts; the button snaps into the fit coupon without force.",
         ["PRINT_Shell", "PRINT_RearCover", "PRINT_SpeakerRing"], ["PRINT_Shell", "PRINT_RearCover", "PRINT_SpeakerRing"], "front"),
        ("set-inserts", "Set the heat-set inserts",
         _ins(["M2.5 heat-set insert x8"], ["soldering iron with an insert tip (heat only)", "tweezers"],
              "Press one insert into each of the four backplane bosses on the floor and each of the four cover bosses "
              "at the rear rim. Keep each insert square and flush with the top of its boss."),
         "Eight inserts sit flush and square; no plastic bulges around them.",
         ["PRINT_Shell", "PURCHASE_Inserts"], ["PURCHASE_Inserts"], "rear_open"),
        ("mount-backplane", "Screw down the backplane",
         _ins(["backplane x1", "M2.5 x 6 socket head screw x4"], ["2 mm hex key"],
              "Lower the backplane onto the four bosses with its USB-C socket toward the rear opening. The two printed "
              "posts pass through the holes beside the microSD socket. Fit the four screws finger tight, then a quarter turn."),
         "The board sits flat on all four bosses and the USB-C socket lines up with the rear opening.",
         ["PURCHASE_Backplane", "PURCHASE_UsbC", "PURCHASE_BoardScrews"], ["PURCHASE_Backplane", "PURCHASE_UsbC", "PURCHASE_BoardScrews"],
         "rear_open"),
        ("plug-core-modules", "Plug in the core board, amplifier and microSD module",
         _ins(["ESP32-S3-DevKitC-1 x1", "MAX98357A amplifier x1", "microSD module x1"], ["none"],
              "Press the core board into its two long sockets with its USB ports toward the robot's left side. Press the "
              "amplifier and the microSD module into their sockets. The microSD card slot faces the robot's right side."),
         "All three boards sit level on their sockets and no pin is outside a socket.",
         ["PURCHASE_CoreBoard", "PURCHASE_Amp", "PURCHASE_SdCard"], ["PURCHASE_CoreBoard", "PURCHASE_Amp", "PURCHASE_SdCard"],
         "rear_open_left"),
        ("fit-speaker", "Fit the speaker behind the grille",
         _ins(["40 mm speaker x1", "PRINT_SpeakerRing x1", "2-pin speaker lead x1"], ["soldering iron", "solder"],
              "Solder the two-wire lead to the speaker's eyelets (this is the soldered joint in this build). Seat the "
              "speaker against the inside of the grille and press the speaker ring over its rim."),
         "The speaker sits centred behind the grille and does not rattle when the shell is tapped.",
         ["PURCHASE_Speaker", "PRINT_SpeakerRing"], ["PURCHASE_Speaker", "PRINT_SpeakerRing"], "front_low_inside"),
        ("fit-button", "Snap in the story button",
         _ins(["30 mm arcade button x1"], ["none"],
              "Push the button through the round hole from the front until its snap ring clicks behind the wall. Turn "
              "it so the microswitch terminals face down."),
         "The button sits flush and clicks when pressed.",
         ["PURCHASE_Button"], ["PURCHASE_Button"], "button_inside"),
        ("fit-light-mic", "Fit the night light and the microphone",
         _ins(["WS2812B LED module x1", "INMP441 microphone module x1"], ["tweezers"],
              "Seat the LED module behind the small square window under the smile, LED facing out. Seat the microphone "
              "module behind the pin hole left of the button, its sound port over the hole."),
         "The LED is centred in its window and the microphone port lines up with the hole.",
         ["PURCHASE_Led", "PURCHASE_Mic"], ["PURCHASE_Led", "PURCHASE_Mic"], "top_inside"),
        ("connect-cables", "Plug in the four cables",
         _ins(["3-pin cable x2", "6-pin cable x1", "2-pin speaker lead x1"], ["none"],
              "Plug each keyed cable into its port on the backplane: button to J_IO1, night light to J_LED, microphone to "
              "J_MIC and speaker to J_SPK. Route the cables along the walls, away from the core board's antenna end."),
         "Every plug is fully seated; no cable crosses the antenna end of the core board.",
         CABLES + ["PURCHASE_Backplane"], CABLES, "cables"),
        ("first-power-on", "Insert the story card and power on",
         _ins(["microSD card with the story pack x1", "USB-C cable x1"], ["5 V USB-C supply (not in the box)"],
              "Copy the `sdcard/` story pack to a microSD card and insert it into the microSD module. Connect USB-C at "
              "the rear opening with the cover still off."),
         "The night light glows briefly and a short chime plays. Physical check still needed on a real unit.",
         ["PURCHASE_SdCard", "PURCHASE_CoreBoard", "PURCHASE_Led"], ["PURCHASE_SdCard", "PURCHASE_CoreBoard", "PURCHASE_Led"],
         "rear_open_left"),
        ("close-cover", "Close the rear cover and fit the feet",
         _ins(["PRINT_RearCover x1", "M2.5 x 8 socket head screw x4", "adhesive bumper foot x4"], ["2 mm hex key"],
              "Fit the rear cover with its opening over the USB-C socket and fit the four screws. Stick a foot into each "
              "of the four shallow recesses under the base."),
         "The cover sits flush all round, the USB-C plug fits through the opening and the gadget stands steady.",
         ["PRINT_RearCover", "PURCHASE_CoverScrews", "PURCHASE_Feet"], ["PRINT_RearCover", "PURCHASE_CoverScrews", "PURCHASE_Feet"],
         "rear_closed"),
    ]
    out = []
    for k, (sid, title, ins, exp, parts, hl, cam) in enumerate(S, 1):
        out.append({"id": sid, "title": title, "instruction": ins, "expected": exp, "parts": parts, "highlight": hl,
                    "camera": CAM[cam], "visuals": [{"path": f"manual/step-{k:02d}.png", "alt": f"Step {k}: {title}",
                                                     "caption": f"{title}. Highlighted: " + ", ".join(h.split('_', 1)[1] for h in hl)
                                                     + ". Render of the CAD model, not a photo."}]})
    return out


def physical_tests() -> list[dict]:
    T = [("Button fit coupon", "the arcade button snaps into TEST_ButtonFit without force and does not turn"),
         ("First power-on", "with only USB-C power the night light glows briefly and the start chime plays"),
         ("Story at bedside volume", "a story plays clearly at a comfortable volume with no rattle from the shell"),
         ("Name capture", "holding the button lights the listening light and the spoken name plays back"),
         ("Closed-case warm-up", "after 30 minutes playing with the cover closed no surface is uncomfortably warm"),
         ("Stability and cord", "the closed gadget stands steady on a table and the cord leaves at the rear")]
    return [{"title": t, "expected": e, "kind": "physical", "status": "not_run"} for t, e in T]


def wiring_block(electrical: dict, purchased: list[dict], routes: list[dict]) -> dict:
    by_mod = {}
    for r in purchased:
        if r.get("module_id"):
            by_mod.setdefault(r["module_id"], r["name"])
    size = {"BP1": [62.8, 78.6, 1.6]}
    parts = []
    for i in electrical["instances"]:
        if i["ref"] in ("CBL1",):
            continue
        item = by_mod.get(i["module"])
        if item is None and i["ref"] == "BP1":
            item = next((r["name"] for r in purchased if r.get("refs") and "BP1" in r["refs"]), None)
        if item is None:
            continue
        kind = {"core": "board", "backplane": "board", "power_in": "connector"}.get(i.get("role"), "module")
        parts.append({"id": i["ref"], "name": i.get("name") or i["ref"], "kind": kind, "item": item, "module_id": i["module"],
                      "sizeMm": size.get(i["ref"])})
    ids = {p["id"] for p in parts}
    conns = []
    for c in electrical["connections"]:
        a, b = str(c["from"]).split(".")[0], str(c["to"]).split(".")[0]
        if a in ids and b in ids:
            conns.append({"from": c["from"], "to": c["to"], "kind": c.get("kind", "cable"), "via": c.get("via"),
                          "color": c.get("color"), "note": c.get("net")})
    for i in electrical["instances"]:
        if i.get("slot") in ("U1", "A1", "S1") and i["ref"] in ids:
            conns.append({"from": f"BP1.{i['slot']}", "to": f"{i['ref']}.HEADER", "kind": "socket", "via": "2.54 mm pin socket",
                          "color": None, "note": "plugs straight in, no cable"})
    rmap = {r["ref"]: r for r in routes}
    W = [("wire-sockets", "Seat the plug-in boards", "The core board, amplifier and microSD module plug straight into "
          "their sockets on the backplane. No cables.", ["PURCHASE_Backplane", "PURCHASE_CoreBoard", "PURCHASE_Amp", "PURCHASE_SdCard"],
          ["BP1.U1", "BP1.A1", "BP1.S1"], {"position": [-230, 340, 300], "target": [0, 0, 44]})]
    for ref, sid, label, comp in (("BTN1", "wire-button", "Story button to J_IO1", "PURCHASE_Button"),
                                  ("LED1", "wire-light", "Night light to J_LED", "PURCHASE_Led"),
                                  ("MIC1", "wire-mic", "Microphone to J_MIC", "PURCHASE_Mic"),
                                  ("SPK1", "wire-speaker", "Speaker to J_SPK", "PURCHASE_Speaker")):
        r = rmap.get(ref)
        if not r:
            continue
        inst = next(i for i in electrical["instances"] if i["ref"] == ref)
        wires = ", ".join(f"{w['module_pin']} to pin {w['slot_pin']} ({w['color']})" for w in inst["harness"]["wires"])
        W.append((sid, label, f"Use the {inst['harness']['cable']} cable: {wires}.", [r["id"], comp], [r["from"], r["to"]],
                  {"position": [-250, 300, 320], "target": [0, -10, 50]}))
    W.append(("wire-power", "USB-C power at the rear", "A 5 V USB-C supply plugs into J_PWR through the rear cover opening.",
              ["PURCHASE_UsbC", "PRINT_RearCover"], ["BP1.J_PWR", "SHELL.USB_OPENING"], {"position": [300, 420, 160], "target": [0, 40, 30]}))
    return {"runtime": "esp32-arduino", "board": "esp32-s3-devkitc-1", "code": "firmware/src/main.cpp", "parts": parts,
            "connections": conns,
            "steps": [{"id": a, "title": b, "text": c, "highlight": d, "labels": e, "camera": f} for a, b, c, d, e, f in W]}


def brief(spec: dict, size_mm: list[float], parts: list[dict]) -> dict:
    return {
        "version": 1, "concept_id": None, "title": spec["name"],
        "purpose": f"{spec['name']} is a bedside story companion for a five-year-old. It tells short bedtime stories "
                   "starring her favourite characters and remembers their names.",
        "size": {"w_mm": round(size_mm[0]), "d_mm": round(size_mm[1]), "h_mm": round(size_mm[2]),
                 "text": f"About {round(size_mm[0])} W x {round(size_mm[1])} D x {round(size_mm[2])} H mm (from the CAD bounding box)"},
        "functions": [{"title": "Bedtime stories", "text": "Tap the big button for a short offline story with one of her saved characters."},
                      {"title": "Remembers favourites", "text": "Hold the button and say a new favourite; it plays the name back and keeps it after a tap."},
                      {"title": "Night light", "text": "A soft glow during the story that fades out on a timer."}],
        "interaction": "One big button and one light. Tap to start or stop a story; hold to add a favourite. A parent page on "
                       "the home Wi-Fi edits favourites and volume.",
        "privacy_safety": "The microphone listens only while the button is held and the light shows it. Name clips stay on the "
                          "device's memory card and are never uploaded. This is a child-directed product, so it needs a safety "
                          "review before it can be sold as a built device.",
        "power": "USB-C 5 V from a certified phone adapter (not in the box). No batteries.",
        "parts": parts,
        "limits": ["Stories are pre-written, not made up live.",
                   "Recognising a young child's speech is not tested; a physical check is still needed.",
                   "The face and limbs do not move."],
        "safety_flags": spec["safety"]["flags"], "approved": True,
    }
