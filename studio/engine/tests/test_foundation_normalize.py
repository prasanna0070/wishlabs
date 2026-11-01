"""Foundation: normalization (implied parts, refs, slots, harness, hardware) and fit rules."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _fixtures import good  # noqa: E402

from gstudio.errors import SpecError  # noqa: E402
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import ShellGeometry, normalize  # noqa: E402
from gstudio.spec import load_spec  # noqa: E402


@pytest.fixture(scope="module")
def lib():
    return Library()


def codes(rep):
    return {e.get("code") for e in rep["errors"]}


def test_good_spec_normalizes(lib):
    sp, rep = normalize(good(), lib)
    assert rep["ok"], rep["errors"]
    refs = {i.ref: i for i in sp.derived.instances}
    for r in ("U1", "BP1", "PWR1", "BTN1", "LED1", "SPK1", "MIC1", "DISP1", "AMP1", "SD1", "CBL1", "CARD1", "PSU1"):
        assert r in refs, r
    assert refs["CARD1"].required_by == "SD1" and refs["CARD1"].module == "STORAGE_MICROSD_32GB_A1"
    assert sp.electronics.power.adapter_in_box is True  # child-directed (REVIEW-1 MAJ-5 rule)
    assert refs["AMP1"].source == "implied" and refs["SD1"].source == "implied"
    assert refs["DISP1"].slot == "J_I2C1" and refs["AMP1"].slot == "A1" and refs["SD1"].slot == "S1"
    assert refs["SPK1"].slot == "J_SPK" and refs["LED1"].slot == "J_LED" and refs["MIC1"].slot == "J_MIC"
    assert "LOCAL_VOICE_CAPTURE" in sp.safety.flags
    for h in sp.derived.harness:
        assert h.length_mm in (100, 150, 200, 300)
        assert h.pins in (2, 3, 4, 6, 8)
        assert lib.has(h.module) and h.match == "exact" and h.sku and lib.get(h.module)["length_mm"] == h.length_mm
        assert h.module_end in ("jst_ph", "jst_xh", "dupont_2.54", "spade_2.8", "solder", "factory_prewired")
    mods = {h.ref: h.module for h in sp.derived.harness}
    assert mods["DISP1"].startswith("CABLE_JSTPH_4P_DUPONT1X4_") and mods["DISP1"].endswith("_X2143")  # VCC,GND,SCL,SDA
    assert mods["BTN1"].startswith("CABLE_JSTPH_3P_SPADE28_") and mods["MIC1"].startswith("CABLE_JSTPH_6P_DUPONT2X1X3_")
    assert mods["LED1"].startswith("CABLE_JSTXH_3P_") and mods["SPK1"].startswith("CABLE_JSTPH_2P_")
    ends = {h.ref: h.module_end for h in sp.derived.harness}
    assert ends["DISP1"] == "dupont_2.54" and ends["BTN1"] == "spade_2.8"
    assert ends["SPK1"] == "factory_prewired" and ends["LED1"] == "factory_prewired"
    hw = {h.name: h.qty for h in sp.derived.hardware}
    assert hw["adhesive bumper foot"] == 4
    # same rules as the CAD stage: 4 cartridge + 4 cover + speaker ring 3 + display 2 + LED 2 + mic 2 (+ arcade button 0)
    assert hw["M2.5 heat-set insert"] == 17
    assert sum(h.qty for h in sp.derived.hardware if str(h.size).startswith("M2.5x") and "screw" in h.name) == 17
    assert {h.group for h in sp.derived.hardware} >= {"CartridgeScrews", "CoverScrews", "ModuleScrews", "HeatSetInserts"}
    assert all(h.qty_basis == "planned" for h in sp.derived.hardware)
    m2 = [h for h in sp.derived.hardware if str(h.size).startswith("M2x")]
    assert [h.group for h in m2] == ["SupportPostScrews"] and m2[0].qty == 2
    c = sp.derived.cartridge
    assert c is not None and c.board_top_z == pytest.approx(c.floor_z + 8.0)
    # cartridge keep-out lies inside the inner shell
    g = ShellGeometry(sp)
    x0, x1, y0, y1, z0, z1 = c.keepout_box
    for x in (x0, x1):
        for y in (y0, y1):
            for z in (z0 + 0.1, z1):
                assert g.inside(x, y, z, margin=0.0)
    # normalized spec still validates (derived round-trips)
    again = load_spec(sp.to_json_dict())
    assert again.derived is not None and len(again.derived.instances) == len(sp.derived.instances)


def test_normalize_is_fast(lib):
    import time

    t = time.time()
    normalize(good(), lib)
    assert time.time() - t < 2.0


def test_default_speaker_grille_added(lib):
    d = good()
    d["features"][2].pop("grille")
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    assert sp.feature("speaker").grille is not None
    assert any("grille" in a for a in rep["added"])


def test_template_requirements(lib):
    d = good()
    d["features"] = [f for f in d["features"] if f["id"] != "speaker"]
    _, rep = normalize(d, lib)
    assert "template_requirement" in codes(rep)
    assert any("speaker" in e["msg"] for e in rep["errors"])
    d = good()
    d["features"] = [f for f in d["features"] if f["id"] != "mic"]
    _, rep = normalize(d, lib)
    assert any(e["path"] == "behaviour.settings.capture_mode" for e in rep["errors"])


def test_unknown_module_suggests(lib):
    d = good()
    d["features"][4]["module"] = "OLED_096"
    _, rep = normalize(d, lib)
    e = next(e for e in rep["errors"] if e.get("code") == "unknown_module")
    assert "OLED_096_SSD1306_I2C" in e.get("fix", "")


def test_internal_module_as_feature_rejected(lib):
    d = good()
    d["features"].append({"id": "amp", "module": "AMP_MAX98357A_I2S", "face": "front", "at": [0, 60]})
    _, rep = normalize(d, lib)
    assert any("implied" in e["msg"] for e in rep["errors"])


def test_unsupported_module(lib):
    d = good()
    d["features"].append({"id": "knob", "module": "ENCODER_KY040_PUSH", "face": "front", "at": [50, 60]})
    _, rep = normalize(d, lib)
    assert "unsupported_module" in codes(rep)


def test_small_body_fails_size_and_cartridge(lib):
    d = good()
    d["body"]["outline"] = [[-25, 0], [25, 0], [30, 30], [0, 50], [-30, 30]]
    d["body"]["depth"] = 50
    d["features"] = []
    d["decor"] = []
    d["behaviour"]["template"] = "ambient_light"
    _, rep = normalize(d, lib)
    assert "size" in codes(rep)
    d["body"]["outline"] = [[-40, 0], [40, 0], [45, 40], [0, 70], [-45, 40]]
    d["body"]["depth"] = 70
    _, rep = normalize(d, lib)
    assert "cartridge_fit" in codes(rep)
    e = next(e for e in rep["errors"] if e.get("code") == "cartridge_fit")
    assert "wide" in e["fix"] and "deep" in e["fix"]


def test_bed_fit(lib):
    d = good()
    d["body"]["outline"] = [[-125, 0], [125, 0], [125, 230], [-125, 230]]
    d["body"]["depth"] = 210
    _, rep = normalize(d, lib)
    assert "bed" in codes(rep)


def test_feature_off_body_and_overlap(lib):
    d = good()
    d["features"][0]["at"] = [120, 82]
    _, rep = normalize(d, lib)
    assert codes(rep) & {"feature_miss", "feature_edge"}
    d = good()
    d["features"][1]["at"] = [-14, 82]  # LED window inside the button opening margin
    _, rep = normalize(d, lib)
    assert "feature_overlap" in codes(rep)


def test_feature_collides_with_cartridge(lib):
    d = good()
    d["features"][0]["at"] = [0, 22]  # arcade button (38 mm deep) low on the front, speaker moved away
    d["features"][2]["at"] = [0, 60]
    d["features"][2]["grille"] = {"pattern": "hex_holes", "w": 30, "h": 20, "hole": 2.6, "pitch": 4.4}
    d["features"][1]["at"] = [-40, 110]
    d["features"][3]["at"] = [40, 110]
    _, rep = normalize(d, lib)
    assert codes(rep) & {"feature_cartridge", "feature_depth", "feature_overlap"}


def test_decor_overlap(lib):
    d = good()
    d["decor"].append({"type": "mouth", "style": "smile", "at": [0, 84], "size": [20, 7], "relief": "engrave", "depth": 0.8})
    _, rep = normalize(d, lib)
    assert "decor_overlap" in codes(rep)


def test_i2c_clash_and_coin_cell(lib):
    d = good()
    d["electronics"]["extra_modules"] = ["IMU_MPU6050_GY521", "RTC_DS3231_ZS042"]
    _, rep = normalize(d, lib)
    assert "i2c_clash" in codes(rep)
    assert "child_safety" in codes(rep)  # coin cell in a child-directed design
    d["audience"]["child_directed"] = False
    d["safety"]["flags"] = ["CHILD_VOICE_STORED"]  # keeps the fixture's voice-clip consent (firmware consent gate, MAJ-1)
    d["electronics"]["extra_modules"] = ["RTC_DS3231_ZS042"]
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    assert any("coin cell" in w for w in rep["warnings"])


def test_port_capacity(lib):
    d = good()
    d["electronics"]["extra_modules"] = ["TEMPHUM_AHT20_ADAFRUIT_4566", "BARO_BMP280_GY_3V3_I2C", "IMU_MPU6050_GY521"]
    _, rep = normalize(d, lib)  # OLED + 3 I2C sensors = 4 devices on 3 I2C ports
    assert "slot_full" in codes(rep)


def test_raise_on_error(lib):
    d = good()
    d["features"] = []
    with pytest.raises(SpecError):
        normalize(d, lib, raise_on_error=True)


def test_revolve_and_blob_bodies(lib):
    d = good()
    d["body"] = {"family": "revolve", "profile": [[0, 0], [65, 0], [70, 30], [66, 80], [48, 130], [0, 150]], "squash_y": 1.0, "wall": 2.4}
    d["decor"] = []
    d["features"] = [
        {"id": "story_button", "module": "BUTTON_TACTILE_KY004", "face": "top", "at": [0, -20]},
        {"id": "speaker", "module": "SPK_28MM_8R_2W", "face": "front", "at": [0, 60]},
    ]
    d["behaviour"]["settings"] = {"capture_mode": "parent_web"}
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    assert sp.derived.cartridge is not None
    d["body"] = {"family": "blob", "primitives": [
        {"kind": "rounded_box", "center": [0, 0, 50], "size": [140, 120, 100], "radius": 6},
        {"kind": "sphere", "center": [0, 0, 120], "size": [90, 90, 90]}]}
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]


def test_desk_companion_lcd_and_touch(lib):
    d = good()
    d["audience"] = {"child_directed": False}
    d["safety"] = {"flags": []}
    d["behaviour"] = {"template": "desk_companion", "settings": {}}
    d["features"] = [
        {"id": "face", "module": "LCD_200_ST7789_240X320_WAVESHARE", "face": "front", "at": [0, 120]},
        {"id": "pat", "module": "TOUCH_TTP223_SMALL_15X11", "face": "top", "at": [0, -10]},
        {"id": "chime", "module": "SPK_28MM_8R_2W", "face": "front", "at": [0, 60]},
    ]
    d["decor"] = []
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    refs = {i.ref: i for i in sp.derived.instances}
    assert refs["DISP1"].slot == "J_DISP"
    assert "SD1" not in refs  # desk companion without audio files: no microSD
    assert refs["TCH1"].slot.startswith("J_IO")


def test_rear_cover_seam_and_base_plate_floor(lib):
    sp, rep = normalize(good(), lib)  # silhouette, rear_cover, depth 120, edge_round 12
    c = sp.derived.cartridge
    seam = 120 / 2 - min(12, 6) - 1.5
    assert c.seam_y == pytest.approx(seam)
    assert c.board_origin[1] + 75.1 + 3.6 + 0.8 <= seam + 1e-6  # rear bosses stay forward of the cover seam
    assert c.floor_z == pytest.approx(2.4) and c.usb_c_tunnel_mm > 2.4
    d = good()
    d["body"]["split"] = "base_plate"
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    assert sp.derived.cartridge.floor_z == pytest.approx(3.5) and sp.derived.cartridge.board_top_z == pytest.approx(11.5)
    assert sp.derived.cartridge.seam_y is None


def test_blob_base_plate_opening_too_small(lib):
    d = good()
    d["decor"] = []
    d["features"] = [{"id": "story_button", "module": "BUTTON_TACTILE_KY004", "face": "top", "at": [0, -20]},
                     {"id": "speaker", "module": "SPK_28MM_8R_2W", "face": "front", "at": [0, 60]}]
    d["behaviour"]["settings"] = {"capture_mode": "parent_web"}
    d["body"] = {"family": "blob", "primitives": [{"kind": "rounded_box", "center": [0, 0, 50], "size": [140, 120, 100], "radius": 20}]}
    _, rep = normalize(d, lib)
    e = next(e for e in rep["errors"] if e.get("code") == "cartridge_fit")
    assert "base-plate opening" in e["fix"] or "rear_cover" in e["fix"]
