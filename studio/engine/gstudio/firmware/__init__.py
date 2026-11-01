"""Firmware stage: PlatformIO project generator, compile, native unit tests, story pack.

    run(ctx) -> summary            generate <out>/firmware/, compile (pio run -e s3), test (pio test -e native)
    validate_settings(template, settings) -> (normalized | None, errors)
    generate(spec, electrical, out, ...) -> facts   generate only (no compile), for tests and previews

Options read from ctx.options:
    stories_audio      bool   synthesise story audio with Gemini TTS on Vertex (default False)
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
    settings, errors = validate_settings(template, beh.get("settings") or {}, child_directed=child)
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
    from .project import PLATFORM

    fwfacts = {"framework": "arduino", "board": "esp32-s3-devkitc-1", "platform": PLATFORM,
               "compiled": bool(result.get("compiled")), "size": size,
               "tests_passed": result.get("tests_passed"), "tests_total": result.get("tests_total"),
               "binary": "firmware/firmware.bin" if result.get("compiled") else None,
               "merged_binary": "firmware/firmware-merged.bin" if result.get("merged") else None,
               "sha256": result.get("sha256"), "merged_sha256": result.get("merged_sha256"),
               "flash": result.get("flash"), "version": facts["version"], "template": template,
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
        "warnings": warnings,
        "firmware": fwfacts,
        "settings": settings,
        "story_pack": ({k: v for k, v in story.items() if k != "files"} | {"file_count": len(story.get("files", []))}) if story else None,
        "instances": [{"ref": i.ref, "module": i.module, "kind": i.kind, "slot": i.slot, "feature": i.feature,
                       "pins": {p: v[0] for p, v in i.pins.items()}} for i in b.instances],
        "lib_deps": b.lib_deps,
        "selftest_protocol": SELFTEST_PROTOCOL,
        "api": {"state": "GET /api/state", "action": "POST /api/action {action, ...}", "auth_header": "X-GS-PIN",
                "info": "GET /api/info (no PIN)", "clip": "GET /api/clip?id=N", "ota": "POST /api/ota (multipart, X-GS-Signature)"},
        "files": ["firmware/" + f for f in facts["files"]],
        "timings": timings,
        "platformio": result.get("platformio"),
        "setup_markdown": md,
        "software_setup_md": md,
    }


__all__ = ["run", "generate", "validate_settings", "settings_schema", "TEMPLATES", "MODELS", "library_lookup"]
