"""Foundation: merged module library and backplane-v1 data."""

import json

import pytest

from gstudio.library import Library, UnknownModule


@pytest.fixture(scope="module")
def lib():
    return Library()


def test_library_loads_merged(lib):
    assert len(lib) >= 50
    ids = lib.ids()
    assert len(ids) == len(set(ids))
    for m in lib:
        assert isinstance(m.get("evidence"), list) and isinstance(m.get("uncertain"), list)
        assert m["role"] and "backplane_v1" in m


def test_get_alias_and_unknown(lib):
    assert lib.get("BACKPLANE_S3_V0_CUSTOM")["id"] == "BACKPLANE_V1"
    assert lib.get("oled_096_ssd1306_i2c")["id"] == "OLED_096_SSD1306_I2C"
    with pytest.raises(UnknownModule) as ei:
        lib.get("OLED_096_SSD")
    assert "OLED_096_SSD1306_I2C" in str(ei.value)


def test_roles_and_apertures(lib):
    assert len(lib.by_role("display")) >= 3
    ap = lib.aperture_modules()
    assert ap and all(m["aperture"]["feature_allowed"] and m["backplane_v1"]["supported"] for m in ap)
    assert not any(m["role"] in ("amp", "storage", "core") for m in ap)


def test_summary_for_llm(lib):
    t = lib.summary_for_llm()
    lines = t.splitlines()
    assert lines[0].startswith("id | role | name")
    assert any(l.startswith("CORE_ESP32S3_DEVKITC1_N8R8 |") for l in lines)
    assert not any(l.startswith("ENCODER_KY040_PUSH") for l in lines)  # unsupported on v1
    assert len(t) < 8000


def test_catalog_mapping(lib):
    assert lib.catalog_id_for_price("CORE_ESP32S3_DEVKITC1_N8R8") == "CORE_ESP32S3_A"
    assert lib.catalog_id_for_price("SPK_28MM_8R_2W") == "SPK_A"  # variant mapped by blueprint row
    assert lib.catalog_id_for_price("ENCODER_KY040_PUSH") is None  # row 25 not in the sim catalog


def test_i2c_addresses_parsed(lib):
    assert lib.get("RTC_DS3231_ZS042")["electrical"]["i2c_addr"] == ["0x68", "0x57"]
    assert lib.get("LIGHT_BH1750_GY302_I2C")["electrical"]["i2c_addr_alt"] == ["0x5C"]


def test_backplane_consistency(lib):
    bp = lib.backplane
    w, h = bp["board"]["size"]
    assert sorted([w, h], reverse=True) <= [95, 65] or (max(w, h) <= 95 and min(w, h) <= 65)
    assert bp["checks"]["layout_issues"] == []
    gp = [g for s, g in bp["gpio"].items() if s not in ("USB_DP", "USB_DN", "UART0_TX", "UART0_RX", "BOOT_IO0")]
    assert len(gp) == len(set(gp))
    reserved = {int(k) for k in bp["gpio_reserved"]}
    assert not set(gp) & reserved
    header_gpios = {p["gpio"] for p in lib.slot("U1")["pins"] if p["gpio"] is not None}
    assert set(gp) <= header_gpios
    for s in bp["slots"]:
        for p in s["pins"]:
            assert p.get("net") or p.get("kind") == "nc", (s["id"], p)
    ids = {s["id"] for s in bp["slots"]}
    assert {"U1", "A1", "S1", "J_PWR", "J_SPK", "J_I2C1", "J_I2C2", "J_DISP", "J_MIC", "J_IO1", "J_IO2", "J_IO3", "J_LED"} <= ids
    assert all("_XH_" not in p["footprint"] for p in bp["parts"])  # PH only
    assert len([p for p in bp["parts"] if p["role"] == "test_pad"]) == 7
    assert len(bp["board"]["holes"]) >= 4


def test_every_slot_signal_net_reaches_the_core(lib):
    bp = lib.backplane
    u1_nets = {p["net"] for p in lib.slot("U1")["pins"] if p["net"]}
    support_nets = {n for p in bp["parts"] for n in (p.get("pins") or {}).values()}
    for s in bp["slots"]:
        if s["id"] in ("U1",):
            continue
        for p in s["pins"]:
            n = p.get("net")
            if not n:
                continue
            assert n in u1_nets or n in support_nets or n in ("SPK_P", "SPK_N"), (s["id"], n)


def test_readme_has_one_row_per_module(lib):
    txt = (lib.dir / "README.md").read_text()
    for m in lib:
        assert f"| `{m['id']}` |" in txt


def test_slot_and_module_footprints_agree(lib):
    usb = lib.get("USBC_RECEPTACLE_GCT_USB4105")
    assert usb["kicad"]["backplane_footprint"] == lib.slot("J_PWR")["footprint"]
    assert "GCT_USB4105" in lib.slot("J_PWR")["footprint"]


def test_harness_modules(lib):
    for n in (2, 3, 4, 6, 8):
        for L in (100, 150, 200, 300):
            m = lib.get(f"CABLE_JSTPH_{n}P_{L}")
            assert m["role"] == "harness" and m["price_inr_planning"] == [30, 100]
            assert m["backplane_v1"].get("proposed_row", False) == (n in (2, 6, 8))
    assert lib.catalog_id_for_price("CABLE_JSTPH_4P_150") == "CABLE_4PIN_A"


def test_prewire_and_maker_soldering(lib):
    for mid, n in (("SPK_40MM_4R_3W", 2), ("SPK_28MM_8R_2W", 2), ("LED_WS2812B_1PX_MODULE", 3), ("LED_RING_12_WS2812_50MM", 3)):
        m = lib.get(mid)
        assert m["requires_soldering"] is False and m["factory_soldering"] is True
        assert m["prewire"]["by"] == "factory" and m["prewire"]["lead"] == f"JST-PH 2.0 {n}p 100 mm"
    assert lib.get("AMP_MAX98357A_I2S")["requires_soldering"] is False
    pim = [r for r in lib.get("SPK_40MM_4R_3W")["sourcing_in"] if r.get("variant") == "prewired_jst_ph"]
    assert pim and pim[0]["same_part"] is False
