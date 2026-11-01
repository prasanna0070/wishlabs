"""Library and backplane access for the dev fixture (read-only use of library/ and the PCB agent's fixture)."""
from __future__ import annotations

import json
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[4]          # .../studio
LIBRARY = STUDIO / "library"
ENGINE = STUDIO / "engine"
BACKPLANE_CANDIDATES = [LIBRARY / "backplane-v1.json",
                        ENGINE / "gstudio" / "pcb" / "tests" / "fixtures" / "backplane-v1.fixture.json"]


def load_modules() -> dict[str, dict]:
    mods: dict[str, dict] = {}
    files = [LIBRARY / "modules.json"] if (LIBRARY / "modules.json").exists() else []
    files += [LIBRARY / "modules-a.json", LIBRARY / "modules-b.json"]
    for f in files:
        if f.exists():
            for m in json.loads(f.read_text()):
                mods.setdefault(m["id"], m)
    return mods


def load_backplane() -> tuple[dict, str]:
    for p in BACKPLANE_CANDIDATES:
        if p.exists():
            return json.loads(p.read_text()), str(p.relative_to(STUDIO))
    raise FileNotFoundError("no backplane-v1 definition found")


class LibAdapter:
    """The small part of gstudio.library.Library that gstudio.order.bom and docs_gen use."""

    def __init__(self, mods: dict[str, dict]):
        self.mods = mods

    def get(self, mid: str) -> dict:
        if mid not in self.mods:
            raise KeyError(mid)
        return self.mods[mid]

    def catalog_id_for_price(self, mid: str):
        return (self.mods.get(mid) or {}).get("catalog_id")

    def by_role(self, role: str) -> list[dict]:
        return [m for m in self.mods.values() if m.get("role") == role]


# library ids used by this fixture (looked up by the scripted agent's role hints, then pinned here)
IDS = {
    "core": "CORE_ESP32S3_DEVKITC1_N8R8",
    "backplane": "BACKPLANE_S3_V0_CUSTOM",
    "usbc": "USBC_RECEPTACLE_GCT_USB4105",
    "button": "BUTTON_ARCADE_30MM_SNAPIN",
    "led": "LED_WS2812B_1PX_MODULE",
    "speaker": "SPK_40MM_4R_3W",
    "mic": "MIC_INMP441_I2S",
    "amp": "AMP_MAX98357A_I2S",
    "sd": "MICROSD_SPI_MODULE_5V",
    "cable3": "CABLE_JSTPH_3P_300MM",
    "screws": "SCREW_SET_M2_M2_5_SHCS_A2",
    "inserts": "INSERT_HEATSET_M2_5X4_STD",
    "feet": "FEET_BUMPON_SJ5302_CLEAR",
}


def dims(m: dict) -> tuple[float, float, float]:
    d = m.get("dims_mm") or {}
    return tuple(float(d.get(k) or 0.0) for k in ("x", "y", "z"))
