# Module library, part B: notes

Companion to `modules-b.json` (31 objects, blueprint §3.3 rows 08-13, 19, 25-33, 36, 38, 41-50). Rows 34 and 35 are skipped because they are for wet environments. Researched on 2026-11-01.

## How to read the data

- **Price status.** `page_read` means the product page or its Shopify product JSON was fetched and the price was seen there. `search_snippet` means the price came from a store's search API or a web search result, and no product page was opened.
- **Uncertain values.** Any number not read from a primary page (maker drawing, Eagle board file, or datasheet) is listed under `uncertain` in that object. `null` dimensions mean "set by another row or by the design", not zero.
- **Robu.in could not be read.** It returns HTTP 403 to every automated fetch, so none of its prices appear here. Robocraze, Quartz Components, ThinkRobotics, Probots and Evelta could be read.
- **Strongest sources.** Adafruit AHT20 (outline and holes parsed from Adafruit's own `.brd`), Pololu 2490 / 2130 / micro-metal-gearmotor drawings, the Omron SS datasheet, the Tag-Connect TC2030-NL drawing, the TowerPro SG90 sheet (Rapid), and the CNC Kitchen insert table.

## Choices

| Row | id | Reference product | Main source of dimensions |
|---|---|---|---|
| 08 | STRAIN_TIE_NYLON66_100X2_5 | Nylon 6/6 tie, 100 x 2.5 mm (Quartz, ₹44 per 100), plus printed tie anchors | Listing title |
| 09 | SCREW_SET_M2_M2_5_SHCS_A2 | SS304 SHCS (ThinkRobotics); ISO 4032 nuts | Wermac ISO 4032 table; ThinkRobotics variant list |
| 10 | INSERT_HEATSET_M2_5X4_STD / INSERT_HEATSET_M3X5_7_STD | CNC Kitchen standard inserts | CNC Kitchen table: hole 4.0, OD 4.6, wall ≥1.6 |
| 11 | FEET_BUMPON_SJ5302_CLEAR | 3M Bumpon SJ5302, about 7.9 x 2.2 mm | Distributor snippets only |
| 12 | CORE_CARTRIDGE_FRAME_V0 | Our printed part (constraints only) | n/a |
| 13 | SPK_GASKET_FOAM_25X3_ADH | Single-sided foam strip, 25 x 3 mm (Quartz, ₹64), plus printed clamp ring | Listing |
| 19 | DIFFUSER_PRINTED_NATURAL_PETG | Natural PETG printed wall; opal acrylic as the alternative | No validated numbers; a test matrix is proposed |
| 25 | ENCODER_KY040_PUSH | KY-040 (Robocraze ₹59) | ProtoSupplies 26 x 20 x 30; Alps EC11E drawing |
| 26 | TOUCH_TTP223_SMALL_15X11 | Small TTP223 board | TinyTronics 15 x 11 |
| 27 | LIGHT_BH1750_GY302_I2C (+ LIGHT_LDR_LM393_4PIN as the alternative) | GY-302 (Robocraze ₹110) | Handsontec drawing 18.5 x 13.5 |
| 28 | TEMPHUM_AHT20_ADAFRUIT_4566 | Adafruit 4566 (Evelta ₹625 incl. GST) | Adafruit Eagle `.brd`: 25.4 x 17.78, 4 x Ø2.5 holes |
| 29 | BARO_BMP280_GY_3V3_I2C | GY-BMP280-3.3 (Robocraze ₹39) | components101: 15 x 11.5 |
| 30 | TOF_VL53L0X_POLOLU_2490 | Pololu 2490 | Pololu drawing: 12.7 x 17.8, 2 x Ø2.2 holes |
| 31 | PIR_AM312_MINI (+ PIR_HCSR501_STD as the alternative) | AM312 (Probots ₹69) | TinyTronics / Probots: PCB 10 x 8, 12 x 27 overall, lens Ø10 |
| 32 | IMU_MPU6050_GY521 | GY-521 | ProtoSupplies 21 x 16 |
| 33 | HALL_KY003_A3144 | KY-003 (Quartz ₹23) | ProtoSupplies' similar 3-pin module, 20 x 15 |
| 36 | RTC_DS3231_ZS042 | ZS-042 DS3231 + AT24C32 | ProtoSupplies 38 x 22; 14 mm tall (snippet) |
| 38 | SERVO_SG90_TOWERPRO | TowerPro SG90 (Robocraze ₹106) | TowerPro/Rapid drawing: 32.2 across tabs, 22.2 x 11.8 body, 31 tall |
| 41 | GEARMOTOR_N20_6V_LOWRPM | N20 6 V, 56 RPM (ThinkRobotics, out of stock) | Pololu drawing: 12 x 10 face, M1.6 holes ±4.5, Ø3 D-shaft |
| 42 | MOTOR_DRV_DRV8833_POLOLU_2130 | Pololu 2130 form factor (Robocraze ₹72 listing has the same specs) | Pololu drawing: 12.7 x 20.3 |
| 43 | ENDSTOP_OMRON_SS01GL | Omron SS-01GL (micro-load) | Omron: 19.8 x 6.4 x 10.2, 2 x Ø2.35 holes at 9.5 mm pitch |
| 44 | FAN_3010_5V_GUARDED | 5 V 3010 fan (Robocraze ₹89) with a printed guard | Size code; 24 mm hole pattern from snippets |
| 45 | SERVO_BRACKET_SG90_ACRYLIC | ProtoSupplies acrylic bracket (cutout 13 x 24), to be printed in-house | ProtoSupplies |
| 46 | SERVO_HORN_KIT_SG90_SUPPLIED | Horns supplied with the SG90, plus printed linkage | n/a (spline count unknown) |
| 47 | MAGNET_NDFEB_D8X5_N35 | NdFeB 8 x 5 N35, Ni-Cu-Ni (Quartz ₹23 each) | Listing |
| 48 | SHELL_PANEL_FASTENED_V0 | Our printed part (constraints only) | n/a |
| 49 | TEST_JIG_TAGCONNECT_TC2030_NL | Tag-Connect TC2030-NL footprint; P75-H2 pogo pins (Probots ₹35) as the low-cost alternative | Tag-Connect drawing |
| 50 | LABEL_QR_POLYESTER_50X25_PACK | 50 x 25 mm thermal-transfer polyester label | Listing snippets only |

## Risks and decisions for other rows

1. **Connector policy gaps (rows 06/07).** Several modules don't fit a 4-pin I2C port or a 3-pin port:
   - the encoder needs 5 pins;
   - the DRV8833 control side needs 6;
   - the motor and fan need 2-pin outputs;
   - the SG90 plug is a non-keyed 2.54 mm connector.

   The blueprint needs to approve JST PH 2P, 5P and 6P plus a keyed servo adapter, or these modules need a different wiring scheme. Every harness colour and pin order in the JSON is a proposal for the row 06/07 owner.
2. **5 V on 3-pin ports.** The HC-SR501 needs 4.5 V or more, and the KY-003's A3144 is out of spec at 3.3 V. The KY-003 also pulls its output up to 5 V, which is unsafe for the 3.3 V ESP32-S3 GPIO. Three ways to handle this:
   - 3-pin ports that can be strapped to 5 V, with GPIO protection;
   - remove the pull-up on the module;
   - choose a 3.3 V Hall or reed part instead.
3. **I2C address clash at 0x68.** The MPU-6050 and the DS3231 both default to 0x68. If both are fitted, set AD0 high on the MPU-6050 (0x69). The other addresses (AHT20 0x38, BH1750 0x23, BMP280 0x76, VL53L0X 0x29) don't clash with each other. The AHT20 can only be used once per bus.
4. **Batteries and magnets.** The ZS-042 RTC carries a coin cell, and its charge circuit is meant for an LIR2032. The blueprint says to avoid batteries at launch, so a battery-free RTC that re-syncs from NTP is the safer default. Magnets (row 47) and coin cells are ingestion hazards: they must be fully enclosed, and the product must stay adult-only.
5. **ESP32-S3 antenna keep-out.** The 15 mm clearance comes from secondary sources. Check it against the Espressif module datasheet before freezing the cartridge (row 12).
6. **Generic module footprints.** Hole positions for the GY-302, GY-BMP280, GY-521, ZS-042, KY-040 and HC-SR501 were not confirmed. Hold these boards in printed cradles until a measured sample exists. The Indian VL53L0X and DRV8833 listings are generic boards, not the Pololu reference. The generic GY-VL53L0XV2 has a different footprint: 25 x 10.7 mm with a 19.3 mm hole pitch (snippet).
7. **Prices against the planning ranges.** The AHT20 is above its range (₹625 against 100-350). The BMP280 (₹33-39), the TTP223 (₹9-25) and the KY-040 (₹47-59) sit far below theirs. The N20 low-RPM variant is out of stock; the only N20 in stock at Robocraze is a 600 RPM version.
8. **Values that must be measured on samples:**
   - SG90 tab hole pitch (about 27.5 mm, estimated) and current draw;
   - KY-040 detents per turn (sources say 20 or 30);
   - TTP223 maximum wall thickness;
   - diffuser wall thickness and LED gap;
   - insert hole size for the Indian inserts;
   - fan current (the Robocraze page says both 20 mA and 0.2 A).
9. **Libraries.** Arduino library names are listed without pinned versions; versions were not checked.
