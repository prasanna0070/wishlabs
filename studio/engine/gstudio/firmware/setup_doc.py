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
    from .project import APP_FLASH_MODE, HEADER_FLASH_MODE

    fl = fw.get("flash") or {}
    mode, freq, size = fl.get("flash_mode", HEADER_FLASH_MODE), fl.get("flash_freq", "80m"), fl.get("flash_size", "8MB")
    app_mode = fl.get("app_flash_mode", APP_FLASH_MODE)
    tp, ts = fw.get("tests_product") or {}, fw.get("tests_shared") or {}
    tests = ""
    if tp.get("total") or ts.get("total"):
        tests = (f"\n\nUnit tests on the build machine: {tp.get('passed', 0)} of {tp.get('total', 0)} product tests (this "
                 f"behaviour) and {ts.get('passed', 0)} of {ts.get('total', 0)} shared tests (every gadget) passed. They run "
                 "the firmware's own source against simulated hardware, not on a board.")
    return f"""## 1. Put the firmware on the board

The core board is an ESP32-S3-DevKitC-1 N8R8. Plug a USB-C data cable into the devkit port labelled **UART**
(the other port, USB, also works for flashing). The build made these files in `firmware/`:

| File | What it is |
|---|---|
| `firmware-merged.bin` | the complete flash image (bootloader, partition table, app) for offset 0x0 |
| `firmware.bin` | the app only, for offset 0x10000 and for updates |
| `flash/` | the bootloader (0x0), partition table (0x8000) and boot_app0 (0xE000); the app part is `firmware.bin` |
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

**Flash mode.** Use `{mode}` with esptool: that is what the image header holds. The firmware itself runs the flash in
`{app_mode}` (`board_build.flash_mode = {app_mode}` in `platformio.ini`): ESP-IDF's bootloader starts in {mode} and
switches to {app_mode}. Both values are also in `build.json` (`firmware.flash.flash_mode`, `firmware.flash.app_flash_mode`).

**Option C, PlatformIO from source** (rebuilds and flashes; needs PlatformIO 6.2):

```sh
cd firmware
pio run -e s3 -t upload
pio test -e native     # optional: re-run the unit tests on the computer
```

If the board is not found, hold **BOOT**, tap **RST**, release BOOT, and try again.
Check the result with a serial monitor at 115200 baud (`pio device monitor`): a line starting with
`GS-READY` means the firmware started.{tests}"""


def _wifi(name: str, slug: str, minutes: int, settings: dict) -> str:
    if settings.get("network") != "parent_page":
        return """## 2. Wi-Fi: off in this build

This gadget has **no Wi-Fi**: the radio is never switched on, there is no setup hotspot and no parent page
(the default for a gadget made for a child). Everything it needs is on the story card. To change names or
stories, edit them in the Gadget Studio app, download the new story card and copy it onto the microSD card.
Holding the button while plugging in does nothing special in this build."""
    return f"""## 2. Connect it to your home Wi-Fi

1. Hold the gadget's button while you plug it in, and keep holding for **5 seconds**, until the light pulses
   purple. Only then does it open its setup hotspot, **{name}-setup-XXXX** (XXXX are the last characters of its
   address); it never opens one by itself. The hotspot is protected (WPA2) with a password made for this
   gadget, shown on its screen (gadgets without a screen print it on the serial port at 115200 baud). It stays
   up for {minutes} minutes.
2. Join that hotspot with a phone using that password. The setup page opens by itself; if not, open http://192.168.4.1.
3. Pick your home Wi-Fi, enter its password, and choose a **parent PIN of 6 to 8 digits**. The first time,
   the page asks you to **press the button on the gadget once**, then press Save again: only someone at the
   gadget can claim it.
4. The gadget joins your Wi-Fi. On your home network, open **http://{slug}.local** (or the IP address
   the setup page shows; Android phones may need the IP address).

Forgot the PIN or changed router: hold the button while plugging the gadget in. After 5 seconds the setup
hotspot opens. Keep holding for 10 seconds in total to also forget the saved Wi-Fi and PIN. Favourites are kept."""


def _parent(template: str, settings: dict) -> str:
    if settings.get("network") != "parent_page":
        if template == "story_player":
            return """## 3. Favourites without a parent page

The names you typed in the Gadget Studio app are on the story card with their spoken clips. At start-up the
gadget adds each one to its favourites; a story then stars one as the hero and another as the friend.
To add, rename or remove a name, change it in the app and copy the new story card."""
        return """## 3. Settings

This build has no parent page; its settings are fixed in the design. Change them in the app and rebuild."""
    common = """## 3. The parent page

The parent page runs on the gadget itself. It works only on your home network and needs the parent PIN, which
travels over plain HTTP on that network (there is no certificate on a home gadget). After 5 wrong PINs from
one phone or computer it locks that one out for a minute (longer after repeated locks); after 20 wrong PINs
in all it locks the page for everyone. The counts and locks survive unplugging the gadget."""
    if template == "story_player":
        return common + f"""

On the page you can:
- add, rename and remove favourite names (each change asks you to confirm). A new name shows **not spoken yet**
  until it has a spoken clip: add one with **Add spoken name** (a 16-bit mono WAV of 64 KB or less, for
  example the one the Gadget Studio app makes for that name). Until then a story uses the default name there;
- erase every favourite (type ERASE);
- set the volume (0 to {settings.get('volume_max')}; this is a software setting, not a measured loudness);
- set the night light: on or off during stories, timer up to 120 minutes, brightness up to {settings.get('night_light_max_percent')} %;
- play a chosen story, or stop.

**Tap** the gadget's button for a story with your favourites as the hero and the friend, and the warm night
light. **Tap again** stops it."""
    extra = {
        "desk_companion": "Use it to say hello, ring a chime, see the face, and set the volume.",
        "notifier_clock": "Use it to see the time, send a notification to the light ring, and clear notifications. "
                          "Apps can send one with `POST /api/action {\"action\":\"notify\",\"text\":\"...\"}`.",
        "ambient_light": "Use it to pick a scene, set the brightness, and turn the light on or off.",
        "sensor_display": "Use it to see the readings, the trend line, and any warning.",
    }.get(template, "")
    return common + "\n\n" + extra


SD_TEXT = """## 4. Prepare the story card (microSD)

The gadget needs a microSD card formatted as **FAT32** (32 GB or smaller cards are FAT32 out of the box). Copy
everything inside `firmware/sdcard/` to the root of the card, put it in the gadget's card slot, and restart.
{audio_line}
The card also carries short spoken instructions for the child (`prompts/`); where one is missing the gadget
plays a chime instead."""


def _privacy(template: str, settings: dict) -> str:
    """The one privacy rule, exactly as this firmware behaves (MAJ-1). The design agent's SAFETY rule says the same."""
    if template != "story_player":
        mic = ("The microphone, when fitted, measures loudness for claps only and keeps no sound; a dot on the face shows "
               "while it is on." if template == "desk_companion" else "This gadget has no voice features.")
        return f"## 5. Privacy\n\n{mic} Nothing is sent to the internet."
    if settings.get("capture_mode") == "child_voice_clip":
        days = settings.get("clip_retention_days")
        return f"""## 5. Privacy: the child's voice clips

This build records the child's own voice for favourite names, because the parent agreed to it in the design
brief. The rule it follows:
- The microphone opens only while the button is held after the light turns blue, and the light and face show
  "listening" for as long as it is open (also during the factory self-test).
- Each kept clip (at most {settings.get('clip_seconds')} seconds) is stored on the microSD card **encrypted**
  (AES-256) with a random key that stays inside the gadget. On a computer the files cannot be played.
- A clip you do not keep within {settings.get('keep_window_seconds')} seconds is deleted. Removing a favourite,
  or erasing all, overwrites its file with zeros and deletes it; erasing all also destroys the key. If the card
  is out, the page says so and the gadget finishes the deletion the next time it starts with the card.
- Clips are deleted after {days} days of the gadget being switched on (it has no clock of its own).
- Nothing is sent to the internet.
SD cards move data around internally, so the zero overwrite is best effort; the encryption is what protects a
card that is lost or copied."""
    return """## 5. Privacy

This gadget **records no voice**. Favourite names are typed by a parent and spoken from clips made by the
Gadget Studio app. If the design has a microphone, this firmware opens it only for the factory self-test's
level check, with the listening light and face on. Nothing is sent to the internet."""


def _selftest() -> str:
    return """## 6. Factory self-test

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
This self-test is not a physical, acoustic or toy-safety certification. Physical checks still needed: flashing
a real board, the speaker level at the volume cap, the light colours and brightness, and the memory surviving a
power cut during a save on real hardware (the two-copy memory record is unit-tested for power loss, which is a
test of the logic, not of this board)."""


def _ota(fw: dict) -> str:
    signed = fw.get("ota_signed")
    s = ("Updates over Wi-Fi are **on**: the parent page (or the app) uploads `firmware.bin` with its signature "
         "`firmware.sig`, and the gadget refuses any image whose ECDSA P-256 signature does not match the key built into it."
         if signed else
         "Updates over Wi-Fi are **off** in this build because no signing key was provided, so only USB flashing "
         "works. The update slots and rollback are already in place.")
    return f"""## 7. Updates, rollback and the production profile

The flash has two app slots. {s} A new version boots on trial: it is kept only after its start-up checks pass
and it has run for 30 seconds. If it crashes or fails the checks, the gadget returns to the previous version
by itself. Favourites live in a separate memory area that updates do not touch.

For gadgets that are sold, `platformio.ini` has a second environment, `s3-production`, with
`sdkconfig.production.defaults`: **flash encryption in release mode** and NVS encryption. It is not built or
tested here: it needs the ESP-IDF framework package, it burns the board's eFuses on first boot (irreversible),
and the favourites partition still needs encrypted NVS initialisation before that profile ships."""


def setup_markdown(spec: dict, template: str, settings: dict, fw: dict, story: dict | None) -> str:
    name = spec.get("name") or "Gadget"
    from .project import ap_prefix, slug_ok

    parts = [f"# Software setup: {name}",
             f"Firmware {fw.get('version', '')} for the **{template}** behaviour, Arduino framework on PlatformIO "
             f"(`{fw.get('platform', '')}`), board `esp32-s3-devkitc-1` with 8 MB flash and 8 MB PSRAM.",
             _flash(fw),
             _wifi(ap_prefix(name), slug_ok(spec.get("slug") or name), int(settings.get("setup_hotspot_minutes", 15)), settings),
             _parent(template, settings)]
    if template == "story_player":
        if story and story.get("audio"):
            line = "This build includes the story audio (computer voice; listen to one story yourself first)."
            sp = story.get("speech") or {}
            if sp:
                line += (f" Each card file was read back by the firmware's WAV parser ({sp.get('wav_files_ok')} passed) and "
                         f"transcribed by a speech model ({sp.get('segments')} story parts within the word-error limit, "
                         f"names exact {sp.get('names_exact')}): `firmware/speech-roundtrip.json`. That is a test of the "
                         "synthetic voice only: no speaker or child was tested.")
        else:
            line = ("**This build has the story text but no audio yet.** Generate the story audio in the app "
                    "(Code tab, Generate story audio), then copy the new card folder. Until then a tap plays a short low chime.")
        parts.append(SD_TEXT.format(audio_line=line))
    parts += [_privacy(template, settings), _selftest(), _ota(fw)]
    return "\n\n".join(parts) + "\n"


__all__ = ["setup_markdown", "SELFTEST_PROTOCOL"]
