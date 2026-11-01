# Sleepy Bear: build guide

A gentle bear that tells stories about her favorite friends. Finished size from the CAD: about **142 mm wide × 110 mm deep × 188 mm tall**. The build has 18 steps; each one has a checklist ID that stays the same across design versions.

**Before you start:** an adult assembles and tests this gadget. Keep screws, inserts, feet and cut cable-tie tails in a tray and away from children. Disconnect USB-C power before plugging, unplugging or moving any part. The heat-set insert step uses a hot soldering iron: work in a ventilated place, use its stand, and let parts cool. The USB-C supply stays outside the gadget. Route the cord where nobody trips on it and away from a bed. Use the gadget on a stable surface with its openings clear.

## How to read the pictures

The pictures are rendered from the saved CAD (`cad/components/*.brep`) by a script, so they show this exact design. They keep **+Z up** with the front at **-Y**. Left and right are the gadget's own: **+X is the gadget's left**, which is on your right when you face its front. A view from the rear or from below says so in its subtitle. A transparent shell only reveals what is inside; it is not an instruction to print clear plastic. Parts drawn apart (A panels) show the direction a part is fitted from; the distances are for clarity.

**Evidence limits:** purchased parts are simplified envelopes from published dimensions; pins, latches, threads and cable stiffness are simplified or absent. Cable routes are display envelopes. The wiring diagram is drawn from the saved slot map and electrical data. No picture proves a fit, a connection, retention or safety, and no physical test is recorded as passed in this guide.

## Parts and tools

Printed parts (files in `print/`, already in print orientation):

| Part | File | Material | Colour | Notes |
|---|---|---|---|---|
| `PRINT_Shell` | `print/PRINT_Shell.stl` | PLA | #8CC9C0 | about 227 g (slicer estimate); about 18.9 h (slicer estimate); upright on its flat base: face and decor print on vertical walls; tree supports for the domed top and the inside of the head |
| `PRINT_RearCover` | `print/PRINT_RearCover.stl` | PLA | #8CC9C0 | about 60 g (slicer estimate); about 6.7 h (slicer estimate); outer back face on the bed, lip and tubes up; no supports |
| `PRINT_SpeakerRing` | `print/PRINT_SpeakerRing.stl` | PLA | #ADD5CD | about 3 g (slicer estimate); about 0.3 h (slicer estimate); ring face on the bed, presser tube up; no supports |
| `PRINT_DisplayClip` | `print/PRINT_DisplayClip.stl` | PLA | #ADD5CD | about 1 g (slicer estimate); about 0.2 h (slicer estimate); flat strap face on the bed, fingers up; no supports |
| `PRINT_LedDiffuser` | `print/PRINT_LedDiffuser.stl` | PETG natural | #F7F5EB | about 0 g (slicer estimate); about 0.1 h (slicer estimate); outer face on the bed (smooth light face); print in natural PETG |
| `PRINT_LedClip` | `print/PRINT_LedClip.stl` | PLA | #ADD5CD | about 1 g (slicer estimate); about 0.1 h (slicer estimate); flat strap face on the bed, fingers up; no supports |
| `PRINT_MicClip` | `print/PRINT_MicClip.stl` | PLA | #ADD5CD | about 1 g (slicer estimate); about 0.1 h (slicer estimate); flat strap face on the bed, fingers up; no supports |
| `TEST_FitCoupon` | `print/TEST_FitCoupon.stl` | same as the shell | any | fit test, not a gadget part |
| `TEST_SeamMate` | `print/TEST_SeamMate.stl` | same as the shell | any | fit test, not a gadget part |
| `TEST_SeamShell` | `print/TEST_SeamShell.stl` | same as the shell | any | fit test, not a gadget part |

Purchased parts (quantities are items used, not packs to buy; prices and sources are in the parts list):

| Item | Quantity | Notes |
|---|---:|---|
| ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM) | 1 | Price seen at Electropi on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: Rs 1,449 excl. 18% GST (MRP 1,709.82 incl.); genuine Espressif N8R8; page says USB Type-C while Espressif's v1.1 guide says Micro-USB |
| Standard backplane PCB backplane-v1 (ours) | 1 | Price estimate; confirm before ordering. |
| USB-C 2.0 receptacle on backplane (5V sink, CC 5.1k Rd) | 1 | Price estimate; confirm before ordering. Source note: DigiKey listings: 24 positions (16+8 dummy), 5 A rating, 20,000 mating cycles, -40 to 85 C; INR price not seen |
| Speaker 40 mm round, 4 ohm, 3 W | 1 | Price seen at DigiKey India on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: qty 1; 25 in stock |
| Big 30 mm snap-in arcade push button (microswitch) | 1 | Price seen at Thingbits on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: TH1801; incl. GST (79.00 excl.); red sold out; red/yellow/blue/green/clear; 10.4 g |
| 1.3 in 128x64 mono OLED, SH1106, I2C 4-pin | 1 | Price seen at Probots on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: PRO1243; incl. GST (278.81 excl.); in stock; pin order VCC, GND, SCK/SCL, SDA; 0x3C, 0x3D via resistor move |
| Addressable RGB LED, single WS2812B 5050 on a mini board | 1 | Price seen at Probots on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: PRO4080; incl. GST (83.90 excl.); in stock; 4-7 V; no dimensions or pin labels on page |
| INMP441 I2S MEMS microphone module | 1 | Price seen at Robocraze on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: incl. GST; 272 in stock |
| MAX98357A I2S 3 W class-D mono amplifier breakout | 1 | Price seen at Robocraze on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: SmartElex MAX98357A breakout (third-party), incl. GST, 179 in stock; dims/pinout not shown, equivalence to Adafruit 3006 not confirmed |
| microSD SPI module with LDO and level shifter (Catalex-type) | 1 | Price seen at Probots on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: PRO1129; incl. GST (41.53 excl.); in stock; 4x M2 holes; level shifter 'like 74LVC125A' |
| USB-C to USB-C cable, 1 m, USB 2.0 | 1 | Price seen at Probots on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Source note: Rs 199 incl. GST (168.64 excl.); in stock; spec list says 3A but description says 2A / 2.4A |
| JST-PH 2.0 2-pin harness, 200 mm, pre-crimped both ends | 1 | Keyed cable, 2 pins; length class needed: 200 mm. Price estimate; confirm before ordering. |
| JST-PH 2.0 3-pin harness, 300 mm, pre-crimped both ends | 1 | Keyed cable, 3 pins; length class needed: 300 mm. Price estimate; confirm before ordering. |
| JST-PH 2.0 4-pin harness, 300 mm, pre-crimped both ends | 1 | Keyed cable, 4 pins; length class needed: 300 mm. Price estimate; confirm before ordering. |
| JST-PH 2.0 3-pin harness, 200 mm, pre-crimped both ends | 1 | Keyed cable, 3 pins; length class needed: 200 mm. Price estimate; confirm before ordering. |
| JST-PH 2.0 6-pin harness, 300 mm, pre-crimped both ends | 1 | Keyed cable, 6 pins; length class needed: 300 mm. Price estimate; confirm before ordering. |
| M2 / M2.5 socket head cap screws (SS304) + ISO 4032 hex nuts | 17 | Quantity 17 from the CAD model (4 x M2.5 cartridge screws x4; 4 x M2.5 cover screws x4; 9 x M2.5 module clamp screws x9); the normalizer's hardware list says 19. Price seen at ThinkRobotics on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Sold in packs of 10; this build uses 17. |
| Heat-set insert M2.5 x 4.0 (standard) | 17 | Price seen at ThinkRobotics on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Pack size not recorded; priced as one set per build for the 17 pieces used (the blueprint catalog prices this kind of row per set). Confirm the pack holds enough. |
| Adhesive bumper feet, hemispherical ~7.9 mm | 4 | Price estimate; confirm before ordering. Pack size not recorded; priced as one set per build for the 4 pieces used (the blueprint catalog prices this kind of row per set). Confirm the pack holds enough. |
| Cable tie 100 x 2.5 mm (nylon 6/6) + printed tie anchors | 6 | Price seen at Quartz Components on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. Sold in packs of 100; this build uses 6. |
| Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) | 1 | Price seen at Quartz Components on 2026-11-01: a retail snapshot, not reserved stock or a signed receipt. |
| Device ID label (QR + serial, silver/white polyester, thermal transfer) + packing kit | 1 | IndiaMART (Quick Barcode Solution, New Delhi) price 280 INR was only seen in a search result on 2026-11-01, not on the vendor page. Price estimate; confirm before ordering. |

Tools (not part of the kit):

- Printer or printing service
- Ruler or caliper
- Slicer
- Flush cutters or a deburring tool
- Soldering iron with a heat-set insert tip
- Tweezers
- A flat metal tool to level each insert
- Dry
- Non-metal surface
- A screwdriver that fits the supplied screws
- A computer with PlatformIO
- See `SOFTWARE_SETUP
- Md`

Fastener allocation:

| Where | Fastener | Quantity |
|---|---|---:|
| Heat-set into the printed bosses | M2.5 heat-set inserts | 17 |
| Core cartridge to its bosses | M2.5 cartridge screws | 4 |
| Story speaker | M2.5 module clamp screws | 2 |
| Animated sleepy eyes | M2.5 module clamp screws | 2 |
| Night light | M2.5 module clamp screws | 3 |
| Voice microphone | M2.5 module clamp screws | 2 |
| Closing part | M2.5 cover screws | 4 |

Threads are not modeled and no torque value has been established: tighten gently, a little at a time.

## Assembly steps

### 1. Check the parts

Checklist ID: `check-parts`.

**Use:** 1 × **Speaker 40 mm round, 4 ohm, 3 W**, 1 × **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)**, 1 × **Big 30 mm snap-in arcade push button (microswitch)**, 1 × **1.3 in 128x64 mono OLED, SH1106, I2C 4-pin**, 1 × **Addressable RGB LED, single WS2812B 5050 on a mini board**, 1 × **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)**, 1 × **INMP441 I2S MEMS microphone module**, 1 × **Standard backplane PCB backplane-v1 (ours)**, 1 × **ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM)**, 1 × **MAX98357A I2S 3 W class-D mono amplifier breakout**, 1 × **microSD SPI module with LDO and level shifter (Catalex-type)**, 17 × **M2.5 heat-set inserts**, 9 × **M2.5 module clamp screws**, 4 × **M2.5 cartridge screws**, 4 × **M2.5 cover screws** and 4 × **adhesive bumper feet**.

**Tools:** None. A clear table, a small parts tray and the parts list.

Lay every purchased item on a clear table and tick it off against the parts list: exact module, quantity and connector pin count. Keep screws, inserts and feet in a small tray, away from children.

Do not substitute a module. The backplane ports, cable pin counts and firmware pin numbers are fixed for these exact modules. Compare each module's printed pin labels with the wiring table (`manual/wiring.png`) now, before anything is printed or plugged.

The 7 printed parts come later (step 3). The exploded view shows where every part ends up.

**Expected result:** Every line of the parts list is present in the stated quantity, and each module's printed pin labels match the wiring table.

**Stop if:** a module, its pin labels or a cable's pin count differ from the parts list or the wiring table. Photograph both sides of the module and ask before continuing; do not swap in a similar part.

![Every purchased part laid out on a table, one of each, labelled with the quantity used and its name.](manual/step-01-parts.png)

*CAD envelopes, not photos: check the real parts against the parts list and the wiring table.*

![Exploded view of the Sleepy Bear: shell, front modules, core cartridge and closing part separated along one axis.](manual/exploded.png)

*Separation distances are for clarity only. Cables are hidden.*

### 2. Print and try the fit coupon

Checklist ID: `fit-coupon`.

**Use:** 1 × `TEST_FitCoupon`, `TEST_SeamMate`, `TEST_SeamShell` and, for trying only, 1 × **Big 30 mm snap-in arcade push button (microswitch)**, 1 × **M2.5 heat-set inserts** and 1 × **M2.5 cartridge screws**.

**Tools:** Printer or printing service, ruler or caliper.

Print `print/TEST_FitCoupon.stl`, `print/TEST_SeamMate.stl`, `print/TEST_SeamShell.stl` first, in the same material and with the same settings you will use for the shell. It is a small, cheap sample of the tight interfaces: try each real part in its matching feature by hand (Big 30 mm snap-in arcade push button (microswitch), M2.5 heat-set inserts and M2.5 cartridge screws). Parts should go in without force and stay put.

If something binds or is loose, stop before printing the shell and record what binds. Do not scale the design to force a fit. Printer accuracy is not assumed: the designed clearances are trial values that this real coupon checks.

**Expected result:** The real parts fit the printed coupon by hand without force and are held without wobble. Keep the coupon as a reference; it is not a gadget part.

**Stop if:** a part binds, a hole is too tight or loose, or an insert will not seat square. Measure the hole and record the material and printer; send that with a photo before printing the shell.

![The print STL files resting on a notional print bed in their exported orientation.](manual/print-bed.png)

*Orientation view only. It does not show supports or prove that a printer can print the part.*

### 3. Print and inspect the printed parts

Checklist ID: `print-parts`.

**Use:** 1 × `PRINT_Shell`, 1 × `PRINT_RearCover`, 1 × `PRINT_SpeakerRing`, 1 × `PRINT_DisplayClip`, 1 × `PRINT_LedDiffuser`, 1 × `PRINT_LedClip` and 1 × `PRINT_MicClip`.

**Tools:** Printer or printing service, slicer, flush cutters or a deburring tool, ruler or caliper.

Print each file from `print/` as exported: it is already in its print orientation, resting on the bed. Do not scale or rotate it.

- `print/PRINT_Shell.stl`: PLA; #8CC9C0; about 227 g (slicer estimate); about 18.9 h (slicer estimate); supports: yes; upright on its flat base: face and decor print on vertical walls; tree supports for the domed top and the inside of the head
- `print/PRINT_RearCover.stl`: PLA; #8CC9C0; about 60 g (slicer estimate); about 6.7 h (slicer estimate); supports: yes; outer back face on the bed, lip and tubes up; no supports
- `print/PRINT_SpeakerRing.stl`: PLA; #ADD5CD; about 3 g (slicer estimate); about 0.3 h (slicer estimate); supports: none; ring face on the bed, presser tube up; no supports
- `print/PRINT_DisplayClip.stl`: PLA; #ADD5CD; about 1 g (slicer estimate); about 0.2 h (slicer estimate); supports: none; flat strap face on the bed, fingers up; no supports
- `print/PRINT_LedDiffuser.stl`: PETG natural; #F7F5EB; about 0 g (slicer estimate); about 0.1 h (slicer estimate); supports: yes; outer face on the bed (smooth light face); print in natural PETG
- `print/PRINT_LedClip.stl`: PLA; #ADD5CD; about 1 g (slicer estimate); about 0.1 h (slicer estimate); supports: none; flat strap face on the bed, fingers up; no supports
- `print/PRINT_MicClip.stl`: PLA; #ADD5CD; about 1 g (slicer estimate); about 0.1 h (slicer estimate); supports: none; flat strap face on the bed, fingers up; no supports

The design colour is pale teal. Remove all support material. Deburr every opening (grille holes, windows, button and screw holes) so no strings or flakes are left. Check that every boss and clip is whole. Try the closing part on the empty shell before adding anything: it should seat evenly by hand.

**Expected result:** Every printed part is complete, unscaled and clean, with open holes and whole bosses, and the closing part seats on the empty shell by hand.

**Stop if:** a part is warped, a boss or clip is cracked, or the closing part will not seat without force. Reprint rather than sand or force it, and note which part and where.

![The print STL files resting on a notional print bed in their exported orientation.](manual/print-bed.png)

*Orientation view only. It does not show supports or prove that a printer can print the part.*

### 4. Install the heat-set inserts

Checklist ID: `install-inserts`.

**Use:** 17 × **M2.5 heat-set inserts**, 1 × `PRINT_Shell` and 1 × `PRINT_RearCover`.

**Tools:** Soldering iron with a heat-set insert tip (or a clean conical tip), tweezers, a flat metal tool to level each insert.

Fit all 17 inserts before any electronics go in. Set the iron to the temperature the insert supplier recommends for your print material. Stand each insert on its boss, then let the hot tip carry it in with light, straight pressure until its top is flush with the boss. Do not push it below flush, and do not tilt it. Lift the iron straight out and let the boss cool before the next one.

Work in a ventilated place and park the iron in its stand between inserts. The tip and the insert stay hot after you lift the iron.

**Expected result:** All 17 inserts sit square and flush in their bosses, and no boss is cracked or bulged.

**Stop if:** an insert goes in crooked or sinks below flush, or a boss cracks. Let it cool, photograph it, and ask before using that boss.

![Heat-set inserts shown above the printed bosses, then seated flush in them.](manual/step-04-inserts.png)

*Placement only. The image does not prove the insert is straight or fully melted in; check each one by eye.*

### 5. Assemble the core cartridge

Checklist ID: `assemble-cartridge`.

**Use:** 1 × **Standard backplane PCB backplane-v1 (ours)**, 1 × **ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM)**, 1 × **MAX98357A I2S 3 W class-D mono amplifier breakout** and 1 × **microSD SPI module with LDO and level shifter (Catalex-type)**.

**Tools:** Fingers only. Work on a clean, dry, non-metal surface.

1. Press the **ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM)** into the core-board sockets at **U1** (ESP32-S3-DevKitC-1 on 2 x 1x22 female sockets) so its USB connectors point toward +X (the gadget's left), over the board edge. Push evenly on both ends until it sits fully down. Every pin must be inside a socket; none may hang outside a row. Slot map: devkit USB ports face +x (robot's left), 1.0 mm past the board edge; antenna overhangs the -x edge.
2. Plug the **MAX98357A I2S 3 W class-D mono amplifier breakout** (AMP1) into socket **A1** (MAX98357A I2S amplifier breakout (1x7 + 2 x 1x1 sockets)). Match its pin labels to the labels next to the socket before pressing it down.
3. Plug the **microSD SPI module with LDO and level shifter (Catalex-type)** (SD1) into socket **S1** (microSD SPI module (Catalex-type) on a 1x6 socket). Match its pin labels to the labels next to the socket before pressing it down. Slot map: -x (board edge at the robot's right); card ejects toward -x.

The socket ids (U1, A1, S1) are the reference designators of the backplane; check the board's own markings and stop if they differ. Handle boards by their edges.

**Expected result:** The core board and every plug-in module sit fully down in their sockets, in the orientation shown, with no visible pin outside a socket row.

**Stop if:** a pin misses its socket, a module rocks, or a label on a module differs from the socket labels. Unplug it, photograph the header and the board markings, and ask. Do not bend pins straight by force.

![The core board, amplifier and microSD module lifted above the backplane, then seated in their sockets.](manual/step-05-cartridge.png)

*Shows placement and orientation only; it does not prove every pin is in its socket. Check each row by eye.*

### 6. Mount the core cartridge

Checklist ID: `mount-cartridge`.

**Use:** 4 × **M2.5 cartridge screws** and the assembled core cartridge from the previous step.

**Tools:** A screwdriver that fits the supplied screws.

Lower the cartridge onto its 4 bosses, component side up, with the core board's USB connectors toward +X (the gadget's left). The USB-C power input must line up with its opening in the shell. Fit the 4 × **M2.5 cartridge screws** through the board holes into the bosses. Tighten each one a little at a time, in a cross pattern, and stop as soon as it is snug: the board must not bend. No torque value has been established.

**Expected result:** The cartridge sits flat on all its bosses, every screw is snug, and the USB-C power input lines up with its opening.

**Stop if:** a screw will not start by hand, the board bends or rocks, or the USB-C input does not line up with its opening. Back the screws out and photograph the boss and the opening.

![The assembled core cartridge lifted out of the shell, then screwed onto its bosses.](manual/step-06-mount-cartridge.png)

*Placement only: the image does not prove screw engagement or that the USB-C port lines up. Check both by hand.*

### 7. Fit the speaker (Story speaker)

Checklist ID: `mount-speaker`.

**Use:** 1 × **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)**.

**Tools:** Fingers only.

Peel the backing and press the **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)** in place behind its opening (Story speaker), cone toward -Y (front). Hold it firmly; it cannot be repositioned easily.

Its cable connector points toward +X (the gadget's left); keep that side clear for the cable.

Do not press on the speaker cone. Keep the speaker leads free toward the cartridge.

**Expected result:** The speaker sits square behind its opening with the cone toward -Y (front), held without force, and its cable is free.

**Stop if:** the speaker does not sit flat, the cone is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The speaker shown moving into position, then installed behind Story speaker.](manual/step-07-speaker.png)

*Shows where the speaker sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 8. Fit the speaker (Story speaker)

Checklist ID: `mount-speaker-2`.

**Use:** 1 × **Speaker 40 mm round, 4 ohm, 3 W**, 1 × `PRINT_SpeakerRing` and 2 × **M2.5 module clamp screws**.

**Tools:** A screwdriver that fits the supplied screws.

Seat the **Speaker 40 mm round, 4 ohm, 3 W** behind its opening (Story speaker) so the cone faces -Y (front). It must sit flat and square to the opening.

Its cable connector points toward +X (the gadget's left); keep that side clear for the cable.

Fit `PRINT_SpeakerRing` as shown in the image to hold it.

Fix it with the 2 × **M2.5 module clamp screws**. Tighten gently and stop as soon as it is snug.

Do not press on the speaker cone. Keep the speaker leads free toward the cartridge.

**Expected result:** The speaker sits square behind its opening with the cone toward -Y (front), held without force, and its cable is free.

**Stop if:** the speaker does not sit flat, the cone is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The speaker shown moving into position, then installed behind Story speaker.](manual/step-08-speaker.png)

*Shows where the speaker sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 9. Fit the display (Animated sleepy eyes)

Checklist ID: `mount-eyes`.

**Use:** 1 × **1.3 in 128x64 mono OLED, SH1106, I2C 4-pin**, 1 × `PRINT_DisplayClip` and 2 × **M2.5 module clamp screws**.

**Tools:** A screwdriver that fits the supplied screws.

Hold the **1.3 in 128x64 mono OLED, SH1106, I2C 4-pin** against its bosses behind its opening (Animated sleepy eyes), screen toward -Y (front).

Its cable connector points toward +Z (up); keep that side clear for the cable.

Fit `PRINT_DisplayClip` as shown in the image.

Fix it with the 2 × **M2.5 module clamp screws**. Tighten gently and stop as soon as it is snug.

Hold the display by its edges and do not press on the glass.

**Expected result:** The display sits square behind its opening with the screen toward -Y (front), held without force, and its cable is free.

**Stop if:** the display does not sit flat, the screen is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The display shown moving into position, then installed behind Animated sleepy eyes.](manual/step-09-eyes.png)

*Shows where the display sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 10. Fit the button (Story and listening button)

Checklist ID: `mount-story-button`.

**Use:** 1 × **Big 30 mm snap-in arcade push button (microswitch)**.

**Tools:** Fingers only.

From the outside, push the **Big 30 mm snap-in arcade push button (microswitch)** straight into its opening, cap toward +Z (up), until its clips catch. Check from inside that every clip has caught; do not lever against the cap.

Its cable connector points toward -Z (down); keep that side clear for the cable.

**Expected result:** The button sits square behind its opening with the cap toward +Z (up), held without force, and its cable is free.

**Stop if:** the button does not sit flat, the cap is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The button shown moving into position, then installed behind Story and listening button.](manual/step-10-story-button.png)

*Shows where the button sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 11. Fit the light (Night light)

Checklist ID: `mount-status-light`.

**Use:** 1 × **Addressable RGB LED, single WS2812B 5050 on a mini board**, 1 × `PRINT_LedDiffuser`, 1 × `PRINT_LedClip` and 3 × **M2.5 module clamp screws**.

**Tools:** A screwdriver that fits the supplied screws.

Peel the backing and press the **Addressable RGB LED, single WS2812B 5050 on a mini board** in place behind its opening (Night light), LEDs toward -Y (front). Hold it firmly; it cannot be repositioned easily.

Its cable connector points toward +Y (rear); keep that side clear for the cable.

Fit `PRINT_LedDiffuser` as shown in the image.

Fit `PRINT_LedClip` as shown in the image.

Fix it with the 3 × **M2.5 module clamp screws**. Tighten gently and stop as soon as it is snug.

Do not look directly into the LEDs from close up when they are tested later.

**Expected result:** The light sits square behind its opening with the LEDs toward -Y (front), held without force, and its cable is free.

**Stop if:** the light does not sit flat, the LEDs are not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The light shown moving into position, then installed behind Night light.](manual/step-11-status-light.png)

*Shows where the light sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 12. Fit the microphone (Voice microphone)

Checklist ID: `mount-mic`.

**Use:** 1 × **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)**, 1 × `PRINT_MicClip` and 2 × **M2.5 module clamp screws**.

**Tools:** A screwdriver that fits the supplied screws.

Peel the backing and press the **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)** in place behind its opening (Voice microphone), sound port toward -Y (front). Hold it firmly; it cannot be repositioned easily.

Its cable connector points toward +Y (rear); keep that side clear for the cable.

Fit `PRINT_MicClip` as shown in the image.

Fix it with the 2 × **M2.5 module clamp screws**. Tighten gently and stop as soon as it is snug.

Keep the sound port clear: no foam, tape or glue over it.

**Expected result:** The microphone sits square behind its opening with the sound port toward -Y (front), held without force, and its cable is free.

**Stop if:** the microphone does not sit flat, the sound port is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The microphone shown moving into position, then installed behind Voice microphone.](manual/step-12-mic.png)

*Shows where the microphone sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 13. Fit the microphone (Voice microphone)

Checklist ID: `mount-mic-2`.

**Use:** 1 × **INMP441 I2S MEMS microphone module**.

**Tools:** Fingers only.

Peel the backing and press the **INMP441 I2S MEMS microphone module** in place behind its opening (Voice microphone), sound port toward -Y (front). Hold it firmly; it cannot be repositioned easily.

Its cable connector points toward +X (the gadget's left); keep that side clear for the cable.

Keep the sound port clear: no foam, tape or glue over it.

**Expected result:** The microphone sits square behind its opening with the sound port toward -Y (front), held without force, and its cable is free.

**Stop if:** the microphone does not sit flat, the sound port is not centred on its opening, or anything needs force. Photograph it from inside and outside before changing anything; do not drill, cut or glue.

![The microphone shown moving into position, then installed behind Voice microphone.](manual/step-13-mic.png)

*Shows where the microphone sits and which way it faces. It does not prove fit or retention; check it by hand.*

### 14. Plug the cables into their ports

Checklist ID: `plug-harnesses`.

**Use:** 6 module cables (already on the modules or in the kit).

**Tools:** Fingers only.

Disconnect USB-C power first. Plug each cable into the port named below. Every JST-PH plug is keyed and fits one way: line up the ridge on the plug with the slot in the header, then push straight in until it seats fully. Never pull a cable by its wires; hold the plug body.

1. **J_PWR** ← USB-C 2.0 receptacle on backplane (5V sink, CC 5.1k Rd) (PWR1), its cable.
2. **J_SPK** ← Speaker 40 mm round, 4 ohm, 3 W (SPK1), JST-PH 2.0 2p, 200 mm. Pin order at the port: 1 white +, 2 grey -.
3. **J_IO1** ← Big 30 mm snap-in arcade push button (microswitch) (BTN1), JST-PH 2.0 3p, 300 mm. Pin order at the port: 1 black COM, 3 white NO.
4. **J_I2C1** ← 1.3 in 128x64 mono OLED, SH1106, I2C 4-pin (DISP1), JST-PH 2.0 4p, 300 mm. Pin order at the port: 1 black GND, 2 orange VCC, 3 blue SDA, 4 yellow SCL.
5. **J_LED** ← Addressable RGB LED, single WS2812B 5050 on a mini board (LED1), JST-PH 2.0 3p, 200 mm. Pin order at the port: 1 black GND, 2 red 5V, 3 green DIN.
6. **J_MIC** ← Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) (MIC1), JST-PH 2.0 6p, 300 mm. Pin order at the port: 1 black GND, 2 orange VDD, 3 yellow SCK, 4 green WS, 5 blue SD, 6 black L/R.

The pin order is listed from pin 1. Wire colours are a cross-check, not the identifier: read the port name on the board. The full table is in the wiring diagram.

**Expected result:** Every cable is fully seated in the port named in the wiring table, and no port has the wrong cable.

**Stop if:** a plug will not go in without force, a cable's colours differ from the table, or a port named here is missing on your board. Photograph both ends; do not re-pin a housing or swap wires.

![KiCad render of this build's routed backplane, top side, with the printed port labels.](manual/backplane-pcb.png)

*Rendered by the PCB stage from pcb/backplane.kicad_pcb, not a photo of a made board. Compare the printed labels with the port names in this guide.*

![The core cartridge from above with every port this build uses labelled with the module that plugs into it.](manual/ports.png)

*Port positions from the saved CAD and slot map. It does not prove a plug is seated; push each one fully home.*

![Top view of the backplane with the used ports highlighted, each module's cable drawn in its wire colours, and the pin table.](manual/wiring.png)

*Diagram from the saved slot map and electrical data, not a photo. Read the port names on your board; colours are only a cross-check.*

### 15. Route and tie the cables

Checklist ID: `route-cables`.

**Use:** the cables plugged in the previous step.

**Tools:** Fingers only.

Lay each cable along the path shown, without twisting it, and leave a little slack at both plugs so no plug is pulled sideways. Keep cables away from screw bosses, the closing seam and the speaker cone.

Routes in the CAD:
- JST-PH 2.0 2p 200 mm: BP1.J_SPK to SPK1.CONN, CAD route about 58 mm for a 200 mm cable (enough slack).
- JST-PH 2.0 3p 300 mm: BP1.J_IO1 to BTN1.CONN, CAD route about 133 mm for a 300 mm cable (enough slack).
- JST-PH 2.0 4p 300 mm: BP1.J_I2C1 to DISP1.CONN, CAD route about 138 mm for a 300 mm cable (enough slack).
- JST-PH 2.0 3p 200 mm: BP1.J_LED to LED1.CONN, CAD route about 74 mm for a 200 mm cable (enough slack).
- JST-PH 2.0 6p 300 mm: BP1.J_MIC to MIC1.CONN, CAD route about 113 mm for a 300 mm cable (enough slack).

The routes are display envelopes. Cable stiffness and plug housings are not modeled.

**Expected result:** Every cable follows its route with slack at both plugs, nothing crosses the seam or a screw boss, and no plug is pulled sideways.

**Stop if:** a cable is too short to reach without pulling on its plug, or it has to cross the seam. Photograph the route; do not extend or cut a cable.

![The open gadget with every cable route shown in colour and the tie points labelled.](manual/step-15-routing.png)

*Routes show occupied space, not the exact cable path. Check slack and that nothing crosses the seam.*

### 16. Flash the firmware and run the bench self-test

Checklist ID: `flash-and-test`.

**Use:** The open gadget from the previous steps, 1 × **ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM)** already installed, and a data-capable USB cable (not in the kit).

**Tools:** A computer with PlatformIO (see `SOFTWARE_SETUP.md`).

Keep the gadget open. Connect a computer to the core board's USB port with a data-capable USB cable (the port faces +X (the gadget's left)). Then, from the delivered project folder:

```sh
cd firmware
pio run -t upload
pio device monitor -b 115200
```

PlatformIO builds the delivered source and writes it to the board. Follow `SOFTWARE_SETUP.md` to run the self-test, and check each module in turn:

- the speaker plays the test sound clearly, without crackle
- the speaker plays the test sound clearly, without crackle
- the display shows the test image the right way up
- each press of the button is reported once
- the light shows each test colour
- the microphone level reacts when you speak near it
- the microphone level reacts when you speak near it

Unplug the USB cable before you close the gadget. These are checks for you to make; nothing here records them as passed.

**Expected result:** The upload finishes without errors and every module passes its self-test check by your observation.

**Stop if:** the upload fails, the board resets repeatedly, any part gets warm, or a module does not respond. Unplug USB first, then save the monitor output and check that module's cable against the wiring table.

![The open gadget with the core board's USB port and every module labelled.](manual/open-back.png)

*Placement view. It does not show that the firmware runs or that any module works.*

### 17. Close the rear cover

Checklist ID: `close-cover`.

**Use:** 1 × `PRINT_RearCover`, 4 × **M2.5 cover screws** and 4 × **adhesive bumper feet**.

**Tools:** A screwdriver that fits the supplied screws.

Check that the USB cable from the bench test is unplugged and that no cable lies across the seam. Bring `PRINT_RearCover` straight on along -Y (front). It should seat evenly by hand, without pressure. Fit the 4 × **M2.5 cover screws** and tighten them a little at a time, in a cross pattern, until snug. Never use the screws to pull the seam shut. No torque value has been established.

Turn it over and stick the 4 × **adhesive bumper feet** at the positions shown, on a clean, dry surface.

**Expected result:** The seam is closed evenly all round, no cable is pinched, and the gadget stands level on its feet.

**Stop if:** the part rocks, a gap stays open, or it needs screw force to close. Open it again and look for a cable, a plug or a print edge in the way; photograph it if you cannot find it.

![The closing part separated from the shell with its screws, then closed.](manual/step-17-close.png)

*The image does not prove that the seam closes without pinching a cable. Close it by hand first.*

### 18. Final checks

Checklist ID: `final-checks`.

**Use:** The finished Sleepy Bear and a USB-C 5 V supply (not included).

**Tools:** None (adult supervision).

Connect a USB-C 5 V supply (not included). Keep the supply and its plug outside the gadget, and route the cord where nobody trips on it and away from a bed.

Work through the final checks listed at the end of this guide. They are actions for you, not passed results: record what you observe. Do not leave a gadget that fails a check in use.

**Expected result:** Every final check is done and recorded, and each one behaves as described.

**Stop if:** any check fails, the gadget gets hot, smells, rattles or tips over. Unplug it and do not use it until the cause is found.

![The finished Sleepy Bear from the front, with each control and opening labelled.](manual/assembled.png)

*Saved CAD appearance only. Complete the final checks below before regular use.*

## Wiring

The full diagram is `manual/wiring.png` (vector copy: [manual/wiring.svg](manual/wiring.svg)). In the Build tab the same connections are listed as steps:

- **Meet the backplane** (`meet-backplane`): The backplane carries the core board in U1, the amplifier in A1, the microSD module in S1 and the USB-C power input (J_PWR). Every plug-in module has its own keyed JST-PH port. This build uses J_I2C1, J_IO1, J_LED, J_MIC, J_SPK. Ports J_DISP, J_I2C2, J_I2C3, J_IO2, J_IO3 stay empty. Disconnect USB-C power before plugging or unplugging anything.
- **Seat the core board in U1** (`seat-core`): ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM) (U1) plugs straight into socket U1. No cable.
- **Seat the amplifier in A1** (`seat-amp`): MAX98357A I2S 3 W class-D mono amplifier breakout (AMP1) plugs straight into socket A1. No cable.
- **Seat the microSD module in S1** (`seat-sd`): microSD SPI module with LDO and level shifter (Catalex-type) (SD1) plugs straight into socket S1. No cable.
- **Connect the display to J_I2C1** (`wire-eyes`): Plug the JST-PH 2.0 4p from the 1.3 in 128x64 mono OLED, SH1106, I2C 4-pin (DISP1) into port J_I2C1. The plug is keyed; push it straight in until it seats. From pin 1: pin 1 black GND; pin 2 orange VCC; pin 3 blue SDA (GPIO8); pin 4 yellow SCL (GPIO9).
- **Connect the button to J_IO1** (`wire-story-button`): Plug the JST-PH 2.0 3p from the Big 30 mm snap-in arcade push button (microswitch) (BTN1) into port J_IO1. The plug is keyed; push it straight in until it seats. From pin 1: pin 1 black COM; pin 3 white NO (GPIO4).
- **Connect the light to J_LED** (`wire-status-light`): Plug the JST-PH 2.0 3p from the Addressable RGB LED, single WS2812B 5050 on a mini board (LED1) into port J_LED. The plug is keyed; push it straight in until it seats. From pin 1: pin 1 black GND; pin 2 red 5V; pin 3 green DIN.
- **Connect the microphone to J_MIC** (`wire-mic`): Plug the JST-PH 2.0 6p from the Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) (MIC1) into port J_MIC. The plug is keyed; push it straight in until it seats. From pin 1: pin 1 black GND; pin 2 orange VDD; pin 3 yellow SCK (GPIO41); pin 4 green WS (GPIO42); pin 5 blue SD (GPIO40); pin 6 black L/R.
- **Connect the speaker to J_SPK** (`wire-speaker`): Plug the JST-PH 2.0 2p from the Speaker 40 mm round, 4 ohm, 3 W (SPK1) into port J_SPK. The plug is keyed; push it straight in until it seats. From pin 1: pin 1 white +; pin 2 grey -.
- **Power comes in at J_PWR** (`power-input`): A USB-C 5 V supply plugs into J_PWR at the rear edge of the backplane; the board feeds the core board from it. Plug it in only after everything else is connected and the gadget is closed.

## Stop points and evidence to collect

Stop at a failed fit, a missing part or anything that needs force. Note the checklist ID and collect:

- **Print or fit:** the part name, material and printer, the measured hole or gap, and a photo of where it binds.
- **Module or cable:** the exact marking on the module, photos of both ends of the cable, and the port it should go to.
- **Firmware or self-test:** the saved serial monitor output and which module did not respond.
- **Closing:** a photo of the open seam showing what is in the way.

Do not invent pin assignments, substitute modules, force a closure or leave out a fastener to continue.

## Final checks to record

The following physical checks are actions for you, not passed test results:

- **Power-on and self-test:** On USB-C power the gadget starts, and every module passes its self-test as described in SOFTWARE_SETUP.md: button, display, light, microphone, speaker.
- **Story, stop and volume:** A tap plays a story through the speaker; a tap during a story stops it; the volume is comfortable at the listening position.
- **Listening light and confirmation:** The microphone opens only while the button is held, the light shows it is listening, and a new favourite is kept only after an explicit confirming tap.
- **Memory after restart:** A confirmed favourite is still there after unplugging and re-plugging power, and appears in a later story.
- **Local API:** From a phone or computer on the same network, GET /api/state returns the gadget's state and POST /api/action performs an action.
- **Closed-case heat, power and retention:** During a 30-minute session with the gadget closed there are no resets, no part becomes uncomfortably warm to touch, nothing rattles and no cable is pinched.
- **Stability:** The gadget stands level on its feet and does not tip or slide during normal use by a child under adult supervision.

Digital checks recorded by this build (software and design files only, not the physical gadget):

- **Firmware compiles:** PlatformIO builds the delivered firmware for esp32-s3-devkitc-1. Status: passed (`firmware/firmware.bin`).
- **Firmware unit tests:** The hardware-free unit tests of the firmware logic pass. Status: passed (`firmware/test-results.txt`).
- **Backplane design-rule check:** The routed backplane has no design-rule errors (warnings and waived items excluded). Status: passed (`.gstudio/stages/pcb.json`).

## Digital evidence and its limits

`build.json` holds these steps, the wiring connections and the checks above in machine-readable form. `cad/` holds the geometry the pictures were rendered from; `electrical.json` holds the pin and harness data the wiring diagram was drawn from; `manual/manual-summary.json` counts the images and confirms every one is used here. None of this establishes a physical fit, an electrical or safety result, audible volume, surface temperature or stability.
