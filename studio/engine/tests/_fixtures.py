"""Shared fixtures for foundation tests (not a test module)."""

import copy

GOOD = {
    "schema": "gadget-spec/1",
    "name": "Luna Owl",
    "slug": "luna-owl",
    "tagline": "A sleepy owl that tells bedtime stories",
    "audience": {"child_directed": True, "age_years": 5},
    "region": "IN",
    "colors": {"body": "#8CC9C0", "accent": "#F4F1EA", "detail": "#2B3A42", "body_name": "pale teal"},
    "body": {
        "family": "silhouette",
        "outline": [[-60, 0], [60, 0], [78, 40], [70, 120], [40, 170], [0, 182], [-40, 170], [-70, 120], [-78, 40]],
        "depth": 120, "edge_round": 12, "profile": None, "squash_y": 1.0, "primitives": None,
        "wall": 2.4, "split": "rear_cover", "feet": 4,
    },
    "decor": [
        {"type": "face_plate", "shape": "circle", "at": [0, 125], "size": [80, 80], "depth": 1.2},
        {"type": "eye", "style": "arc_closed", "at": [-30, 150], "size": [9, 9], "relief": "engrave", "depth": 0.8},
        {"type": "eye", "style": "arc_closed", "at": [30, 150], "size": [9, 9], "relief": "engrave", "depth": 0.8},
        {"type": "blush", "at": [-45, 120], "size": [8, 5], "relief": "emboss", "depth": 0.6, "side": "both"},
        {"type": "ear", "style": "owl_tuft", "side": "both", "at": [40, 172], "size": [26, 30]},
        {"type": "text", "text": "ANYA", "at": [0, 12], "height": 8, "relief": "engrave", "depth": 0.6},
    ],
    "features": [
        {"id": "story_button", "module": "BUTTON_ARCADE_30MM_SNAPIN", "face": "front", "at": [0, 82], "label": "Story button"},
        {"id": "night_light", "module": "LED_WS2812B_1PX_MODULE", "face": "front", "at": [-38, 82], "label": "Night light"},
        {"id": "speaker", "module": "SPK_40MM_4R_3W", "face": "front", "at": [0, 40],
         "grille": {"pattern": "hex_holes", "w": 36, "h": 30, "hole": 2.6, "pitch": 4.4}},
        {"id": "mic", "module": "MIC_INMP441_I2S", "face": "front", "at": [38, 82], "label": "Microphone hole"},
        {"id": "eyes", "module": "OLED_096_SSD1306_I2C", "face": "front", "at": [0, 125], "label": "Animated eyes"},
    ],
    "electronics": {"core": "CORE_ESP32S3_DEVKITC1_N8R8", "extra_modules": [], "power": {"input": "usb_c_5v", "adapter_in_box": False}},
    "behaviour": {"template": "story_player", "summary": "Tap: a story. Hold: say a new favourite.",
                  "settings": {"capture_mode": "child_voice_clip"}},
    "safety": {"flags": ["CHILD_DIRECTED"], "notes": "voice clips stay on the microSD"},
}


def good():
    return copy.deepcopy(GOOD)
