"""Design-specific setup Markdown (the pipeline puts it into SOFTWARE_SETUP.md)."""
from __future__ import annotations

from typing import Any

SELFTEST_PROTOCOL = {
    "port": "UART0 at 115200 baud, 8N1: the devkit's USB port labelled UART (USB-to-UART bridge), or test pads TP5 TXD0 / TP6 RXD0",
    "command": "selftest\\n",
    "other_commands": ["info\\n"],
    "lines": {
        "GS-SELFTEST-BEGIN {json}": "start marker",
        "GS-PROMPT {\"text\": ...}": "an instruction for the operator (press the button, clap)",
        "GS-TEST {\"id\", \"status\", \"detail\"}": "one line per check; status is pass | fail | operator_check | skipped",
        "GS-SELFTEST {\"ok\", \"passed\", \"failed\", \"skipped\", \"operator_checks\", \"firmware\", \"scope\"}": "end summary",
        "GS-BOOT / GS-MEMORY / GS-READY / GS-NET / GS-OTA {json}": "boot and status events printed without a command",
        "GS-INFO {json}": "reply to info",
    },
    "check_ids": ["core_board", "memory", "microsd", "led", "display", "speaker", "microphone", "i2c_bus",
                  "<sensor>_<quantity>", "button_<n>"],
    "parse": "split each line at the first space; the prefix names the record, the rest is one JSON object",
}


def _flash(fw: dict) -> str:
    fl = fw.get("flash") or {}
    mode, freq, size = fl.get("flash_mode", "dio"), fl.get("flash_freq", "80m"), fl.get("flash_size", "8MB")
    return f"""## 1. Put the firmware on the board

The core board is an ESP32-S3-DevKitC-1 N8R8. Plug a USB-C data cable into the devkit port labelled **UART**
(the other port, USB, also works for flashing). The build made these files in `firmware/`:

| File | What it is |
|---|---|
| `firmware-merged.bin` | the complete flash image (bootloader, partition table, app) for offset 0x0 |
| `firmware.bin` | the app only, for offset 0x10000 and for updates |
| `flash/` | the separate parts with their offsets |
| `web-flasher-manifest.json` | manifest for a browser flasher (esp-web-tools format) |
| `SHA256SUMS` | checksums of the two images |

**Option A, in the browser (Chrome or Edge on a computer).** Use the Flash button in the app's Build tab,
which reads `web-flasher-manifest.json`. Or open Espressif's esptool-js page
(https://espressif.github.io/esptool-js/), Connect, add `firmware-merged.bin` at address `0x0`, and Program.

**Option B, esptool on the command line.**

```sh
cd firmware
esptool --chip esp32s3 --baud 921600 write-flash --flash-mode {mode} --flash-freq {freq} --flash-size {size} 0x0 firmware-merged.bin
```

Older esptool versions spell it `esptool.py ... write_flash --flash_mode {mode} --flash_freq {freq} --flash_size {size}`.

**Option C, PlatformIO from source** (rebuilds and flashes; needs PlatformIO 6.2):

```sh
cd firmware
pio run -e s3 -t upload
pio test -e native     # optional: re-run the unit tests on the computer
```

If the board is not found, hold **BOOT**, tap **RST**, release BOOT, and try again.
Check the result with a serial monitor at 115200 baud (`pio device monitor`): a line starting with
`GS-READY` means the firmware started."""


def _wifi(name: str, slug: str, minutes: int) -> str:
    return f"""## 2. Connect it to your home Wi-Fi

1. Power the gadget. On first start it opens a Wi-Fi hotspot named **{name}-setup-XXXX**
   (XXXX are the last characters of its address). The hotspot is open and stays up for {minutes} minutes;
   unplug and replug to open it again.
2. Join that hotspot with a phone. The setup page opens by itself; if not, open http://192.168.4.1.
3. Pick your home Wi-Fi, enter its password, and choose a **parent PIN** of 4 to 8 digits.
4. The gadget joins your Wi-Fi. On your home network, open **http://{slug}.local** (or the IP address
   the setup page shows; Android phones may need the IP address).

Forgot the PIN or changed router: hold the button while plugging the gadget in. After 5 seconds the light
pulses purple and the setup hotspot opens. Keep holding for 10 seconds in total to also forget the saved
Wi-Fi and PIN. Favourites are kept."""


def _parent(template: str, settings: dict) -> str:
    common = """## 3. The parent page

The parent page runs on the gadget itself. It works only on your home network and needs the parent PIN.
After 5 wrong PINs it locks for a minute (longer after repeated locks). Nothing is sent to the internet.
The same page has a JSON API for the app or an MCP client: `GET /api/state` and
`POST /api/action` with `{"action": "..."}`, both with the header `X-GS-PIN`."""
    if template == "story_player":
        mode = settings.get("capture_mode")
        how = {
            "child_voice_clip": f"Hold the button until the light turns **blue** (listening), say the name, and let go. "
                                f"The gadget records about {settings.get('clip_seconds', 2)} seconds, plays it back, and keeps it only if "
                                f"you tap within {settings.get('keep_window_seconds', 10)} seconds. Otherwise it is deleted. "
                                "The microphone is on only while the light is blue.",
            "parent_web": "Type names on the parent page. A name plays as a spoken clip when the story card has one; "
                          "otherwise a soft chime marks the place of the name.",
            "none": "Favourites are off in this build; stories use the default hero.",
        }.get(mode, "")
        return common + f"""

On the page you can:
- see, listen to, rename and remove favourites (each change asks you to confirm);
- erase every favourite and voice clip (type ERASE);
- set the volume (0 to {settings.get('volume_max')}; this is a software setting, not a measured loudness);
- set the night light: on or off during stories, timer up to 120 minutes, brightness up to 60 %;
- play a chosen story, or stop.

**Adding a favourite on the gadget.** {how}

**Tap** plays a story with a favourite as the hero and turns on the warm night light. **Tap again** stops it."""
    extra = {
        "desk_companion": "Use it to say hello, ring a chime, see the face, and set the volume.",
        "notifier_clock": "Use it to see the time, send a notification to the light ring, and clear notifications. "
                          "Apps can send one with `POST /api/action {\"action\":\"notify\",\"text\":\"...\"}`.",
        "ambient_light": "Use it to pick a scene, set the brightness, and turn the light on or off.",
        "sensor_display": "Use it to see the readings, the trend line, and any warning.",
    }.get(template, "")
    return common + "\n\n" + extra


SD_TEXT = """## 4. Prepare the story card (microSD)

Use a microSD card formatted as **FAT32** (32 GB or smaller cards are FAT32 out of the box). Copy everything
inside `firmware/sdcard/` to the root of the card, put it in the gadget's card slot, and restart.
{audio_line}
The gadget creates a `fav/` folder for the child's own voice clips. They stay on this card only."""


def _selftest() -> str:
    return """## 5. Factory self-test

Connect to the serial port (UART0, 115200 baud: the devkit's UART USB port or test pads TP5/TP6) and send
`selftest` followed by Enter. Each check prints one line:

```
GS-TEST {"id":"microsd","status":"pass","detail":{"total_mb":29802,"write_read_512_bytes":true}}
```

`status` is `pass`, `fail`, `operator_check` (the gadget drove the part; a person confirms they saw the colours
or heard the beep) or `skipped` (not in this design). Prompts such as "press the button" or "clap now" arrive
as `GS-PROMPT {"text": ...}` lines. The run ends with one summary line:

```
GS-SELFTEST {"ok":true,"passed":6,"failed":0,"skipped":1,"operator_checks":["led","speaker"],...}
```

`ok` covers the automatic checks only. Microphone levels and sensor values are readings, not calibrations.
This self-test is not a physical, acoustic or toy-safety certification."""


def _ota(fw: dict) -> str:
    signed = fw.get("ota_signed")
    s = ("Updates over Wi-Fi are **on**: the parent page (or the app) uploads `firmware.bin` with its signature "
         "`firmware.sig`, and the gadget refuses any image whose ECDSA P-256 signature does not match the key built into it."
         if signed else
         "Updates over Wi-Fi are **off** in this build because no signing key was provided, so only USB flashing "
         "works. The update slots and rollback are already in place.")
    return f"""## 6. Updates and rollback

The flash has two app slots. {s} A new version boots on trial: it is kept only after its start-up checks pass
and it has run for 30 seconds. If it crashes or fails the checks, the gadget returns to the previous version
by itself. Favourites live in a separate memory area that updates do not touch."""


def _honest(template: str) -> str:
    rows = ["the firmware compiling and its unit tests passing on a computer",
            "pins matching the wiring data (the pin header is generated from it)"]
    return f"""## What has and has not been checked

Checked digitally: {", and ".join(rows)}.

Physical checks still needed: flashing a real board, Wi-Fi joining on your router, the speaker level at the
volume cap, the microphone with a child's voice, the light colours, and the memory surviving a power cut
during a save on real hardware. The memory record is designed and unit-tested for power loss (two copies
with checksums), but that is a test of the logic, not of this board."""


def setup_markdown(spec: dict, template: str, settings: dict, fw: dict, story: dict | None) -> str:
    name = spec.get("name") or "Gadget"
    from .project import ap_prefix, slug_ok

    parts = [f"# Software setup: {name}",
             f"Firmware {fw.get('version', '')} for the **{template}** behaviour, Arduino framework on PlatformIO "
             f"(`{fw.get('platform', '')}`), board `esp32-s3-devkitc-1` with 8 MB flash and 8 MB PSRAM.",
             _flash(fw), _wifi(ap_prefix(name), slug_ok(spec.get("slug") or name), int(settings.get("setup_hotspot_minutes", 15))),
             _parent(template, settings)]
    if template == "story_player":
        if story and story.get("audio"):
            line = "This build includes the story audio (computer voice; listen to one story yourself first)."
        else:
            line = ("**This build has the story text but no audio yet.** Generate the story audio in the app "
                    "(Code tab, Generate story audio), then copy the new card folder. Until then a tap plays a short low chime.")
        parts.append(SD_TEXT.format(audio_line=line))
    parts += [_selftest(), _ota(fw), _honest(template)]
    return "\n\n".join(parts) + "\n"


__all__ = ["setup_markdown", "SELFTEST_PROTOCOL"]
