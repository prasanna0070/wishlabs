# Gadget Studio module library

`modules.json` is the merged library (foundation). Only these modules may appear in a design spec. Sources: `modules-a.json` (rows 01-07, 14-18, 20-24, 37) and `modules-b.json` (rows 08-13, 19, 25-33, 36, 38, 41-50); their `*-notes.md` explain every choice. Regenerate with `python3 -I tools/merge_modules.py` after either source changes, and the backplane with `python3 -I tools/gen_backplane_v1.py` (run that first).

Prices are blueprint §3.3 planning ranges (INR, often excluding GST and labour), not quotes. Every unverified number is listed in that module's `uncertain`. `v1` = how the module connects on `backplane-v1.json`.

| id | row | role | name | dims mm (x × y × z) | aperture | v1 slot | price ₹ |
|---|---|---|---|---|---|---|---|
| `CORE_ESP32S3_DEVKITC1_N8R8` | 01 | core | ESP32-S3-DevKitC-1 (WROOM-1 N8R8, Octal PSRAM) | 69.15 × 25.4 × 13.4 | none | U1 | 650-1300 |
| `BACKPLANE_V1` | 02 | backplane | Standard backplane PCB backplane-v1 (ours) | 62.8 × 78.6 × 17.6 | usb_port | backplane | 120-450 |
| `USBC_BREAKOUT_ADAFRUIT_4090` | 03 | power_in | USB-C downstream breakout with 5.1k CC resistors (fallback for USBC_POWER_A) | 20.32 × 13.97 × 5 | usb_port | not on v1 | 80-250 |
| `USBC_RECEPTACLE_GCT_USB4105` | 03 | power_in | USB-C 2.0 receptacle on backplane (5V sink, CC 5.1k Rd) | 8.94 × 7.35 × 3.2 | usb_port | J_PWR | 80-250 |
| `ADAPTER_RPI_15W_USBC_IN` | 04 | adapter | Raspberry Pi 15W USB-C power supply (5.1 V 3 A, India plug) | ? × ? × ? | none | in_box | 250-600 |
| `CABLE_USBC_C2C_1M` | 05 | usb_cable | USB-C to USB-C cable, 1 m, USB 2.0 | 1000 × ? × ? | none | in_box | 70-180 |
| `CABLE_JSTPH_4P_100` | 06 | harness | JST-PH 2.0 4-pin harness, 100 mm, pre-crimped both ends | 100 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_150` | 06 | harness | JST-PH 2.0 4-pin harness, 150 mm, pre-crimped both ends | 150 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_200` | 06 | harness | JST-PH 2.0 4-pin harness, 200 mm, pre-crimped both ends | 200 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_300` | 06 | harness | JST-PH 2.0 4-pin harness, 300 mm, pre-crimped both ends | 300 × 9.9 × 4.5 | none | J_I2C1/J_I2C2/J_I2C3 | 30-100 |
| `CABLE_JSTPH_4P_300MM` | 06 | harness | Keyed 4-pin JST-PH 2.0 mm cable, pre-crimped, 300 mm | 300 × 9.9 × 4.5 | none | harness | 30-100 |
| `CABLE_JSTPH_2P_100` | 06b | harness | JST-PH 2.0 2-pin harness, 100 mm, pre-crimped both ends | 100 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_150` | 06b | harness | JST-PH 2.0 2-pin harness, 150 mm, pre-crimped both ends | 150 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_200` | 06b | harness | JST-PH 2.0 2-pin harness, 200 mm, pre-crimped both ends | 200 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_2P_300` | 06b | harness | JST-PH 2.0 2-pin harness, 300 mm, pre-crimped both ends | 300 × 5.9 × 4.5 | none | J_SPK | 30-100 |
| `CABLE_JSTPH_6P_100` | 06c | harness | JST-PH 2.0 6-pin harness, 100 mm, pre-crimped both ends | 100 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_150` | 06c | harness | JST-PH 2.0 6-pin harness, 150 mm, pre-crimped both ends | 150 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_200` | 06c | harness | JST-PH 2.0 6-pin harness, 200 mm, pre-crimped both ends | 200 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_6P_300` | 06c | harness | JST-PH 2.0 6-pin harness, 300 mm, pre-crimped both ends | 300 × 13.9 × 4.5 | none | J_MIC | 30-100 |
| `CABLE_JSTPH_8P_100` | 06d | harness | JST-PH 2.0 8-pin harness, 100 mm, pre-crimped both ends | 100 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_150` | 06d | harness | JST-PH 2.0 8-pin harness, 150 mm, pre-crimped both ends | 150 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_200` | 06d | harness | JST-PH 2.0 8-pin harness, 200 mm, pre-crimped both ends | 200 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_8P_300` | 06d | harness | JST-PH 2.0 8-pin harness, 300 mm, pre-crimped both ends | 300 × 17.9 × 4.5 | none | J_DISP | 30-100 |
| `CABLE_JSTPH_3P_100` | 07 | harness | JST-PH 2.0 3-pin harness, 100 mm, pre-crimped both ends | 100 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3/J_LED | 30-100 |
| `CABLE_JSTPH_3P_150` | 07 | harness | JST-PH 2.0 3-pin harness, 150 mm, pre-crimped both ends | 150 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3/J_LED | 30-100 |
| `CABLE_JSTPH_3P_200` | 07 | harness | JST-PH 2.0 3-pin harness, 200 mm, pre-crimped both ends | 200 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3/J_LED | 30-100 |
| `CABLE_JSTPH_3P_300` | 07 | harness | JST-PH 2.0 3-pin harness, 300 mm, pre-crimped both ends | 300 × 7.9 × 4.5 | none | J_IO1/J_IO2/J_IO3/J_LED | 30-100 |
| `CABLE_JSTPH_3P_300MM` | 07 | harness | Keyed 3-pin JST-PH 2.0 mm cable, pre-crimped, 300 mm | 300 × 7.9 × 4.5 | none | harness | 30-100 |
| `STRAIN_TIE_NYLON66_100X2_5` | 08 | ties | Cable tie 100 x 2.5 mm (nylon 6/6) + printed tie anchors | 100 × 2.5 × 1 | none | none | 15-50 |
| `SCREW_SET_M2_M2_5_SHCS_A2` | 09 | screws | M2 / M2.5 socket head cap screws (SS304) + ISO 4032 hex nuts | 4.5 × 4.5 × 10.5 | none | none | 15-70 |
| `INSERT_HEATSET_M2_5X4_STD` | 10 | inserts | Heat-set insert M2.5 x 4.0 (standard) | 4.6 × 4.6 × 4 | none | none | 20-100 |
| `INSERT_HEATSET_M3X5_7_STD` | 10 | inserts | Heat-set insert M3 x 5.7 (standard) | 4.6 × 4.6 × 5.7 | none | none | 20-100 |
| `FEET_BUMPON_SJ5302_CLEAR` | 11 | feet | Adhesive bumper feet, hemispherical ~7.9 mm | 7.9 × 7.9 × 2.2 | none | none | 20-70 |
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
| `MICROSD_SPI_MODULE_5V` | 37 | storage | microSD SPI module with LDO and level shifter (Catalex-type) | 42 × 24 × 12 | none | S1 | 120-380 |
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

## Schema (merged)

Every object has the library agents' keys (`id, catalog_row, catalog_id, role, name, reference_product, dims_mm, pcb_thickness_mm, mount, pins, module_connector, backplane_harness, aperture, electrical, firmware, kicad, sourcing_in, price_inr_planning, evidence, uncertain`) plus:

- `role`: canonical role (vocabulary in `tools/merge_modules.py`); the agents' coarse role is kept in `category`.
- `aliases`: former ids that `Library.get()` still resolves.
- `ref_prefix`: instance ref prefix (DISP, SPK, MIC, BTN, LED, AMP, SD, U, BP, ...).
- `face_mm` [w, h], `depth_mm`, `harness_clearance_mm`: mounting envelope used by the fit rules.
- `backplane_v1`: `{port, slots[], cable, supported, why?}`.
- `electrical.supply_rail`, `electrical.i2c_addr[]`, `electrical.i2c_addr_alt[]`, `electrical.current_basis` (`estimate` when the module's `uncertain` mentions current).
- `aperture.feature_allowed`, `aperture.required`.
- `requires_soldering` (true/false/null = unknown) + `requires_soldering_basis`; `sourcing_in[].gst` (`incl`/`excl`/null, parsed from the row's notes) and `sourcing_in[].pack_size` (parsed, else null).
- `backplane_harness_library`: the library agent's original harness proposal when backplane-v1 differs.

## Connector policy (backplane-v1) and proposed catalog rows

One family, JST-PH 2.0 vertical, keyed. All PH ports carry 3.3 V except `J_LED` (5 V supply, 5 V data through the 74AHCT1G125 buffer), which is labelled 'LED 5V'. Every 3-pin IO module in this library accepts 3.3-5 V and its output would only meet the buffer, so a mis-plug into `J_LED` does not reach a GPIO.

Blueprint rows 06 (4-pin) and 07 (3-pin) cover I2C and IO cables. backplane-v1 also needs these cable rows (proposed, not yet in the blueprint catalog):

| proposed row | cable | used by |
|---|---|---|
| 06b | JST-PH 2-pin speaker lead, 100-300 mm | `J_SPK` (speaker) |
| 06c | JST-PH 6-pin to Dupont 1x6, 100-300 mm | `J_MIC` (INMP441) |
| 06d | JST-PH 8-pin to JST-PH 8-pin, same order, 100-300 mm | `J_DISP` (Waveshare 2in LCD) |
| 06e | JST-PH 3-pin to servo 3-way 2.54 plug adapter | `J_LED` when it drives a servo |
| 06f | JST-PH 5-pin / 6-pin | encoder / DRV8833: not on backplane-v1, kept for backplane-v2 |

Harness modules `CABLE_JSTPH_<n>P_<len>` (n = 2, 3, 4, 6, 8; len = 100/150/200/300 mm) are generated by `tools/merge_modules.py` and referenced by `spec.derived.harness[].module`. 3-pin and 4-pin map to rows 07/06; 2, 6 and 8-pin are proposed rows 06b/06c/06d with the 06/07 planning price (marked `backplane_v1.proposed_row`).
| 02a | 2 x 1x22, 1x7, 1x6, 2 x 1x1 female 2.54 sockets | DevKitC, amp and microSD sockets on the backplane |

## Rules worth knowing

- I2C clash: `IMU_MPU6050_GY521` and `RTC_DS3231_ZS042` both default to 0x68. Normalize rejects the pair unless the IMU is strapped to 0x69 (not modelled yet).
- `RTC_DS3231_ZS042` carries a coin cell (blueprint: no batteries at launch). Normalize rejects it in child-directed designs and warns otherwise.
- `HALL_KY003_A3144` maps to a 3.3 V port only (its pull-up then stays at 3.3 V); switching at 3.3 V is not guaranteed.
- Not on backplane-v1: `ENCODER_KY040_PUSH`, `PIR_HCSR501_STD`, `GEARMOTOR_N20_6V_LOWRPM`, `MOTOR_DRV_DRV8833_POLOLU_2130`, `FAN_3010_5V_GUARDED`, `USBC_BREAKOUT_ADAFRUIT_4090`.
- `SERVO_SG90_TOWERPRO` uses `J_LED` (the only 5 V port), so a design has an addressable LED or a servo, not both.
