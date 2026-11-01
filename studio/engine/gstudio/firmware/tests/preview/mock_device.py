"""A stand-in for the device's web API, serving data/parent.html, so the parent page can be looked at in a
browser without hardware. The JSON shapes copy what main.cpp (api_state) and app_story_player.cpp return.

    python mock_device.py <port> <setup|ready>
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / "templates" / "common" / "data" / "parent.html").read_bytes()
MODE = sys.argv[2] if len(sys.argv) > 2 else "ready"

INFO = {"name": "Momo the Bedtime Robot", "slug": "momo-bedtime-robot", "template": "story_player",
        "firmware": "0.1.0+8c46ce84", "setup": MODE == "setup", "wifi_saved": MODE != "setup", "pin_set": MODE != "setup",
        "mode": "setup_hotspot" if MODE == "setup" else "home_wifi", "ip": "192.168.4.1" if MODE == "setup" else "192.168.1.42",
        "host": "momo-bedtime-robot.local", "child_directed": True}

STORIES = [("mango_tree", "The Mango Tree That Hummed"), ("monsoon_cloud", "The Sleepy Monsoon Cloud"),
           ("little_diya", "The Little Diya"), ("tea_garden", "The Tea Garden Elephant"),
           ("houseboat_moon", "The Houseboat and the Moon"), ("night_train", "The Night Train to Nani's Village")]

STATE = {
    "device": {"name": INFO["name"], "slug": INFO["slug"], "template": "story_player", "firmware": INFO["firmware"],
               "build": "8c46ce84881d", "uptime_s": 5400, "heap_free": 201344, "child_directed": True},
    "memory": {"status": "ok", "writable": True, "storage": "ok"},
    "network": {"mode": "home_wifi", "ssid": "Sharma Home", "ip": "192.168.1.42", "host": INFO["host"], "rssi": -58, "setup_hotspot": ""},
    "update": {"state": "valid", "partition": "app0", "signed_updates": False, "boot_checks": "ok"},
    "card": {"present": True, "mounted": True, "total_mb": 29802, "used_mb": 38},
    "app": {
        "capture_mode": "child_voice_clip", "clip_seconds": 2.0, "keep_window_seconds": 10,
        "player": {"state": "playing", "light": "story", "story_id": "mango_tree", "story_title": "The Mango Tree That Hummed",
                   "missing_audio_files": 0, "listening": False},
        "volume": 35, "volume_max": 60, "volume_note": "software setting, not a measured sound level",
        "night_light": {"enabled": True, "minutes": 20, "level": 12, "minutes_max": 120, "level_max": 60, "on": True, "remaining_s": 1020},
        "max_favourites": 12,
        "favourites": [{"id": 1, "name": "Anya", "has_clip": True, "clip_on_card": True, "name_audio": False},
                       {"id": 2, "name": "", "has_clip": True, "clip_on_card": True},
                       {"id": 3, "name": "Gattu the Elephant", "has_clip": False, "name_audio": False}],
        "story_pack": {"loaded": True, "message": "6 of 6 stories have audio.", "language": "en-IN", "count": 6},
        "stories": [{"id": i, "title": t, "audio": True} for i, t in STORIES],
        "privacy": "Voice clips are stored only on this device's microSD card and are never sent to the internet.",
    },
}


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/api/info":
            self._json(200, INFO)
        elif self.path == "/api/scan":
            self._json(200, {"ok": True, "networks": [{"ssid": "Sharma Home", "rssi": -50, "secure": True},
                                                       {"ssid": "JioFiber-5G", "rssi": -71, "secure": True}]})
        elif self.path == "/api/state":
            if self.headers.get("X-GS-PIN") != "2468":
                self._json(401, {"ok": False, "message": "Wrong parent PIN."})
            else:
                self._json(200, STATE)
        else:
            self._json(404, {"ok": False, "message": "Not found."})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._json(200, {"ok": True, "message": "Saved."})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
