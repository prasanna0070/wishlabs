# Gadget Studio module library

`modules.json` is the merged library (foundation). Only these modules may appear in a design spec. Sources: `modules-a.json` (rows 01-07, 14-18, 20-24, 37) and `modules-b.json` (rows 08-13, 19, 25-33, 36, 38, 41-50); their `*-notes.md` explain every choice. Regenerate with `python3 -I tools/merge_modules.py` after either source changes, and the backplane with `python3 -I tools/gen_backplane_v1.py` (run that first).

Optional packs (`packs/<name>.json`, CATALOG-PLAN 0.5) add rows marked `pack`; a row whose pack is off (`packs/enabled.json`, env `GS_PACKS_ENABLED` / `GS_PACKS_OFF`) is invisible to `gstudio.library.Library`. Pack rows in this file: FLOAT_SWITCH_VERTICAL_PP (sensing), SERVO_MG90S_ARM (motion), SERVO_SG90_ARM (motion), SOIL_CAP_V12_ANALOG (sensing).

Prices are blueprint §3.3 planning ranges (INR, often excluding GST and labour), not quotes. Every unverified number is listed in that module's `uncertain`. `v1` = how the module connects on `backplane-v1.json`.

| id | row | role | name | dims mm (x × y × z) | aperture | v1 slot | price ₹ |
|---|---|---|---|---|---|---|---|
| `CORE_ESP32S3_DEVKITC1_N8R8` | 01 | core | ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM) | 69.15 × 25.4 × 13.4 | none | U1 | 650-1300 |
| `BACKPLANE_V1` | 02 | backplane | Standard backplane PCB backplane-v1 (ours) | 62.8 × 78.6 × 17.6 | usb_port | backplane | 120-450 |
| `USBC_BREAKOUT_ADAFRUIT_4090` | 03 | power_in | USB-C downstream breakout with 5.1k CC resistors (fallback for USBC_POWER_A) | 20.32 × 13.97 × 5 | usb_port | not on v1 | 80-250 |
| `USBC_RECEPTACLE_GCT_USB4105` | 03 | power_in | USB-C 2.0 receptacle on backplane (5V sink, CC 5.1k Rd) | 8.94 × 7.35 × 3.2 | usb_port | J_PWR | 80-250 |
| `ADAPTER_RPI_15W_USBC_IN` | 04 | adapter | Raspberry Pi 15W USB-C power supply (5.1 V 3 A, India plug) | ? × ? × ? | none | in_box | 250-600 |
| `CABLE_USBC_C2C_1M` | 05 | usb_cable | USB-C to USB-C cable, 1 m, USB 2.0 | 1000 × ? × ? | none | in_box | 70-180 |
| `CABLE_JSTPH_4P_100` | 06 | harness | JST-PH 2.0 4-pin harness, PHR-4 both ends, 100 mm | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_150` | 06 | harness | JST-PH 2.0 4-pin harness, PHR-4 both ends, 150 mm | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_200` | 06 | harness | JST-PH 2.0 4-pin harness, PHR-4 both ends, 200 mm | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_300` | 06 | harness | JST-PH 2.0 4-pin harness, PHR-4 both ends, 300 mm | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_300MM` | 06 | harness | Keyed 4-pin JST-PH 2.0 mm cable, pre-crimped, 300 mm | 300 × 9.9 × 4.5 | none | harness | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_100` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 100 mm, straight-through | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_100_X2134` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 100 mm, made to drawing GS-H-PH4-D1X4-X2134 (module-end positions = port pins 2,1,3,4) | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_100_X2143` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 100 mm, made to drawing GS-H-PH4-D1X4-X2143 (module-end positions = port pins 2,1,4,3) | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_100_X4321` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 100 mm, made to drawing GS-H-PH4-D1X4-X4321 (module-end positions = port pins 4,3,2,1) | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_150` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 150 mm, straight-through | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_150_X2134` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 150 mm, made to drawing GS-H-PH4-D1X4-X2134 (module-end positions = port pins 2,1,3,4) | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_150_X2143` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 150 mm, made to drawing GS-H-PH4-D1X4-X2143 (module-end positions = port pins 2,1,4,3) | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_150_X4321` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 150 mm, made to drawing GS-H-PH4-D1X4-X4321 (module-end positions = port pins 4,3,2,1) | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_200` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 200 mm, straight-through | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_200_X2134` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 200 mm, made to drawing GS-H-PH4-D1X4-X2134 (module-end positions = port pins 2,1,3,4) | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_200_X2143` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 200 mm, made to drawing GS-H-PH4-D1X4-X2143 (module-end positions = port pins 2,1,4,3) | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_200_X4321` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 200 mm, made to drawing GS-H-PH4-D1X4-X4321 (module-end positions = port pins 4,3,2,1) | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_300` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 300 mm, straight-through | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_300_X2134` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 300 mm, made to drawing GS-H-PH4-D1X4-X2134 (module-end positions = port pins 2,1,3,4) | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_300_X2143` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 300 mm, made to drawing GS-H-PH4-D1X4-X2143 (module-end positions = port pins 2,1,4,3) | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_DUPONT1X4_300_X4321` | 06 | harness | JST-PH 2.0 4-pin to Dupont 1x4 female harness, 300 mm, made to drawing GS-H-PH4-D1X4-X4321 (module-end positions = port pins 4,3,2,1) | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_2P_100` | 06b | harness | JST-PH 2.0 2-pin harness, PHR-2 both ends, 100 mm | 100 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_150` | 06b | harness | JST-PH 2.0 2-pin harness, PHR-2 both ends, 150 mm | 150 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_200` | 06b | harness | JST-PH 2.0 2-pin harness, PHR-2 both ends, 200 mm | 200 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_300` | 06b | harness | JST-PH 2.0 2-pin harness, PHR-2 both ends, 300 mm | 300 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_6P_100` | 06c | harness | JST-PH 2.0 6-pin harness, PHR-6 both ends, 100 mm | 100 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_150` | 06c | harness | JST-PH 2.0 6-pin harness, PHR-6 both ends, 150 mm | 150 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_200` | 06c | harness | JST-PH 2.0 6-pin harness, PHR-6 both ends, 200 mm | 200 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_300` | 06c | harness | JST-PH 2.0 6-pin harness, PHR-6 both ends, 300 mm | 300 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_DUPONT2X1X3_100` | 06c | harness | JST-PH 2.0 6-pin to 2 x Dupont 1x3 female harness, 100 mm, made to drawing GS-H-PH6-D2X1X3 (module-end positions = port pins 6,4,3,5,2,1) | 100 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_DUPONT2X1X3_150` | 06c | harness | JST-PH 2.0 6-pin to 2 x Dupont 1x3 female harness, 150 mm, made to drawing GS-H-PH6-D2X1X3 (module-end positions = port pins 6,4,3,5,2,1) | 150 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_DUPONT2X1X3_200` | 06c | harness | JST-PH 2.0 6-pin to 2 x Dupont 1x3 female harness, 200 mm, made to drawing GS-H-PH6-D2X1X3 (module-end positions = port pins 6,4,3,5,2,1) | 200 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_DUPONT2X1X3_300` | 06c | harness | JST-PH 2.0 6-pin to 2 x Dupont 1x3 female harness, 300 mm, made to drawing GS-H-PH6-D2X1X3 (module-end positions = port pins 6,4,3,5,2,1) | 300 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_8P_100` | 06d | harness | JST-PH 2.0 8-pin harness, PHR-8 both ends, 100 mm | 100 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_150` | 06d | harness | JST-PH 2.0 8-pin harness, PHR-8 both ends, 150 mm | 150 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_200` | 06d | harness | JST-PH 2.0 8-pin harness, PHR-8 both ends, 200 mm | 200 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_300` | 06d | harness | JST-PH 2.0 8-pin harness, PHR-8 both ends, 300 mm | 300 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_3P_100` | 07 | harness | JST-PH 2.0 3-pin harness, PHR-3 both ends, 100 mm | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_150` | 07 | harness | JST-PH 2.0 3-pin harness, PHR-3 both ends, 150 mm | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_200` | 07 | harness | JST-PH 2.0 3-pin harness, PHR-3 both ends, 200 mm | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_300` | 07 | harness | JST-PH 2.0 3-pin harness, PHR-3 both ends, 300 mm | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_300MM` | 07 | harness | Keyed 3-pin JST-PH 2.0 mm cable, pre-crimped, 300 mm | 300 × 7.9 × 4.5 | none | harness | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_100` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 100 mm, straight-through | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_100_X231` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 100 mm, made to drawing GS-H-PH3-D1X3-X231 (module-end positions = port pins 2,3,1) | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_100_X321` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 100 mm, made to drawing GS-H-PH3-D1X3-X321 (module-end positions = port pins 3,2,1) | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_150` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 150 mm, straight-through | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_150_X231` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 150 mm, made to drawing GS-H-PH3-D1X3-X231 (module-end positions = port pins 2,3,1) | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_150_X321` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 150 mm, made to drawing GS-H-PH3-D1X3-X321 (module-end positions = port pins 3,2,1) | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_200` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 200 mm, straight-through | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_200_X231` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 200 mm, made to drawing GS-H-PH3-D1X3-X231 (module-end positions = port pins 2,3,1) | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_200_X321` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 200 mm, made to drawing GS-H-PH3-D1X3-X321 (module-end positions = port pins 3,2,1) | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_300` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 300 mm, straight-through | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_300_X231` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 300 mm, made to drawing GS-H-PH3-D1X3-X231 (module-end positions = port pins 2,3,1) | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X3_300_X321` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x3 female harness, 300 mm, made to drawing GS-H-PH3-D1X3-X321 (module-end positions = port pins 3,2,1) | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X4_100_X2103` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x4 female harness, 100 mm, made to drawing GS-H-PH3-D1X4-X2103 (module-end positions = port pins 2,1,-,3) | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X4_150_X2103` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x4 female harness, 150 mm, made to drawing GS-H-PH3-D1X4-X2103 (module-end positions = port pins 2,1,-,3) | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X4_200_X2103` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x4 female harness, 200 mm, made to drawing GS-H-PH3-D1X4-X2103 (module-end positions = port pins 2,1,-,3) | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_DUPONT1X4_300_X2103` | 07 | harness | JST-PH 2.0 3-pin to Dupont 1x4 female harness, 300 mm, made to drawing GS-H-PH3-D1X4-X2103 (module-end positions = port pins 2,1,-,3) | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_SPADE28_100` | 07 | harness | JST-PH 2.0 3-pin to 2 x 2.8 mm female spade harness, 100 mm, made to drawing GS-H-PH3-SP28 (module-end positions = port pins 1,3) | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_SPADE28_150` | 07 | harness | JST-PH 2.0 3-pin to 2 x 2.8 mm female spade harness, 150 mm, made to drawing GS-H-PH3-SP28 (module-end positions = port pins 1,3) | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_SPADE28_200` | 07 | harness | JST-PH 2.0 3-pin to 2 x 2.8 mm female spade harness, 200 mm, made to drawing GS-H-PH3-SP28 (module-end positions = port pins 1,3) | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTPH_3P_SPADE28_300` | 07 | harness | JST-PH 2.0 3-pin to 2 x 2.8 mm female spade harness, 300 mm, made to drawing GS-H-PH3-SP28 (module-end positions = port pins 1,3) | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `CABLE_JSTXH_3P_100` | 07 | harness | JST-XH 2.5 3-pin harness, XHP-3 both ends, 100 mm | 100 × 8.9 × 4.5 | none | J_LED | 30-100 |
| `CABLE_JSTXH_3P_150` | 07 | harness | JST-XH 2.5 3-pin harness, XHP-3 both ends, 150 mm | 150 × 8.9 × 4.5 | none | J_LED | 30-100 |
| `CABLE_JSTXH_3P_200` | 07 | harness | JST-XH 2.5 3-pin harness, XHP-3 both ends, 200 mm | 200 × 8.9 × 4.5 | none | J_LED | 30-100 |
| `CABLE_JSTXH_3P_300` | 07 | harness | JST-XH 2.5 3-pin harness, XHP-3 both ends, 300 mm | 300 × 8.9 × 4.5 | none | J_LED | 30-100 |
| `STRAIN_TIE_NYLON66_100X2_5` | 08 | ties | Cable tie 100 x 2.5 mm (nylon 6/6) + printed tie anchors | 100 × 2.5 × 1 | none | none | 15-50 |
| `SCREW_M2X6_SHCS_A2` | 09 | screws | M2 x 6 mm socket head cap screw, SS304 (pack of 10) | 3.8 × 3.8 × 6 | none | none | 79.99-79.99 |
| `SCREW_M2_5X12_SHCS_A2` | 09 | screws | M2.5 x 12 mm socket head cap screw, SS304 (pack of 10) | 4.5 × 4.5 × 12 | none | none | 99.99-99.99 |
| `SCREW_M2_5X5_SHCS_A2` | 09 | screws | M2.5 x 5 mm socket head cap screw, SS304 (pack of 10) | 4.5 × 4.5 × 5 | none | none | 49.99-49.99 |
| `SCREW_M2_5X8_SHCS_A2` | 09 | screws | M2.5 x 8 mm socket head cap screw, SS304 (pack of 10) | 4.5 × 4.5 × 8 | none | none | 79.99-79.99 |
| `SCREW_SET_M2_M2_5_SHCS_A2` | 09 | screws | M2 / M2.5 socket head cap screws (SS304) + ISO 4032 hex nuts | 4.5 × 4.5 × 10.5 | none | none | 15-70 |
| `INSERT_HEATSET_M2_5X4_MEC8005` | 10 | inserts | Brass heat-set knurled insert M2.5, 3.5 mm OD x 4.0 mm (pack of 10) | 3.5 × 3.5 × 4 | none | none | 59.99-59.99 |
| `INSERT_HEATSET_M2_5X4_STD` | 10 | inserts | Heat-set insert M2.5 x 4.0 (standard) | 4.6 × 4.6 × 4 | none | none | 20-100 |
| `INSERT_HEATSET_M3X5_7_STD` | 10 | inserts | Heat-set insert M3 x 5.7 (standard) | 4.6 × 4.6 × 5.7 | none | none | 20-100 |
| `FEET_BUMPON_SJ5302_CLEAR` | 11 | feet | Adhesive bumper feet, hemispherical ~7.9 mm | 7.9 × 7.9 × 2.2 | none | none | 20-70 |
| `FEET_RUBBER_8X2_5_SUNROM_4436` | 11 | feet | Self-adhesive rubber feet, clear, 8 mm dia x 2.5 mm (Sunrom 4436) | 8 × 8 × 2.5 | none | none | 54.3-54.3 |
| `CORE_CARTRIDGE_FRAME_V0` | 12 | cartridge | Standard core cartridge frame (our printed part; constraints only) | ? × ? × ? | usb_port | none | 60-300 |
| `SPK_GASKET_FOAM_25X3_ADH` | 13 | gasket | Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring) | 10000 × 25 × 3 | grille | none | 30-120 |
| `OLED_096_SSD1306_I2C` | 14 | display | 0.96 in 128x64 mono OLED, SSD1306, I2C 4-pin | 27.3 × 27.3 × 9.87 | display_window | J_I2C1/J_I2C2/J_I2C3 | 130-350 |
| `OLED_130_SH1106_I2C` | 15 | display | 1.3 in 128x64 mono OLED, SH1106, I2C 4-pin | 35.4 × 33.5 × 11.3 | display_window | J_I2C1/J_I2C2/J_I2C3 | 220-550 |
| `LCD_200_ST7789_240X320_WAVESHARE` | 16 | display | 2.0 in IPS colour LCD 240x320, ST7789V, SPI, PH2.0 8-pin | 58 × 35 × 10 | display_window | J_DISP | 350-950 |
| `LED_WS2812B_1PX_MODULE` | 17 | led | Addressable RGB LED, single WS2812B 5050 on a mini board | 10 × 10 × 3.2 | led_window | J_LED | 25-100 |
| `LED_RING_12_WS2812_50MM` | 18 | led | Addressable RGB LED ring, 12 x WS2812 5050, 50 mm OD | 50 × 50 × 3.2 | led_window | J_LED | 100-300 |
| `DIFFUSER_PRINTED_NATURAL_PETG` | 19 | diffuser | LED diffuser: printed natural PETG wall (alt: opal acrylic sheet) | ? × ? × ? | led_window | none | 30-150 |
| `MIC_INMP441_I2S` | 20 | mic | INMP441 I2S MEMS microphone module | 14 × 14 × 3 | mic_port | J_MIC | 110-350 |
| `AMP_MAX98357A_I2S` | 21 | amp | MAX98357A I2S 3 W class-D mono amplifier breakout | 17.78 × 19.05 × 3 | none | A1 | 110-300 |
| `SPK_28MM_8R_2W` | 22 | speaker | Speaker 28 mm round with mounting ears, 8 ohm, 2 W | 37.6 × 28 × 5.2 | grille | J_SPK | 80-250 |
| `SPK_40MM_4R_3W` | 22 | speaker | Speaker 40 mm round, 4 ohm, 3 W | 40 × 40 × 17.5 | grille | J_SPK | 80-250 |
| `BUZZER_PASSIVE_TRANSISTOR_MODULE` | 23 | buzzer | Passive buzzer module with transistor driver (low-level trigger) | 32 × 13 × 11 | grille | J_IO1/J_IO2/J_IO3 | 20-80 |
| `BUTTON_ARCADE_30MM_SNAPIN` | 24 | button | Big 30 mm snap-in arcade push button (microswitch) | 33 × 33 × 38 | button_hole | J_IO1/J_IO2/J_IO3 | 15-70 |
| `BUTTON_TACTILE_KY004` | 24 | button | Tactile push-button module (KY-004 type) | 18 × 15 × 6 | button_hole | J_IO1/J_IO2/J_IO3 | 15-70 |
| `ENCODER_KY040_PUSH` | 25 | encoder | Rotary encoder module with push switch (KY-040) | 26 × 20 × 30 | button_hole | not on v1 | 80-220 |
| `TOUCH_TTP223_SMALL_15X11` | 26 | touch | Capacitive touch module TTP223 (small 15 x 11 mm) | 15 × 11 × 3 | touch_zone | J_IO1/J_IO2/J_IO3 | 50-160 |
| `LIGHT_BH1750_GY302_I2C` | 27 | light_sensor | Ambient light sensor BH1750 (GY-302 I2C module) | 18.5 × 13.5 × 2.3 | led_window | J_I2C1/J_I2C2/J_I2C3 | 30-130 |
| `LIGHT_LDR_LM393_4PIN` | 27 | light_sensor | LDR light module, LM393 comparator, analog + digital out (alternative) | 30 × 14 × 8 | led_window | J_IO1/J_IO2/J_IO3 | 30-130 |
| `TEMPHUM_AHT20_ADAFRUIT_4566` | 28 | temp_humidity | Temperature/humidity sensor AHT20 (Adafruit 4566, STEMMA QT) | 25.4 × 17.78 × 4.6 | vent | J_I2C1/J_I2C2/J_I2C3 | 100-350 |
| `BARO_BMP280_GY_3V3_I2C` | 29 | pressure | Barometric pressure sensor BMP280 (GY-BMP280-3.3) | 15 × 11.5 × 2.5 | vent | J_I2C1/J_I2C2/J_I2C3 | 140-350 |
| `TOF_VL53L0X_POLOLU_2490` | 30 | distance | Time-of-flight distance sensor VL53L0X (Pololu 2490 carrier) | 12.7 × 17.8 × 2.2 | led_window | J_I2C1/J_I2C2/J_I2C3 | 160-500 |
| `PIR_AM312_MINI` | 31 | motion | Mini PIR motion sensor AM312 | 27 × 12 × 12 | led_window | J_IO1/J_IO2/J_IO3 | 60-180 |
| `PIR_HCSR501_STD` | 31 | motion | PIR motion sensor HC-SR501 (larger, adjustable; alternative) | 32 × 24 × 25 | led_window | not on v1 | 60-180 |
| `IMU_MPU6050_GY521` | 32 | imu | 6-axis IMU MPU-6050 (GY-521 module) | 21 × 16 × 3 | none | J_I2C1/J_I2C2/J_I2C3 | 90-300 |
| `HALL_KY003_A3144` | 33 | hall | Hall-effect magnet sensor module (KY-003, A3144) | 20 × 15 × 6 | none | J_IO1/J_IO2/J_IO3 | 25-130 |
| `RTC_DS3231_ZS042` | 36 | rtc | Real-time clock DS3231 + AT24C32 (ZS-042 type) | 38 × 22 × 14 | none | J_I2C1/J_I2C2/J_I2C3 | 90-260 |
| `MICROSD_SPI_MODULE_5V` | 37 | storage | microSD SPI module with LDO and level shifter (Catalex-type) | 42 × 24 × 12 | none | S1 | 41-49 |
| `STORAGE_MICROSD_32GB_A1` | 37b | storage_card | microSD card 32 GB, SDHC Class 10 A1 (SanDisk), FAT32 | 11 × 15 × 1 | none | card_slot | 1499.0-1549.0 |
| `SERVO_SG90_TOWERPRO` | 38 | servo | Micro servo TowerPro SG90 (9 g) | 32.2 × 12.2 × 31 | none | J_LED | 100-250 |
| `GEARMOTOR_N20_6V_LOWRPM` | 41 | motor | N20 micro metal gearmotor, 6 V, low RPM (12 x 10 mm) | 25.6 × 12 × 10 | none | not on v1 | 180-500 |
| `MOTOR_DRV_DRV8833_POLOLU_2130` | 42 | motor_driver | Dual H-bridge motor driver DRV8833 (Pololu 2130 carrier form factor) | 12.7 × 20.3 × 3.4 | none | not on v1 | 100-300 |
| `ENDSTOP_OMRON_SS01GL` | 43 | endstop | End-stop micro switch, hinge lever, micro-load (Omron SS-01GL footprint) | 19.8 × 6.4 × 10.2 | none | J_IO1/J_IO2/J_IO3 | 30-100 |
| `FAN_3010_5V_GUARDED` | 44 | fan | Mini fan 30 x 30 x 10 mm, 5 V (with printed guard) | 30 × 30 × 10 | grille | not on v1 | 120-350 |
| `SERVO_BRACKET_SG90_ACRYLIC` | 45 | servo_bracket | SG90/MG90 servo mounting bracket (reference) -> printed equivalent | 37 × 35 × 3 | none | none | 30-180 |
| `SERVO_HORN_KIT_SG90_SUPPLIED` | 46 | servo_horn | Servo horn set supplied with SG90 + printed linkage | ? × ? × ? | none | none | 30-150 |
| `MAGNET_NDFEB_D8X5_N35` | 47 | magnet | NdFeB disc magnet 8 x 5 mm, N35, Ni-Cu-Ni (captive in shell) | 8 × 8 × 5 | none | none | 20-100 |
| `SHELL_PANEL_FASTENED_V0` | 48 | panel | Standard fastened removable shell panel (our printed part; constraints only) | ? × ? × ? | none | none | 30-150 |
| `TEST_JIG_TAGCONNECT_TC2030_NL` | 49 | test_interface | Factory test/programming interface: Tag-Connect TC2030-NL footprint (+ 2.54 mm pogo alternative) | 5.08 × 2.03 × 0 | none | none | 20-110 |
| `LABEL_QR_POLYESTER_50X25_PACK` | 50 | label | Device ID label (QR + serial, silver/white polyester, thermal transfer) + packing kit | 50 × 25 × 0.1 | none | none | 30-150 |
| `SERVO_SG90_ARM` | P-motion-1 | servo | Micro servo TowerPro SG90 with a printed arm or flap | 32.2 × 12.2 × 31 | shaft_hole | J_LED | 100-250 |
| `SERVO_MG90S_ARM` | P-motion-2 | servo | Micro servo MG90S (metal gear) with a printed arm or flap | 32.2 × 12.5 × 31 | shaft_hole | J_LED | 120-300 |
| `SOIL_CAP_V12_ANALOG` | P-sensing-1 | soil_moisture | Capacitive soil moisture probe v1.2 (analog out) | 98 × 23 × 4 | cable_exit | J_IO1/J_IO2/J_IO3 | 55-130 |
| `FLOAT_SWITCH_VERTICAL_PP` | P-sensing-2 | level_switch | Vertical float level switch (PP, dry reed contact) | ? × ? × ? | cable_exit | J_IO1/J_IO2/J_IO3 | 100-200 |

## Schema (merged)

Every object has the library agents' keys (`id, catalog_row, catalog_id, role, name, reference_product, dims_mm, pcb_thickness_mm, mount, pins, module_connector, backplane_harness, aperture, electrical, firmware, kicad, sourcing_in, price_inr_planning, evidence, uncertain`) plus:

- `role`: canonical role (vocabulary in `tools/merge_modules.py`); the agents' coarse role is kept in `category`.
- `aliases`: former ids that `Library.get()` still resolves.
- `ref_prefix`: instance ref prefix (DISP, SPK, MIC, BTN, LED, AMP, SD, U, BP, ...).
- `face_mm` [w, h], `depth_mm`, `harness_clearance_mm`: mounting envelope used by the fit rules.
- `backplane_v1`: `{port, slots[], cable, supported, why?}`.
- `electrical.supply_rail`, `electrical.i2c_addr[]`, `electrical.i2c_addr_alt[]`, `electrical.current_basis` (`estimate` when the module's `uncertain` mentions current).
- `aperture.feature_allowed`, `aperture.required`.
- `requires_soldering`: does the maker (the person following BUILD_GUIDE) solder? true/false/null = unknown, plus `requires_soldering_basis`. Factory work is separate: `prewire` {by, lead, pads, slot, note} and `factory_soldering`. `sourcing_in[].variant`/`same_part` flag pre-wired listings that are a different physical part. `sourcing_in[].gst` (`incl`/`excl`/null, parsed from the row's notes) and `sourcing_in[].pack_size` (parsed, else null).
- `backplane_harness_library`: the library agent's original harness proposal when backplane-v1 differs.

## Connector policy (backplane-v1) and harness rows

Ports are keyed JST-PH 2.0 (3.3 V) except `J_LED`, which is JST-XH 2.5 (B3B-XH-A, 5 V supply and 5 V data through the 74AHCT1G125 buffer, silkscreen 'LED 5V'; REVIEW-1 MAJ-3). Rule checked by `tools/gen_backplane_v1.py`: two ports with the same connector family and pin count carry the same supply, so no lead fits a port at the wrong voltage.

Every design harness row (`design_harness: true`, generated here from the backplane ports and the modules on them) names ONE module-end family (`jst_ph`, `jst_xh`, `dupont_2.54` or `spade_2.8`) and its position table (`module_end.housings[].positions[]`: port pin, module pin, wire colour), plus `sku`, `drawing` and a `factory_make` entry (made by our factory to the drawing; no retail listing exists for these exact double-ended leads). Rows:

| id pattern | module end | used by |
|---|---|---|
| `CABLE_JSTPH_<n>P_<len>` | PHR-n, same order | J_SPK speaker lead (factory pre-wired), J_DISP Waveshare LCD |
| `CABLE_JSTXH_3P_<len>` | XHP-3, same order | J_LED leads (factory pre-wired to the LED pads) |
| `CABLE_JSTPH_{3,4}P_DUPONT1X{3,4}_<len>` | Dupont 1xn, straight-through | modules whose header order equals the port |
| `..._DUPONT1X<k>_<len>_X<order>` | Dupont 1xk, made to drawing | header modules whose order differs (e.g. X2143 = OLED VCC,GND,SCL,SDA) |
| `CABLE_JSTPH_6P_DUPONT2X1X3_<len>` | 2 x Dupont 1x3, made to drawing | INMP441 (L/R,WS,SCK + SD,VDD,GND) |
| `CABLE_JSTPH_3P_SPADE28_<len>` | 2 x 2.8 mm female spade | arcade button microswitch COM/NO |

len = 100/150/200/300 mm. `CABLE_JSTPH_{3,4}P_300MM` are single-ended Evelta pigtails kept as factory stock (`design_harness: false`). `gstudio.electrical.select_cable` picks the row whose position table matches the module.
| 02a | 2 x 1x22, 1x7, 1x6, 2 x 1x1 female 2.54 sockets | DevKitC, amp and microSD sockets on the backplane |

## Rules worth knowing

- I2C clash: `IMU_MPU6050_GY521` and `RTC_DS3231_ZS042` both default to 0x68. Normalize rejects the pair unless the IMU is strapped to 0x69 (not modelled yet).
- `RTC_DS3231_ZS042` carries a coin cell (blueprint: no batteries at launch). Normalize rejects it in child-directed designs and warns otherwise.
- `HALL_KY003_A3144` maps to a 3.3 V port only (its pull-up then stays at 3.3 V); switching at 3.3 V is not guaranteed.
- Not on backplane-v1: `ENCODER_KY040_PUSH`, `PIR_HCSR501_STD`, `GEARMOTOR_N20_6V_LOWRPM`, `MOTOR_DRV_DRV8833_POLOLU_2130`, `FAN_3010_5V_GUARDED`, `USBC_BREAKOUT_ADAFRUIT_4090`.
- `SERVO_SG90_TOWERPRO` uses `J_LED` (the only 5 V port), so a design has an addressable LED or a servo, not both.

## Merge report

- FEET_RUBBER_8X2_5_SUNROM_4436: missing keys ['pins', 'electrical'] (added empty)
- TEMPHUM_AHT20_ADAFRUIT_4566: module connector 'qwiic' has no harness family; electrical falls back to the base harness made to drawing
- PIR_AM312_MINI: module connector 'wire_leads' has no harness family; electrical falls back to the base harness made to drawing
- SERVO_SG90_TOWERPRO: module connector 'wire_leads' has no harness family; electrical falls back to the base harness made to drawing
- ENDSTOP_OMRON_SS01GL: module connector 'solder_pads' has no harness family; electrical falls back to the base harness made to drawing
- SERVO_SG90_ARM: module connector 'wire_leads' has no harness family; electrical falls back to the base harness made to drawing
- SERVO_MG90S_ARM: module connector 'wire_leads' has no harness family; electrical falls back to the base harness made to drawing
