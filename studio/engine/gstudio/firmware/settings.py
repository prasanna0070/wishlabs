"""Behaviour settings per template (spec.behaviour.settings), validated with pydantic.

`validate_settings(template, settings) -> (normalized: dict | None, errors: list[str])`

Unknown keys are rejected and every number has bounds. Errors are short and actionable, prefixed
with the spec path, because the design agent reads them and retries. The defaults are child-safe:
a software volume cap of 60 of 100, a dim, warm night light capped at 30 % of its scale, no voice
recording (story_player capture_mode parent_web), and no Wi-Fi at all for a child-directed design
(network off).

Storing a child's voice (capture_mode child_voice_clip) is opt-in: it needs voice_clip_consent true,
which the design agent sets only when the brief records the parent's explicit consent, AND the
CHILD_VOICE_STORED safety flag. Callers that know the spec's safety flags pass them (the firmware stage
always does), and then both are enforced.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
NAME = re.compile(r"^[A-Za-z][A-Za-z '.\-]{0,31}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
POSIX_TZ = re.compile(r"^[A-Za-z<>+\-0-9:,./]{3,48}$")

TEMPLATES = ("story_player", "desk_companion", "notifier_clock", "ambient_light", "sensor_display")


def _clean_name(v: str) -> str:
    v = " ".join(str(v).split())
    if not NAME.match(v) or not any(c.isalpha() for c in v):
        raise ValueError("use 1 to 32 English letters, spaces, apostrophes, hyphens or full stops")
    return v


def _hex(v: str) -> str:
    if not isinstance(v, str) or not HEX.match(v):
        raise ValueError("use a colour like #FFB36B")
    return v.upper()


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Common(_S):
    volume_default: int = Field(35, ge=0, le=100, description="software volume at first boot (0..100 scale, not a sound level)")
    volume_max: int = Field(60, ge=10, le=100, description="hard cap the parent page cannot exceed; child-safe default 60")
    long_press_ms: int = Field(600, ge=400, le=2000, description="hold time that counts as a long press")
    led_max_percent: int = Field(45, ge=5, le=100, description="global LED brightness cap (keeps the 5 V budget)")
    network: Optional[Literal["off", "parent_page"]] = Field(
        None, description="off: no Wi-Fi radio at all; parent_page: local parent web page and JSON API (setup hotspot "
                          "only after a 5 s button hold at power-up). Default: off for a child-directed design")
    setup_hotspot_minutes: int = Field(15, ge=3, le=60, description="how long the Wi-Fi setup hotspot stays open")
    mic_gain_shift: int = Field(13, ge=10, le=16, description="INMP441 24-bit to 16-bit window; lower = louder")

    @model_validator(mode="after")
    def check_volume(self):
        if self.volume_default > self.volume_max:
            raise ValueError(f"volume_default ({self.volume_default}) must not exceed volume_max ({self.volume_max})")
        return self


class StoryPlayer(Common):
    capture_mode: Literal["child_voice_clip", "parent_web", "none"] = Field(
        "parent_web", description="how favourites are captured: names typed by a parent (default; no recording), the "
                                  "child's own voice clip (opt-in: needs voice_clip_consent and CHILD_VOICE_STORED), or none")
    voice_clip_consent: bool = Field(
        False, description="true only when the brief records the parent's explicit consent to storing the child's voice "
                           "clips on the card (encrypted, deleted on request or after clip_retention_days)")
    clip_seconds: float = Field(3.0, ge=1.0, le=4.0, description="voice clip length")
    keep_window_seconds: int = Field(10, ge=5, le=30, description="tap within this window after the playback to keep a clip")
    clip_retention_days: int = Field(90, ge=1, le=365,
                                     description="kept voice clips are deleted after this many days of the gadget being switched on")
    night_light: bool = True
    night_light_minutes: int = Field(20, ge=0, le=120)
    night_light_brightness: int = Field(12, ge=1, le=60, description="percent of LED full scale")
    night_light_max_percent: int = Field(30, ge=5, le=60,
                                         description="firmware cap on the night glow (percent of scale, before the global LED cap)")
    night_light_color: str = "#FFB36B"
    max_favourites: int = Field(12, ge=1, le=12)
    story_order: Literal["shuffle", "sequential"] = "shuffle"
    default_hero: str = Field("little star", description="who stars when no favourite is saved")
    default_friend: str = Field("little bunny", description="the friend in the story when only one favourite is saved")
    names_for_audio: list[str] = Field(default_factory=list, max_length=12,
                                       description="names to pre-record as story-pack name clips (parent_web)")
    voice: str = Field("Achernar", pattern=r"^[A-Za-z]{3,20}$", description="Gemini TTS voice for the story pack")
    language: Literal["en-IN"] = "en-IN"

    @field_validator("night_light_color")
    @classmethod
    def check_night_color(cls, v):
        return _hex(v)

    @field_validator("default_hero", "default_friend")
    @classmethod
    def check_default_names(cls, v):
        return _clean_name(v)

    @model_validator(mode="after")
    def check_night_cap(self):
        if self.night_light_brightness > self.night_light_max_percent:
            raise ValueError(f"night_light_brightness ({self.night_light_brightness}) must not exceed "
                             f"night_light_max_percent ({self.night_light_max_percent})")
        return self

    @field_validator("names_for_audio")
    @classmethod
    def check_names(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for n in v:
            n = _clean_name(n)
            if n.casefold() not in seen:
                seen.add(n.casefold())
                out.append(n)
        return out


SERVO_ONLY = "only with a servo arm: "


class ServoSettings(_S):
    """Servo fields shared by the apps that can drive the servo arm (motion pack). They are accepted and
    emitted into config.h for every design of those templates and are ignored when no servo is fitted."""
    servo_rest_deg: int = Field(90, ge=0, le=180, description=SERVO_ONLY + "angle the arm rests at, in degrees")
    servo_speed_deg_s: int = Field(120, ge=30, le=360, description=SERVO_ONLY + "top arm speed in degrees per second")


class DeskCompanion(Common, ServoSettings):
    sleep_after_minutes: int = Field(5, ge=1, le=60, description="idle time before the face looks sleepy")
    clap_detect: bool = Field(True, description="react to a clap (needs the microphone)")
    clap_threshold: int = Field(6000, ge=1000, le=30000)
    chime_on_tap: bool = True
    light_color: str = "#7FD3FF"
    light_brightness: int = Field(25, ge=0, le=100)
    servo_wave_on_tap: bool = Field(True, description=SERVO_ONLY + "wave the arm when tapped")
    servo_wave_deg: int = Field(35, ge=10, le=60, description=SERVO_ONLY + "how far the arm swings each way from rest, degrees")
    servo_waves: int = Field(2, ge=1, le=5, description=SERVO_ONLY + "number of waves per tap")

    @field_validator("light_color")
    @classmethod
    def check_light_color(cls, v):
        return _hex(v)


SCHEDULE_TEXT = re.compile(r"^[ -~]{1,24}$")  # printable ASCII: the display fonts have no other glyphs


class ScheduleItem(_S):
    time: str = Field(..., description="24-hour HH:MM, e.g. 09:00")
    text: str = Field(..., description="what the notice says, 1 to 24 characters, e.g. Morning pills")

    @field_validator("time")
    @classmethod
    def check_time(cls, v: str) -> str:
        if not isinstance(v, str) or not HHMM.match(v):
            raise ValueError("use 24-hour HH:MM, e.g. 09:00")
        return v

    @field_validator("text")
    @classmethod
    def check_text(cls, v: str) -> str:
        v = " ".join(str(v).split())
        if not SCHEDULE_TEXT.match(v) or not any(c.isalnum() for c in v):
            raise ValueError("use 1 to 24 plain English letters, digits, spaces or punctuation")
        return v


class NotifierClock(Common, ServoSettings):
    timezone: str = Field("IST-5:30", description="POSIX TZ string; India is IST-5:30")
    ntp_server: str = Field("in.pool.ntp.org", pattern=r"^[a-z0-9.\-]{3,64}$")
    clock_24h: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    notification_chime: bool = True
    ring_brightness: int = Field(30, ge=1, le=100)
    default_notice_minutes: int = Field(60, ge=1, le=1440)
    schedule: list[ScheduleItem] = Field(
        default_factory=list, max_length=8,
        description='daily notices the clock posts by itself at local time, up to 8, e.g. [{"time": "09:00", "text": '
                    '"Morning pills"}]; a reminder, never dosing or medical advice')
    servo_on_notice: bool = Field(True, description=SERVO_ONLY + "open the arm or flap when a scheduled notice is due")
    servo_rest_deg: int = Field(0, ge=0, le=180, description=SERVO_ONLY + "closed or resting angle, in degrees")
    servo_open_deg: int = Field(90, ge=0, le=180, description=SERVO_ONLY + "open angle, in degrees")
    servo_hold_s: int = Field(3, ge=1, le=30, description=SERVO_ONLY + "seconds the arm or flap stays open")

    @field_validator("schedule")
    @classmethod
    def check_schedule(cls, v: list) -> list:
        times = [it.time for it in v]
        dup = sorted({t for t in times if times.count(t) > 1})
        if dup:
            raise ValueError(f"two schedule entries at {', '.join(dup)}; use one entry per time")
        return sorted(v, key=lambda it: it.time)

    @field_validator("timezone")
    @classmethod
    def check_tz(cls, v: str) -> str:
        if not POSIX_TZ.match(v):
            raise ValueError("use a POSIX TZ string such as IST-5:30")
        return v

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def check_hhmm(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("use 24-hour HH:MM, e.g. 22:00")
        return v


class Scene(_S):
    name: str = Field(..., min_length=1, max_length=23, pattern=r"^[A-Za-z0-9 '\-]+$")
    color: str
    brightness: int = Field(60, ge=1, le=100)
    effect: Literal["solid", "breathe", "candle", "rainbow"] = "solid"
    @field_validator("color")
    @classmethod
    def check_color(cls, v):
        return _hex(v)


def _default_scenes() -> list[dict]:
    return [
        {"name": "Warm glow", "color": "#FFB36B", "brightness": 60, "effect": "solid"},
        {"name": "Diya", "color": "#FF9A3C", "brightness": 50, "effect": "candle"},
        {"name": "Calm", "color": "#6FA8FF", "brightness": 35, "effect": "breathe"},
        {"name": "Rainbow", "color": "#FFFFFF", "brightness": 40, "effect": "rainbow"},
    ]


class AmbientLight(Common):
    scenes: list[Scene] = Field(default_factory=lambda: [Scene(**s) for s in _default_scenes()], min_length=1, max_length=8)
    default_scene: int = Field(0, ge=0, le=7)
    auto_dim: bool = Field(True, description="dim in a dark room (needs a light sensor)")
    dark_lux: float = Field(5.0, ge=0.0, le=100.0)
    bright_lux: float = Field(300.0, ge=20.0, le=5000.0)
    off_after_minutes: int = Field(0, ge=0, le=480, description="0 = stay on")
    motion_wake: bool = Field(True, description="turn on when motion is seen (needs a PIR sensor)")

    @model_validator(mode="after")
    def check_scene_index(self):
        if self.default_scene >= len(self.scenes):
            raise ValueError(f"default_scene {self.default_scene} is past the last scene (0..{len(self.scenes) - 1})")
        if self.bright_lux <= self.dark_lux:
            raise ValueError("bright_lux must be greater than dark_lux")
        return self


class SensorDisplay(Common):
    temperature_unit: Literal["C", "F"] = "C"
    sample_seconds: int = Field(30, ge=5, le=600)
    trend_hours: int = Field(6, ge=1, le=24)
    temp_high: float = Field(30.0, ge=15.0, le=45.0)
    temp_low: float = Field(12.0, ge=0.0, le=25.0)
    humidity_high: float = Field(70.0, ge=40.0, le=95.0)
    humidity_low: float = Field(25.0, ge=10.0, le=50.0)
    led_warning: bool = True
    warn_color: str = "#FF5A36"
    soil_dry_percent: int = Field(30, ge=5, le=80, description="only with a soil probe: warn when the soil reads below "
                                                                "this percent (0 = the dry calibration, 100 = the wet one)")
    soil_raw_dry: int = Field(3000, ge=0, le=4095, description="only with a soil probe: 12-bit reading with the probe in "
                                                                "dry air (calibrate per probe)")
    soil_raw_wet: int = Field(1300, ge=0, le=4095, description="only with a soil probe: 12-bit reading with the probe in "
                                                                "water up to its line; must be below soil_raw_dry")
    water_low_warning: bool = Field(True, description="only with a float switch: warn when the tank is low")

    @field_validator("warn_color")
    @classmethod
    def check_warn_color(cls, v):
        return _hex(v)

    @model_validator(mode="after")
    def check_ranges(self):
        if self.temp_low >= self.temp_high:
            raise ValueError("temp_low must be below temp_high")
        if self.humidity_low >= self.humidity_high:
            raise ValueError("humidity_low must be below humidity_high")
        if self.soil_raw_wet >= self.soil_raw_dry:
            raise ValueError(f"soil_raw_wet ({self.soil_raw_wet}) must be below soil_raw_dry ({self.soil_raw_dry}): "
                             "a capacitive probe reads lower when wet")
        return self


MODELS: dict[str, type[Common]] = {
    "story_player": StoryPlayer,
    "desk_companion": DeskCompanion,
    "notifier_clock": NotifierClock,
    "ambient_light": AmbientLight,
    "sensor_display": SensorDisplay,
}


def _fmt(err: dict) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ()) if p != "__root__")
    msg = err.get("msg", "invalid")
    msg = msg.replace("Value error, ", "")
    if err.get("type") == "extra_forbidden":
        msg = "unknown setting (remove it)"
    elif err.get("type") == "missing":
        msg = "required"
    inp = err.get("input")
    got = ""
    if err.get("type") not in ("extra_forbidden", "missing", "model_type") and isinstance(inp, (int, float, str, bool)):
        got = f" (got {inp!r})"
    path = "behaviour.settings" + (f".{loc}" if loc else "")
    return f"{path}: {msg}{got}"


CHILD_VOICE_FLAG = "CHILD_VOICE_STORED"


def default_network(template: str, child_directed: Optional[bool]) -> str:
    """off for a child-directed design (MAJ-2), except the notifier clock, which sets its time over Wi-Fi."""
    return "off" if child_directed and template != "notifier_clock" else "parent_page"


def validate_settings(template: str, settings: Optional[dict], *, child_directed: Optional[bool] = None,
                      safety_flags: Optional[Iterable[str]] = None) -> tuple[Optional[dict], list[str]]:
    """Validate and normalize behaviour settings. Returns (normalized, []) or (None, errors).

    safety_flags: the spec's safety.flags. When given, storing a child's voice (child_voice_clip) also needs
    the CHILD_VOICE_STORED flag and voice_clip_consent true."""
    if template not in MODELS:
        return None, [f"behaviour.template: unknown template {template!r}; use one of {', '.join(TEMPLATES)}"]
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        return None, ["behaviour.settings: must be an object"]
    try:
        model = MODELS[template].model_validate(settings)
    except ValidationError as e:
        return None, [_fmt(x) for x in e.errors(include_url=False)]
    data = model.model_dump(mode="json")
    if data.get("network") is None:
        data["network"] = default_network(template, child_directed)
    errors: list[str] = []
    if template == "notifier_clock" and data["network"] == "off":
        errors.append("behaviour.settings.network: notifier_clock sets its clock over Wi-Fi; use parent_page")
    if template == "story_player" and data.get("capture_mode") == "child_voice_clip" and safety_flags is not None:
        flags = {str(f) for f in safety_flags}
        if not data.get("voice_clip_consent"):
            errors.append("behaviour.settings.capture_mode: child_voice_clip stores the child's voice; it needs "
                          "voice_clip_consent true (the parent's explicit consent in the brief), else use parent_web")
        if CHILD_VOICE_FLAG not in flags:
            errors.append(f"safety.flags: child_voice_clip needs the {CHILD_VOICE_FLAG} flag, else use capture_mode parent_web")
    if child_directed and data.get("volume_max", 0) > 70:
        errors.append("behaviour.settings.volume_max: a child-directed gadget is capped at 70 (use 70 or less)")
    if child_directed and data.get("led_max_percent", 0) > 60:
        errors.append("behaviour.settings.led_max_percent: a child-directed gadget is capped at 60 (use 60 or less)")
    if errors:
        return None, errors
    return data, []


def settings_schema(template: str) -> dict:
    """JSON schema of one template's settings (for the design agent's prompt)."""
    return MODELS[template].model_json_schema()


__all__ = ["validate_settings", "settings_schema", "default_network", "MODELS", "TEMPLATES", "CHILD_VOICE_FLAG"]
