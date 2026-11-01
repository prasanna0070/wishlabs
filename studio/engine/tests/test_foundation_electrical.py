"""Foundation: electrical.json (gadget-electrical/1): nets, GPIO, buses, harness colours, power budget."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _fixtures import good  # noqa: E402

from gstudio.context import BuildContext  # noqa: E402
from gstudio.electrical import build_electrical, run  # noqa: E402
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import normalize  # noqa: E402


@pytest.fixture(scope="module")
def lib():
    return Library()


def electrical(d, lib):
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    return sp, build_electrical(sp, lib)


def test_schema_and_shape(lib):
    _, el = electrical(good(), lib)
    assert el["schema"] == "gadget-electrical/1" and el["backplane"] == "backplane-v1"
    assert el["core"]["ref"] == "U1"
    for k in ("instances", "slots_used", "gpio", "buses", "nets", "connections", "power"):
        assert k in el
    assert {"J_PWR", "U1", "A1", "S1", "J_SPK", "J_MIC", "J_LED", "J_IO1", "J_I2C1"} <= set(el["slots_used"])


def test_every_used_slot_pin_has_a_net(lib):
    _, el = electrical(good(), lib)
    nodes = {n: net["name"] for net in el["nets"] for n in net["nodes"]}
    for sid in el["slots_used"]:
        if sid == "U1":
            continue
        for p in lib.slot(sid)["pins"]:
            if p.get("kind") == "nc":
                continue
            node = f"BP1.{sid}.{p['pin']}"
            assert node in nodes, node
            assert nodes[node] == p["net"]


def test_nets_have_no_dangling_signal(lib):
    _, el = electrical(good(), lib)
    for net in el["nets"]:
        assert len(net["nodes"]) >= 2, net  # every net connects at least two things


def test_gpio_unique_and_legal(lib):
    _, el = electrical(good(), lib)
    bp = lib.backplane
    sys_sigs = {"UART0_TX", "UART0_RX", "BOOT_IO0", "USB_DP", "USB_DN"}
    vals = [g for s, g in el["gpio"].items() if s not in sys_sigs]
    assert len(vals) == len(set(vals))
    assert not set(vals) & {int(k) for k in bp["gpio_reserved"]}
    assert not set(vals) & {0, 3, 45, 46, 35, 36, 37, 19, 20, 43, 44}
    # only signals this design uses (no display here -> no DISP_*)
    assert not any(s.startswith("DISP_") for s in el["gpio"])
    assert el["gpio"]["I2C_SDA"] == 8 and el["gpio"]["I2C_SCL"] == 9


def test_wire_colours(lib):
    _, el = electrical(good(), lib)
    for c in el["connections"]:
        if c["kind"] != "cable":
            continue
        if c["net"] == "GND":
            assert c["color"] == "black"
        elif c["net"] == "+5V":
            assert c["color"] == "red"
        elif c["net"] == "+3V3":
            assert c["color"] == "orange"
        else:
            assert c["color"] not in ("black", "red", "orange"), c
    disp = next(i for i in el["instances"] if i["ref"] == "DISP1")
    pins = {w["module_pin"]: w for w in disp["harness"]["wires"]}
    assert pins["SDA"]["slot_pin"] == 3 and pins["SCL"]["slot_pin"] == 4  # mapped by name, not position
    assert pins["VCC"]["net"] == "+3V3"


def test_buses_and_no_i2c_clash(lib):
    _, el = electrical(good(), lib)
    types = {b["type"] for b in el["buses"]}
    assert {"i2c", "i2s_tx", "i2s_rx", "spi", "ws2812"} <= types
    i2c = next(b for b in el["buses"] if b["type"] == "i2c")
    addrs = [d["addr"] for d in i2c["devices"]]
    assert len(addrs) == len(set(addrs)) and i2c["clashes"] == []


def test_sensor_display_three_i2c(lib):
    d = good()
    d["audience"] = {"child_directed": False}
    d["safety"] = {"flags": []}
    d["behaviour"] = {"template": "sensor_display", "settings": {}}
    d["features"] = [{"id": "screen", "module": "OLED_130_SH1106_I2C", "face": "front", "at": [0, 120]},
                     {"id": "light", "module": "LIGHT_BH1750_GY302_I2C", "face": "top", "at": [0, -10]}]
    d["electronics"]["extra_modules"] = ["TEMPHUM_AHT20_ADAFRUIT_4566"]
    d["decor"] = []
    sp, el = electrical(d, lib)
    i2c = next(b for b in el["buses"] if b["type"] == "i2c")
    assert sorted(x["addr"] for x in i2c["devices"]) == ["0x23", "0x38", "0x3C"]
    assert not any(b["type"] == "spi" for b in el["buses"])


def test_power_budget(lib):
    _, el = electrical(good(), lib)
    p = el["power"]
    assert p["limit_ma"] == 1350
    assert p["total_ma_max"] >= p["total_ma_typ"] > 0
    assert all(r["basis"] for r in p["budget"])
    assert isinstance(p["ok"], bool)


def test_power_budget_flags_led_ring(lib):
    d = good()
    d["features"][1]["module"] = "LED_RING_12_WS2812_50MM"
    d["features"][1]["at"] = [0, 125]
    d["features"][4]["at"] = [0, 125]
    d["features"] = [f for f in d["features"] if f["id"] != "eyes"]
    d["decor"] = []
    _, el = electrical(d, lib)
    assert el["power"]["ok"] is False and el["power"]["firmware_caps_required"] is True
    assert "cap LED brightness" in el["power"]["notes"]


def test_run_writes_file(lib, tmp_path):
    ctx = BuildContext.prepare(good(), tmp_path, lib=lib)
    summary = run(ctx)
    data = json.loads((tmp_path / "electrical.json").read_text())
    assert data["schema"] == "gadget-electrical/1"
    assert ctx.electrical == data and summary["instances"] == len(data["instances"])
