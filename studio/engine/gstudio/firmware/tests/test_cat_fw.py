"""C-fw contract (CATALOG-PLAN package C step 0): the settings, config.h defines and hardware.h symbols that the motion
pack (servo arm, schedule) and the sensing pack (soil probe, float switch) code against."""
from __future__ import annotations

import copy
import re

import pytest

from gstudio.errors import StageError
from gstudio.firmware import generate, library_lookup, validate_settings
from gstudio.firmware.hw import bind, check_template, hardware_h, parse_pins_h, pins_h
from gstudio.firmware.project import config_h

from .conftest import load_fixture

LOOKUP = library_lookup()


def _defines(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^#define\s+(\w+)\s+(.*?)\s*(?://.*)?$", text, re.M)}


def _cfg(template: str, settings: dict) -> dict[str, str]:
    spec, elec = load_fixture(template)
    norm, errors = validate_settings(template, settings)
    assert errors == []
    return _defines(config_h(spec, template, norm, bind(elec, spec, LOOKUP), "0.0.0+test", "test", ""))


def _add_io(elec: dict, ref: str, module: str, slot: str, sig_pin: str, feature: str, pins=("GND", "3V3")) -> None:
    """Put one J_IO module on a free slot the way gstudio.electrical writes it (connections + gpio bus)."""
    n = slot[-1]
    elec["instances"].append({"ref": ref, "module": module, "feature": feature, "slot": slot,
                              "harness": {"cable": "JST-PH 3p", "length_mm": 150}})
    elec["connections"] += [
        {"from": f"{ref}.{sig_pin}", "to": f"BP1.{slot}.3", "via": f"{slot} harness", "net": f"IO{n}_SIG"}]
    for i, p in enumerate(pins, start=1):
        elec["connections"].append({"from": f"{ref}.{p}", "to": f"BP1.{slot}.{i}", "via": f"{slot} harness",
                                    "net": "GND" if p == "GND" else "+3V3"})


def _sensing_design(keep_i2c_sensors: bool = True):
    spec, elec = load_fixture("sensor_display")
    spec, elec = copy.deepcopy(spec), copy.deepcopy(elec)
    spec["features"] += [{"id": "soil", "module": "SOIL_CAP_V12_ANALOG", "label": "soil probe"},
                         {"id": "tank", "module": "FLOAT_SWITCH_VERTICAL_PP", "label": "tank float"}]
    _add_io(elec, "SOIL1", "SOIL_CAP_V12_ANALOG", "J_IO2", "AO", "soil", pins=("GND", "VCC"))
    _add_io(elec, "LVL1", "FLOAT_SWITCH_VERTICAL_PP", "J_IO3", "SIG", "tank", pins=("GND",))
    if not keep_i2c_sensors:
        elec["instances"] = [i for i in elec["instances"] if i["ref"] not in ("TH1", "BARO1")]
    return spec, elec


# ---- config.h


def test_desk_companion_servo_defines():
    d = _cfg("desk_companion", {"servo_waves": 3})
    assert (d["GS_CFG_SERVO_WAVE_ON_TAP"], d["GS_CFG_SERVO_REST_DEG"], d["GS_CFG_SERVO_WAVE_DEG"], d["GS_CFG_SERVO_WAVES"],
            d["GS_CFG_SERVO_SPEED_DEG_S"]) == ("1", "90", "35", "3", "120")


def test_notifier_clock_schedule_and_servo_defines():
    d = _cfg("notifier_clock", {"schedule": [{"time": "19:00", "text": "Dinner"}, {"time": "08:00", "text": "Breakfast"}],
                                "servo_on_notice": False, "servo_hold_s": 5})
    assert d["GS_CFG_SCHEDULE_N"] == "2"
    assert d["GS_CFG_SCHEDULE_MIN"] == "{ 480, 1140 }"
    assert d["GS_CFG_SCHEDULE_TEXT"] == '{ "Breakfast", "Dinner" }'
    assert (d["GS_CFG_SERVO_ON_NOTICE"], d["GS_CFG_SERVO_OPEN_DEG"], d["GS_CFG_SERVO_REST_DEG"], d["GS_CFG_SERVO_HOLD_S"],
            d["GS_CFG_SERVO_SPEED_DEG_S"]) == ("0", "90", "0", "5", "120")


def test_empty_schedule_still_compiles():
    d = _cfg("notifier_clock", {})
    assert d["GS_CFG_SCHEDULE_N"] == "0"
    assert d["GS_CFG_SCHEDULE_MIN"] == "{ 0 }" and d["GS_CFG_SCHEDULE_TEXT"] == '{ "" }'  # never a zero-size array


def test_schedule_text_is_a_safe_c_string():
    d = _cfg("notifier_clock", {"schedule": [{"time": "21:00", "text": 'Say "goodnight"?'}]})
    assert d["GS_CFG_SCHEDULE_TEXT"] == r'{ "Say \"goodnight\"\077" }'


def test_sensor_display_soil_defines():
    d = _cfg("sensor_display", {"soil_dry_percent": 25, "soil_raw_dry": 2900, "soil_raw_wet": 1200, "water_low_warning": False})
    assert (d["GS_CFG_SOIL_DRY_PERCENT"], d["GS_CFG_SOIL_RAW_DRY"], d["GS_CFG_SOIL_RAW_WET"], d["GS_CFG_WATER_LOW_WARNING"]) == (
        "25", "2900", "1200", "0")


def test_servo_defines_only_for_the_apps_that_drive_it():
    for t in ("story_player", "ambient_light", "sensor_display"):
        assert not any(k.startswith("GS_CFG_SERVO_") for k in _cfg(t, {})), t


# ---- hardware.h / pins.h


def test_soil_and_float_bind_to_their_io_slots():
    spec, elec = _sensing_design()
    b = bind(elec, spec, LOOKUP)
    assert not b.warnings, b.warnings
    pins = parse_pins_h(pins_h(b))
    assert pins["GS_SOIL1_SIG"] == 5 and pins["GS_LVL1_SIG"] == 6  # J_IO2 / J_IO3 = GPIO5 / GPIO6 (ADC1)
    hw = _defines(hardware_h(b))
    assert hw["GS_HAS_SOIL"] == "1" and hw["GS_PIN_SOIL"] == "GS_SOIL1_SIG"
    assert hw["GS_HAS_LEVEL"] == "1" and hw["GS_PIN_LEVEL"] == "GS_LVL1_SIG"
    assert check_template("sensor_display", b, validate_settings("sensor_display", {})[0]) == []


def test_no_soil_no_level_by_default(design):
    template, spec, elec = design
    hw = _defines(hardware_h(bind(elec, spec, LOOKUP)))
    assert hw["GS_HAS_SOIL"] == "0" and hw["GS_PIN_SOIL"] == "-1"
    assert hw["GS_HAS_LEVEL"] == "0" and hw["GS_PIN_LEVEL"] == "-1"


def test_sensor_display_runs_on_soil_alone():
    spec, elec = _sensing_design(keep_i2c_sensors=False)
    b = bind(elec, spec, LOOKUP)
    assert not b.sensors()
    check_template("sensor_display", b, validate_settings("sensor_display", {})[0])  # does not raise
    bare = copy.deepcopy(elec)
    bare["instances"] = [i for i in bare["instances"] if i["ref"] not in ("SOIL1", "LVL1")]
    with pytest.raises(StageError):
        check_template("sensor_display", bind(bare, spec, LOOKUP), validate_settings("sensor_display", {})[0])


def test_soil_on_another_app_is_flagged_not_silently_dropped():
    spec, elec = load_fixture("ambient_light")
    spec, elec = copy.deepcopy(spec), copy.deepcopy(elec)
    used = {i.get("slot") for i in elec["instances"]}
    slot = next(s for s in ("J_IO1", "J_IO2", "J_IO3") if s not in used)
    _add_io(elec, "SOIL1", "SOIL_CAP_V12_ANALOG", slot, "AO", "soil", pins=("GND", "VCC"))
    b = bind(elec, spec, LOOKUP)
    warns = check_template("ambient_light", b, validate_settings("ambient_light", {})[0])
    assert any("soil probe" in w and "sensor_display" in w for w in warns)


def _servo_design(module: str):
    """desk_companion with its J_LED light swapped for a servo, written the way gstudio.electrical writes a servo on
    J_LED: a "servo_pwm" bus carries the GPIO; the lead's own net (LED_DIN_5V, after the level shifter) has none."""
    spec, elec = load_fixture("desk_companion")
    spec, elec = copy.deepcopy(spec), copy.deepcopy(elec)
    gpio = elec["gpio"]["LED_DIN"]
    old = {i["ref"] for i in elec["instances"] if i.get("slot") == "J_LED"}
    elec["instances"] = [i for i in elec["instances"] if i["ref"] not in old]
    elec["connections"] = [c for c in elec["connections"] if c["from"].split(".")[0] not in old]
    elec["buses"] = [b for b in elec["buses"] if b.get("type") not in ("ws2812", "rmt_led")]
    elec["instances"].append({"ref": "SRV1", "module": module, "feature": "arm", "slot": "J_LED"})
    elec["connections"] += [{"from": "SRV1.GND", "to": "BP1.J_LED.1", "via": "J_LED harness", "net": "GND"},
                            {"from": "SRV1.5V", "to": "BP1.J_LED.2", "via": "J_LED harness", "net": "+5V"},
                            {"from": "SRV1.SIG", "to": "BP1.J_LED.3", "via": "J_LED harness", "net": "LED_DIN_5V"}]
    elec["buses"].append({"type": "servo_pwm", "gpio": gpio, "level_shift": "74AHCT1G125 to 5 V", "devices": [{"ref": "SRV1"}]})
    return spec, elec, gpio


@pytest.mark.parametrize("module", ["SERVO_SG90_ARM", "SERVO_MG90S_ARM", "SERVO_SG90_TOWERPRO"])
def test_servo_on_j_led_gets_its_pin_from_the_servo_bus(module):
    spec, elec, gpio = _servo_design(module)
    b = bind(elec, spec, LOOKUP)
    hw = _defines(hardware_h(b))
    assert hw["GS_HAS_SERVO"] == "1" and hw["GS_PIN_SERVO"] == "GS_SRV1_SIG"
    assert hw["GS_HAS_NEOPIXEL"] == "0"
    assert parse_pins_h(pins_h(b))["GS_SRV1_SIG"] == gpio


def test_generated_project_carries_the_contract(tmp_path):
    spec, elec = _sensing_design()
    generate(spec, elec, tmp_path)
    inc = tmp_path / "firmware" / "include"
    cfg, hw = _defines((inc / "config.h").read_text()), _defines((inc / "hardware.h").read_text())
    assert cfg["GS_CFG_SOIL_RAW_DRY"] == "3000" and hw["GS_HAS_SOIL"] == "1" and hw["GS_HAS_LEVEL"] == "1"


def test_setup_doc_explains_calibration_and_the_float():
    from gstudio.firmware.setup_doc import setup_markdown

    spec, _ = _sensing_design()
    settings = validate_settings("sensor_display", {"soil_raw_dry": 2900})[0]
    md = setup_markdown(spec, "sensor_display", settings, {"version": "t"}, None)
    assert "## 4. Soil probe and tank float" in md
    assert "2900" in md and "Water me" in md and "Refill the tank" in md and "does not water the plant" in md
    plain, _ = load_fixture("sensor_display")
    assert "Soil probe" not in setup_markdown(plain, "sensor_display", validate_settings("sensor_display", {})[0], {}, None)
