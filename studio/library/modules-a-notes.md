# Module library A: catalog rows 01-07, 14-18, 20-24, 37

Generated 2026-11-01. Data file: `modules-a.json` (21 module objects, validated with `json.load`). Generator kept at
`/private/tmp/claude-501/-Users-prasanna-os-brain/a52fdb81-fabb-42b2-ab4e-72b3ad52d494/scratchpad/modsA/gen_modules_a.py`
(scratchpad, not part of the repo).

Every number in the JSON either came from a page or drawing read on 2026-11-01 (listed in `evidence`) or is an estimate that is
also listed in that module's `uncertain`. Prices: `page_read` = seen on the vendor page; `search_snippet` = only seen in a
search result (robu.in returns HTTP 403 to fetches, so Robu prices are snippet-only).

## Choices

| Row | id | Reference product | Mechanical source |
|---|---|---|---|
| 01 | CORE_ESP32S3_DEVKITC1_N8R8 | Espressif ESP32-S3-DevKitC-1-N8R8 v1.1 | Espressif DXF + dimension PDF (parsed) |
| 02 | BACKPLANE_S3_V0_CUSTOM | ours (proposal only) | constraints from the parts below |
| 03 | USBC_RECEPTACLE_GCT_USB4105 | GCT USB4105-GF-A on the backplane (alt HRO TYPE-C-31-M-12) | estimates; KiCad footprint names verified |
| 03 | USBC_BREAKOUT_ADAFRUIT_4090 | Adafruit 4090 downstream breakout (fallback) | Adafruit Eagle board file |
| 04 | ADAPTER_RPI_15W_USBC_IN | Raspberry Pi 15W USB-C PSU, India plug | Raspberry Pi product page |
| 05 | CABLE_USBC_C2C_1M | Probots PRO3795 C-to-C 1 m | page |
| 06 | CABLE_JSTPH_4P_300MM | Evelta JST-PH 2.0 4-pin, 30 cm pre-crimped | page; housing size estimated |
| 07 | CABLE_JSTPH_3P_300MM | same listing, 3-pin | page; housing size estimated |
| 14 | OLED_096_SSD1306_I2C | generic 4-pin (Probots PRO1064) | Raystar REA012864D-03 contour drawing |
| 15 | OLED_130_SH1106_I2C | generic 4-pin (Probots PRO1243) | DisplayModule DM-OLED13-637 drawing |
| 16 | LCD_200_ST7789_240X320_WAVESHARE | Waveshare 2inch LCD Module, 240x320, PH2.0 8-pin | Waveshare 2D drawing |
| 17 | LED_WS2812B_1PX_MODULE | generic (Probots PRO4080) | none: estimates |
| 18 | LED_RING_12_WS2812_50MM | generic 12-LED ring, 50 mm OD (Probots PRO4077) | page (OD only) |
| 20 | MIC_INMP441_I2S | generic INMP441 module (Robocraze) | TDK chip datasheet; board size estimated |
| 21 | AMP_MAX98357A_I2S | Adafruit 3006 | Adafruit Eagle board file |
| 22 | SPK_40MM_4R_3W | PUI Audio AS04004PR-WR-R | PUI datasheet drawing |
| 22 | SPK_28MM_8R_2W | Same Sky CMS-28528N-L152(B) | Same Sky drawing |
| 23 | BUZZER_PASSIVE_TRANSISTOR_MODULE | Probots PRO2418 (transistor-driven, low-level trigger) | page (32 x 13 mm) |
| 24 | BUTTON_TACTILE_KY004 | KY-004 (Joy-IT spec 18 x 15 x 6 mm) | Joy-IT page |
| 24 | BUTTON_ARCADE_30MM_SNAPIN | Thingbits TH1801 30 mm snap-in | page + OBSF-30 reseller specs |
| 37 | MICROSD_SPI_MODULE_5V | generic Catalex-type (Probots PRO1129) | page (42 x 24 x 12, 4 x M2) |

Where a row has two objects, only the primary carries the sim `catalog_id`; the variant has `catalog_id: null`.

## Confirmed facts worth knowing

- **There is no official "ESP32-S3-DevKitC-1-N16R8".** Espressif's DevKitC-1 ordering table lists N8R8 and N32R16V (plus EOL
  N8/N8R2). The WROOM-1-N16R8 *module* is official; the N16R8 *boards* sold in India are clones. So the reference is the N8R8.
- DevKitC-1 geometry (from the DXF): PCB 62.865 x 25.40 mm, 0.5 mm corner chamfers, no mounting holes. Two 1x22 rows at
  **22.86 mm (0.9 in)** centre spacing, 1.27 mm from each long edge. Pin 1 of both rows sits 1.565 mm from the antenna-end edge.
  The WROOM-1 antenna overhangs that edge by **6.285 mm** (overall 69.15 mm). Use two `PinSocket_1x22` footprints, not `2x22`.
- GPIO rules on R8 (Octal PSRAM) boards: GPIO35/36/37 are taken by the PSRAM. Strapping pins are GPIO0, 3, 45, 46. GPIO19/20 carry USB.
  GPIO43/44 carry UART0 to the bridge. The RGB LED is on GPIO38 (v1.1) or GPIO48 (v1.0 and clones). The proposed backplane
  allocation in the JSON avoids all of these.
- WS2812B needs a data high of at least 0.7 x VDD (3.5 V at 5 V), so a 3.3 V ESP32 pin is outside the datasheet spec. The backplane needs a 5 V buffer on the LED line.
- INMP441 is bottom-ported, takes 1.62-3.63 V only, and draws 1.4 mA. 5 V destroys it.
- The KiCad footprint names in the JSON were checked against kicad-footprints tag 9.0.9.

## Risky or open items (decisions for Prasanna)

1. **Keyed-port scheme goes beyond the catalog.** Rows 06/07 cover only 4-pin and 3-pin cables. Audio, display and
   storage need 5/6/7/8-pin harnesses, and the 50-row catalog has no row for them. The proposal makes **pin count encode
   voltage and function**, so a cable cannot reach a wrong-voltage port:
   - PH 3P = IO, 3V3
   - PH 4P = I2C, 3V3
   - PH 5P = mic, 3V3
   - PH 6P = amp, 5 V
   - PH 7P = microSD, 5 V
   - PH 8P = LCD, 3V3
   - XH 3P = LED, 5 V (a second connector family)
   - PH 2P = power-in fallback

   This needs a decision, and probably new catalog rows.
2. **Datasheet-grade vs budget parts.** The speakers with real drawings (PUI about Rs 788, Same Sky about Rs 488) and a genuine Adafruit
   amp in India (about Rs 839) cost well above the blueprint planning ranges. Generic parts fit the budget but have no drawings. Every
   generic part needs a first-article measurement before CAD is frozen: OLED holes, LED boards, INMP441 board, microSD holes, buzzer, KY-004.
3. **Adapter BIS status rests on reseller claims.** ThinkRobotics says "BIS Certified" but shows no R-number. Every seen price
   (Rs 632-740) is above the Rs 600 planning ceiling. Verify the R-number on the physical unit and in the BIS CRS database.
4. **Pin order varies between vendors** for the OLEDs and KY modules. Harnesses must be wired by the silkscreen label, and incoming QC must check it.
5. **Power budget.** Wi-Fi TX peak (355 mA), amp peaks (up to about 0.8 A, estimated), a 12-LED ring at full white (about 650 mA) and the
   LCD can together pass 1.5 A. Firmware must cap LED brightness and volume unless CC sensing shows a 3 A source.
6. **Stock.** Out of stock at the time of reading: the Waveshare 2" LCD at Robocraze (Robu stock unconfirmed), the arcade button (red) at
   Thingbits, and the 40 mm generic speaker at Robocomp. In stock: the official DevKitC-1-N8R8 at Electropi (Rs 1,449 excl. GST).
7. **Back-feed.** I did not check whether the DevKitC's own USB VBUS can feed the backplane 5 V rail. Fit a diode or ideal diode until the schematic has been reviewed.
8. **Not verified in this session:** library version floors, Arduino menu names, the Qwiic colour/order convention, the antenna
   keep-out distance, USB4105 body dimensions and the USB-C plug overmold opening (estimated at 13 x 7 mm).
