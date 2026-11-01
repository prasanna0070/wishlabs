"""QB-19: the firmware pin constants equal the wiring, because pins.h is generated from electrical.json."""
from __future__ import annotations

import json
import re

import pytest

from gstudio.errors import StageError
from gstudio.firmware import library_lookup
from gstudio.firmware.hw import bind, check_template, hardware_h, parse_pins_h, pins_h

from .conftest import example_specs, load_fixture, real_design

LOOKUP = library_lookup()


def _net_gpio(elec: dict) -> dict:
    return {k: v for k, v in elec["gpio"].items() if isinstance(v, int)}


def test_every_gpio_signal_is_in_pins_h_with_the_same_number(design):
    template, spec, elec = design
    defs = parse_pins_h(pins_h(bind(elec, spec, LOOKUP)))
    for sig, num in _net_gpio(elec).items():
        assert defs[f"GS_GPIO_{sig}"] == num, sig
    signal_defs = {k for k in defs if k.startswith("GS_GPIO_")}
    assert signal_defs == {f"GS_GPIO_{s}" for s in _net_gpio(elec)}  # nothing extra, nothing missing


def test_every_instance_pin_equals_its_net_gpio(design):
    template, spec, elec = design
    b = bind(elec, spec, LOOKUP)
    defs = parse_pins_h(pins_h(b))
    gpio = _net_gpio(elec)
    # Each per-instance number must be one that electrical.json assigns to that instance's slot.
    for inst in b.instances:
        nets = {c["net"] for c in elec["connections"] if c["from"].startswith(inst.ref + ".") and c.get("net") in gpio}
        bus_nums = set()
        for bus in elec["buses"]:
            slots = bus.get("slots") or []
            dev = next((d for d in bus.get("devices") or [] if d.get("ref") == inst.ref), None)
            if dev is not None:
                bus_nums |= {v for v in bus.values() if isinstance(v, int)}
                if isinstance(dev.get("cs"), int):
                    bus_nums.add(dev["cs"])
                continue
            if isinstance(slots, dict):
                if inst.slot in slots:
                    bus_nums.add(slots[inst.slot])
            elif inst.slot in slots:
                bus_nums |= {v for v in bus.values() if isinstance(v, int)}
                if isinstance(bus.get("cs"), dict) and inst.slot in bus["cs"]:
                    bus_nums.add(bus["cs"][inst.slot])
        allowed = {gpio[n] for n in nets} | bus_nums
        for pin, (num, _src, _via) in inst.pins.items():
            assert defs[f"GS_{inst.ref}_{pin}"] == num
            assert num in allowed, f"{inst.ref}.{pin}={num} not assigned to {inst.slot}"


def test_hardware_h_has_no_gpio_numbers(design):
    template, spec, elec = design
    text = hardware_h(bind(elec, spec, LOOKUP))
    for m in re.finditer(r"^#define\s+(GS_PIN_\w+)\s+(\S+)", text, re.M):
        name, val = m.group(1), m.group(2)
        assert val == "-1" or re.fullmatch(r"GS_[A-Z0-9_]+", val), f"{name} = {val} is not a pins.h symbol"
    for m in re.finditer(r"#define\s+GS_BUTTON_PINS\s+\{(.*)\}", text):
        assert all(re.fullmatch(r"\s*GS_[A-Z0-9_]+\s*", x) for x in m.group(1).split(","))


def test_pins_follow_the_wiring_when_it_changes():
    spec, elec = load_fixture("story_player")
    moved = json.loads(json.dumps(elec))
    moved["gpio"]["IO1_SIG"] = 47  # the electrical stage moves the button signal
    for b in moved["buses"]:
        if b["type"] == "gpio":
            b["slots"]["J_IO1"] = 47
    defs = parse_pins_h(pins_h(bind(moved, spec, LOOKUP)))
    assert defs["GS_GPIO_IO1_SIG"] == 47
    assert defs["GS_BTN1_SIG"] == 47


def test_conflicting_wiring_is_refused():
    spec, elec = load_fixture("story_player")
    bad = json.loads(json.dumps(elec))
    for b in bad["buses"]:
        if b["type"] == "gpio":
            b["slots"]["J_IO1"] = 21  # bus says 21, the connection's net says 4
    with pytest.raises(StageError) as e:
        bind(bad, spec, LOOKUP)
    assert "two different GPIOs" in e.value.message


def test_checks_stage_qb19_regex_agrees():
    """The pipeline's QB-19 check parses pins.h with its own regex; make sure it sees no mismatch."""
    spec, elec = load_fixture("story_player")
    text = pins_h(bind(elec, spec, LOOKUP))
    defs = {}
    for m in re.finditer(r"(?:#define\s+([A-Za-z_]\w*)\s+\(?(-?\d+)\)?|(?:constexpr|const|static)\s+[\w:<> ]*?\b([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;)", text):
        defs[(m.group(1) or m.group(3)).upper()] = int(m.group(2) or m.group(4))
    mism = []
    for sig, num in _net_gpio(elec).items():
        keys = [k for k in defs if k == sig.upper() or k.endswith("_" + sig.upper()) or k.endswith(sig.upper())]
        assert keys, sig
        mism += [k for k in keys if defs[k] != num]
    assert not mism


def test_reserved_pins_never_drive_a_module(design):
    template, spec, elec = design
    b = bind(elec, spec, LOOKUP)
    reserved = {0, 3, 45, 46, 19, 20, 43, 44, 35, 36, 37}
    for inst in b.instances:
        for pin, (num, _s, _v) in inst.pins.items():
            assert num not in reserved, f"{inst.ref}.{pin} on reserved GPIO {num}"


def test_story_player_requirements_are_actionable():
    spec, elec = load_fixture("story_player")
    no_mic = json.loads(json.dumps(elec))
    no_mic["instances"] = [i for i in no_mic["instances"] if i["ref"] != "MIC1"]
    b = bind(no_mic, spec, LOOKUP)
    with pytest.raises(StageError) as e:
        check_template("story_player", b, {"capture_mode": "child_voice_clip"})
    fixes = json.dumps(e.value.details)
    assert "parent_web" in fixes
    assert check_template("story_player", b, {"capture_mode": "parent_web"}) == []


@pytest.mark.parametrize("spec_path", example_specs(), ids=lambda p: p.parent.name)
def test_real_examples_pins_equal_the_real_electrical_stage(spec_path, tmp_path):
    ctx, elec = real_design(spec_path, tmp_path)
    spec = ctx.spec.model_dump(mode="json")
    b = bind(elec, spec, library_lookup(ctx.lib))
    defs = parse_pins_h(pins_h(b))
    for sig, num in _net_gpio(elec).items():
        assert defs[f"GS_GPIO_{re.sub(r'[^A-Za-z0-9]+', '_', sig).upper()}"] == num
    template = spec["behaviour"]["template"]
    from gstudio.firmware import validate_settings

    settings, errs = validate_settings(template, spec["behaviour"].get("settings") or {})
    assert not errs
    check_template(template, b, settings)  # raises if the example lacks hardware its behaviour needs
    hardware_h(b)  # every role the firmware drives has its GPIO
