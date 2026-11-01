"""Firmware stage: PlatformIO project generator, compile, native unit tests, story pack.

    run(ctx) -> summary            generate <out>/firmware/, compile (pio run -e s3), test (pio test -e native)
    validate_settings(template, settings) -> (normalized | None, errors)
    generate(spec, electrical, out, ...) -> facts   generate only (no compile), for tests and previews

Options read from ctx.options:
    stories_audio      bool   synthesise story audio with Gemini TTS on Vertex (default False)
    speech_roundtrip   bool   with story audio: parse every WAV with the firmware's wav_parse and transcribe it
                              with Gemini (firmware/speech-roundtrip.json, MAJ-18) (default True)
    firmware_compile   bool   compile and test (default True)
    ota_signing_key    path   PEM ECDSA P-256 private key: embeds its public key and signs firmware.bin
    tts_voice          str    override the voice (default from settings.voice)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..errors import StageError
from .settings import MODELS, TEMPLATES, settings_schema, validate_settings

STAGE = "firmware"
LIB_DIR = Path(__file__).resolve().parents[3] / "library"


def _spec_dict(spec: Any) -> dict:
    if hasattr(spec, "model_dump"):
        return spec.model_dump(mode="json")
    if isinstance(spec, dict):
        return spec
    raise StageError("firmware: the spec is not a model or a dict", {})


def library_lookup(lib: Any = None) -> Callable[[str], Optional[dict]]:
    """Module id -> module dict, from ctx.lib when it can answer, else from library/modules*.json."""
    table: dict[str, dict] = {}
    names = ["modules.json"] if (LIB_DIR / "modules.json").exists() else ["modules-a.json", "modules-b.json"]
    for n in names:
        p = LIB_DIR / n
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for m in data if isinstance(data, list) else data.get("modules", []):
                if isinstance(m, dict) and m.get("id"):
                    table[m["id"]] = m

    def get(mid: str) -> Optional[dict]:
        if lib is not None and hasattr(lib, "get"):
            try:
                m = lib.get(mid)
                if m is not None:
                    return m.model_dump(mode="json") if hasattr(m, "model_dump") else dict(m)
            except Exception:
                pass
        return table.get(mid)

    return get


def _load_electrical(ctx) -> dict:
    if isinstance(getattr(ctx, "electrical", None), dict) and ctx.electrical:
        return ctx.electrical
    p = Path(ctx.out) / "electrical.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    raise StageError("firmware needs electrical.json; run the electrical stage first", {"path": str(p)})


def generate(spec: Any, electrical: dict, out: Path, *, lib: Any = None, ota_pubkey_pem: str = "",
             engine_version: Optional[str] = None) -> dict:
    """Validate and write the PlatformIO project into out/firmware (no compile)."""
    from ..version import ENGINE_VERSION
    from .hw import bind, check_template
    from .project import write_project

    sp = _spec_dict(spec)
    beh = sp.get("behaviour") or {}
    template = beh.get("template")
    child = bool((sp.get("audience") or {}).get("child_directed"))
    flags = list((sp.get("safety") or {}).get("flags") or [])
    settings, errors = validate_settings(template, beh.get("settings") or {}, child_directed=child, safety_flags=flags)
    if errors:
        raise StageError("behaviour settings are invalid", {"errors": errors})
    binding = bind(electrical, sp, library_lookup(lib))
    warnings = list(binding.warnings)
    warnings += check_template(template, binding, settings)
    fw = Path(out) / "firmware"
    facts = write_project(fw, sp, template, settings, binding, engine_version or ENGINE_VERSION, ota_pubkey_pem)
    facts.update({"template": template, "settings": settings, "binding": binding, "warnings": warnings, "dir": fw})
    return facts


def _signing(ctx) -> tuple[str, Optional[Any]]:
    keyp = ctx.options.get("ota_signing_key") or os.environ.get("GS_OTA_SIGNING_KEY")
    if not keyp:
        return "", None
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_private_key(Path(keyp).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise StageError("ota_signing_key must be an ECDSA P-256 private key (PEM)", {})
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pub, key


def _sign(key: Any, binp: Path) -> Path:
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    sig = key.sign(binp.read_bytes(), ec.ECDSA(hashes.SHA256()))
    out = binp.with_suffix(".sig")
    out.write_text(base64.b64encode(sig).decode() + "\n")
    return out


def _speech_checks(fw: Path, warnings: list[str]) -> dict:
    """MAJ-18: parse every card WAV with the firmware's wav_parse, then the TTS-to-STT round trip."""
    from .build import shared_dir
    from .speech import STT_MODEL, GeminiStt, check_card, roundtrip, write_report

    sd = fw / "sdcard"
    card = check_card(sd, fw / "lib" / "gs_core" / "src", shared_dir() / "tools")
    if not card["ok"]:
        warnings.append(f"{card['failed']} of {card['files']} card WAVs failed the firmware's wav_parse check; see firmware/speech-roundtrip.json")
    trip, err = None, None
    try:
        stt = GeminiStt(cache=shared_dir() / "stt-cache")
        trip = roundtrip(sd, stt, STT_MODEL, card["rows"])
    except Exception as e:  # no credentials or model: the report says so and the check fails
        err = f"speech round trip did not run: {str(e)[:200]}"
        warnings.append(err)
    rep = write_report(fw, card, trip, err)
    if trip and not trip["ok"]:
        bad = [r["file"] for r in trip["rows"] if not r.get("ok")]
        warnings.append(f"speech round trip: {len(bad)} card files above the limit or unreadable: {', '.join(bad[:5])}")
    return {"file": "firmware/speech-roundtrip.json", "ok": rep["ok"], "scope": rep.get("scope"),
            "segments": f"{rep.get('segments_passed', 0)}/{rep.get('segments_total', 0)}",
            "worst_segment_wer": rep.get("worst_segment_wer"), "names_exact": rep.get("names_exact"),
            "wav_files_ok": f"{card['files'] - card['failed']}/{card['files']}"}


def run(ctx) -> dict:
    t0 = time.monotonic()
    prog = lambda msg, pct=None: ctx.progress(STAGE, msg, pct)  # noqa: E731
    prog("Generating the firmware project", 2)
    electrical = _load_electrical(ctx)
    pub_pem, key = _signing(ctx)
    facts = generate(ctx.spec, electrical, Path(ctx.out).resolve(), lib=getattr(ctx, "lib", None), ota_pubkey_pem=pub_pem)
    fw: Path = facts["dir"]
    sp = _spec_dict(ctx.spec)
    template, settings = facts["template"], facts["settings"]
    warnings: list[str] = list(facts["warnings"])
    timings: dict[str, float] = {"generate_s": round(time.monotonic() - t0, 2)}

    story = None
    if template == "story_player":
        from .stories import write_pack

        audio = bool(ctx.options.get("stories_audio"))
        synth = voice = model = None
        if audio:
            prog("Synthesising story audio (Gemini TTS)", 84)
            try:
                from .build import shared_dir
                from .tts import GeminiTts

                voice = ctx.options.get("tts_voice") or settings.get("voice", "Achernar")
                tts = GeminiTts(voice=voice, language=settings.get("language", "en-IN"),
                                cache_dir=shared_dir() / "tts-cache")
                synth, model = tts.synth, tts.model
            except Exception as e:
                warnings.append(f"story audio was requested but TTS is unavailable: {str(e)[:200]}; the card has text only")
                audio = False
        ts = time.monotonic()
        story = write_pack(fw / "sdcard", sp, settings, audio=audio, synth=synth, voice=voice, model=model,
                           progress=lambda m, p: prog(m, p))
        timings["story_pack_s"] = round(time.monotonic() - ts, 1)
        if story.get("missing"):
            warnings.append(f"{len(story['missing'])} story audio files failed to synthesise; the card lists them")
        if story.get("audio") and ctx.options.get("speech_roundtrip", True):
            ts = time.monotonic()
            prog("Checking the story audio (parse and transcribe)", 96)
            story["speech"] = _speech_checks(fw, warnings)
            timings["speech_check_s"] = round(time.monotonic() - ts, 1)
        elif (fw / "speech-roundtrip.json").exists():
            (fw / "speech-roundtrip.json").unlink()  # a stale report from an earlier run of this version

    result: dict[str, Any] = {"compiled": False, "tests_passed": None, "tests_total": None}
    if ctx.options.get("firmware_compile", True):
        from .build import build

        result = build(fw, facts["binding"].lib_deps, f"{facts['build_id']}-{template}",
                       progress=lambda m, p: prog(m, p))
        timings.update(result.get("timings", {}))
        warnings += [f"compiler: {w}" for w in result.get("warnings", [])]
        if not result.get("compiled"):
            log = (fw / "build-log.txt").read_text(errors="replace") if (fw / "build-log.txt").exists() else ""
            errs = [l for l in log.splitlines() if ": error:" in l or "Error:" in l][:20]
            raise StageError("firmware did not compile for ESP32-S3", {"errors": errs, "log": "firmware/build-log.txt"})
    else:
        warnings.append("compile skipped (firmware_compile is false): no firmware.bin in this version")

    sig = None
    if result.get("compiled") and key is not None:
        sig = _sign(key, fw / "firmware.bin")
    if result.get("compiled"):
        sums = [f"{result['sha256']}  firmware.bin"]
        if result.get("merged"):
            sums.append(f"{result['merged_sha256']}  firmware-merged.bin")
        (fw / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        manifest = {"name": f"{sp.get('name', 'Gadget')} firmware", "version": facts["version"],
                    "new_install_prompt_erase": True,
                    "builds": [{"chipFamily": "ESP32-S3", "parts": [{"path": "firmware-merged.bin", "offset": 0}]}]}
        if result.get("merged"):
            (fw / "web-flasher-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        else:
            warnings.append("esptool merge_bin failed: no firmware-merged.bin or web-flasher manifest; flash the parts in flash/")
    size = dict(result.get("size") or {})
    if result.get("compiled"):
        from .project import APP_SLOT_BYTES

        size.update({"app_bin_bytes": result.get("bytes"), "app_slot_bytes": APP_SLOT_BYTES,
                     "app_slot_used_percent": round(100 * result.get("bytes", 0) / APP_SLOT_BYTES, 1),
                     "merged_bin_bytes": result.get("merged_bytes"), "sha256": result.get("sha256"),
                     "merged_sha256": result.get("merged_sha256")})
        (fw / "size.json").write_text(json.dumps(size, indent=1) + "\n")
    from .project import APP_FLASH_MODE, PLATFORM

    flash = dict(result.get("flash") or {})
    if flash:
        flash["app_flash_mode"] = APP_FLASH_MODE
        flash["note"] = (f"esptool and the image header use {flash.get('flash_mode')}; the bootloader then runs the flash in "
                         f"{APP_FLASH_MODE} (platformio.ini board_build.flash_mode)")
    suites = result.get("test_suites") or {}
    caps = {"volume_max": settings.get("volume_max"), "led_max_percent": settings.get("led_max_percent"),
            "night_light_max_percent": settings.get("night_light_max_percent") if template == "story_player" else None,
            "night_light_effective_max_percent": (round(settings["night_light_max_percent"] * settings["led_max_percent"] / 100, 1)
                                                  if template == "story_player" else None),
            "config_h": ["GS_CFG_VOLUME_MAX", "GS_CFG_LED_CAP", "GS_CFG_NIGHT_LEVEL_MAX", "GS_CFG_NIGHT_DUTY_MAX_PERMILLE"]}
    privacy = {"network": settings.get("network"),
               "setup_hotspot": ("WPA2, opens only after a 5 s button hold at power-up; first PIN needs a tap on the device"
                                 if settings.get("network") == "parent_page" else "none: no Wi-Fi in this build"),
               "pin_digits": "6 to 8", "pin_guard": "per address (5 tries) and device-wide (20), kept across restarts"}
    if template == "story_player":
        voice = settings.get("capture_mode") == "child_voice_clip"
        privacy.update({"capture_mode": settings.get("capture_mode"), "voice_clip_consent": settings.get("voice_clip_consent"),
                        "voice_stored": voice,
                        "clips": ("AES-256-CTR on the card, key in the device's NVS; zeroed before delete; erase destroys the key; "
                                  f"deleted after {settings.get('clip_retention_days')} powered-on days") if voice else "none recorded"})

    fwfacts = {"framework": "arduino", "board": "esp32-s3-devkitc-1", "platform": PLATFORM,
               "compiled": bool(result.get("compiled")), "size": size,
               "tests_passed": result.get("tests_passed"), "tests_total": result.get("tests_total"),
               "binary": "firmware/firmware.bin" if result.get("compiled") else None,
               "merged_binary": "firmware/firmware-merged.bin" if result.get("merged") else None,
               "sha256": result.get("sha256"), "merged_sha256": result.get("merged_sha256"),
               "flash": flash or None, "version": facts["version"], "template": template,
               "tests_product": suites.get("product"), "tests_shared": suites.get("shared"),
               "production_profile": {"env": "s3-production", "sdkconfig": "firmware/sdkconfig.production.defaults",
                                      "flash_encryption": "release mode", "built": False},
               "ota_signed": bool(sig), "web_flasher_manifest": "firmware/web-flasher-manifest.json" if result.get("merged") else None}
    from .setup_doc import SELFTEST_PROTOCOL, setup_markdown

    md = setup_markdown(sp, template, settings, fwfacts, story)
    timings["total_s"] = round(time.monotonic() - t0, 1)
    prog("Firmware ready" if result.get("compiled") else "Firmware generated", 100)
    b = facts["binding"]
    return {
        "template": template,
        "compiled": fwfacts["compiled"],
        "size": size,
        "sha256": fwfacts["sha256"],
        "tests_passed": fwfacts["tests_passed"],
        "tests_total": fwfacts["tests_total"],
        "tests_ok": result.get("tests_ok"),
        "tests_product": suites.get("product"),
        "tests_shared": suites.get("shared"),
        "test_suites": suites.get("suites"),
        "caps": caps,
        "privacy": privacy,
        "speech_roundtrip": (story or {}).get("speech"),
        "warnings": warnings,
        "firmware": fwfacts,
        "settings": settings,
        "story_pack": ({k: v for k, v in story.items() if k != "files"} | {"file_count": len(story.get("files", []))}) if story else None,
        "instances": [{"ref": i.ref, "module": i.module, "kind": i.kind, "slot": i.slot, "feature": i.feature,
                       "pins": {p: v[0] for p, v in i.pins.items()}} for i in b.instances],
        "lib_deps": b.lib_deps,
        "selftest_protocol": SELFTEST_PROTOCOL,
        "api": {"state": "GET /api/state", "action": "POST /api/action {action, ...}", "auth_header": "X-GS-PIN",
                "info": "GET /api/info (no PIN)", "clip": "GET /api/clip?id=N",
                "name_clip": "POST /api/name_clip?id=N (multipart, 16-bit mono WAV <= 64 KB)",
                "ota": "POST /api/ota (multipart, X-GS-Signature)", "enabled": settings.get("network") == "parent_page"},
        "files": ["firmware/" + f for f in facts["files"]],
        "timings": timings,
        "platformio": result.get("platformio"),
        "setup_markdown": md,
        "software_setup_md": md,
    }


__all__ = ["run", "generate", "validate_settings", "settings_schema", "TEMPLATES", "MODELS", "library_lookup"]
