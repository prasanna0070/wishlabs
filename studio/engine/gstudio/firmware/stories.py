"""Story pack for the story_player template: <out>/firmware/sdcard/.

Always written (offline-safe, deterministic): stories.json (text, segments, file names) and README.txt.
With ctx.options["stories_audio"] the segment audio (and name clips) is synthesised with Gemini TTS on
Vertex and written as 16-bit mono WAV, one file per segment, which the firmware plays directly.

Card layout (FAT32, files at the card root):
  stories.json                 index the firmware reads (id, title, segments) plus the full text
  stories/<id>/sNN.wav         one WAV per segment; the favourite's name is played between segments
  names/<slug>.wav             story-pack name clips (parent_web mode); names/_default.wav = default hero
  fav/                         created by the device for the child's own voice clips (never generated here)
  README.txt                   what is on the card and how to finish it
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

DATA = Path(__file__).resolve().parent / "data" / "stories_en_in.json"
SAMPLE_RATE = 24000
MIN_WORDS, MAX_WORDS = 150, 220


def name_slug(name: str) -> str:
    """Same rule as gs::name_slug in the firmware (lower case, spaces/hyphens to '_')."""
    out = []
    for c in name:
        if c.isascii() and c.isalpha():
            out.append(c.lower())
        elif c in " -" and out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def load_stories() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))["stories"]


def segments(story: dict) -> list[str]:
    """Spoken text per audio segment. Segment 0 starts with the title."""
    parts = story["text"].split("{FAV}")
    parts[0] = f"{story['title']}.\n\n{parts[0]}"
    return [re.sub(r"\s+", " ", p).strip() for p in parts]


def word_count(story: dict, sample_name: str = "Anya") -> int:
    return len(story["text"].replace("{FAV}", sample_name).split())


def build_index(settings: dict, audio: bool, voice: Optional[str], model: Optional[str]) -> dict:
    stories = []
    for s in load_stories():
        segs = segments(s)
        stories.append({
            "id": s["id"], "title": s["title"], "segments": len(segs), "words": word_count(s),
            "favourite_slots": s["text"].count("{FAV}"),
            "files": [f"stories/{s['id']}/s{i:02d}.wav" for i in range(len(segs))],
            "segment_text": segs, "text": s["text"],
        })
    names = [{"name": n, "file": f"names/{name_slug(n)}.wav"} for n in settings.get("names_for_audio") or []]
    return {
        "schema": "gadget-storypack/1",
        "language": settings.get("language", "en-IN"),
        "audio": audio,
        "audio_format": f"WAV, 16-bit PCM, mono, {SAMPLE_RATE} Hz" if audio else None,
        "voice": voice if audio else None,
        "tts_model": model if audio else None,
        "default_name": {"name": settings.get("default_hero", "little star"), "file": "names/_default.wav"},
        "names": names,
        "stories": stories,
    }


README_AUDIO = """Story card for {name}
========================

Copy everything in this folder to the root of a microSD card formatted as FAT32
(cards up to 32 GB are FAT32 out of the box). Then put the card in the gadget and restart it.

What is here
- stories.json: the six stories (text, and which audio file plays when).
- stories/<story>/sNN.wav: the story audio, one file per part. The favourite's name plays between parts.
- names/: spoken name clips for names typed on the parent page, and _default.wav for when no favourite is saved.

The gadget makes a fav/ folder for the child's own voice clips. Those clips stay on this card only.
Erase them from the parent page (Erase all favourites), or by deleting the fav/ folder on a computer.

The audio was synthesised with {model} (voice {voice}). It is a computer voice, not a person.
Listen to a story yourself before bedtime use.
"""

README_TEXT = """Story card for {name}
========================

This card has the story TEXT but NO AUDIO yet. The gadget cannot read text aloud by itself, so until
the audio is added a tap plays a short low chime and the parent page says "no audio yet".

To finish the card:
1. In the Gadget Studio app, open this project's Code tab and choose "Generate story audio".
   (The build service makes the audio with a text-to-speech voice; it takes about a minute.)
2. Download the new story card folder and copy everything in it to the root of a FAT32 microSD card.
3. Put the card in the gadget and restart it.

What is here now
- stories.json: the six stories with their text, and the file names the audio will use.

The gadget makes a fav/ folder for the child's own voice clips. Those clips stay on this card only.
"""


def write_pack(sd: Path, spec: dict, settings: dict, *, audio: bool = False,
               synth: Optional[Callable[[str, str, Path], None]] = None, voice: Optional[str] = None,
               model: Optional[str] = None, progress: Optional[Callable[[str, float], None]] = None) -> dict[str, Any]:
    """Write the card. synth(text, style, wav_path) produces one WAV (used when audio is True)."""
    sd.mkdir(parents=True, exist_ok=True)
    for sub in ("stories", "names"):
        p = sd / sub
        if p.exists():
            for f in sorted(p.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            p.rmdir()
    index = build_index(settings, audio, voice, model)
    report: dict[str, Any] = {"audio": False, "stories": len(index["stories"]), "files": [], "missing": []}
    if audio and synth is not None:
        jobs: list[tuple[str, str, Path]] = []
        story_style = ("Read this part of a bedtime story for a young child in India, in a warm, soft, slow "
                       "Indian English voice. Gentle pauses at full stops. It continues straight after a child's name.")
        for st in index["stories"]:
            for i, text in enumerate(st["segment_text"]):
                jobs.append((text, story_style, sd / st["files"][i]))
        name_style = "Say only this name, warmly and softly, as if it is the hero of a bedtime story."
        jobs.append((index["default_name"]["name"], name_style, sd / index["default_name"]["file"]))
        for n in index["names"]:
            jobs.append((n["name"], name_style, sd / n["file"]))
        from concurrent.futures import ThreadPoolExecutor, as_completed

        for _t, _s, path in jobs:
            path.parent.mkdir(parents=True, exist_ok=True)
        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(synth, text, style, path): path for text, style, path in jobs}
            for fut in as_completed(futs):
                path = futs[fut]
                try:
                    fut.result()
                    report["files"].append(str(path.relative_to(sd)))
                except Exception as e:  # recorded honestly, the pack still ships
                    report["missing"].append({"file": str(path.relative_to(sd)), "error": str(e)[:200]})
                done += 1
                if progress:
                    progress(f"Story audio {done}/{len(jobs)}", 85 + 10 * done / len(jobs))
        report["files"].sort()
        report["audio"] = not report["missing"]
        index["audio"] = report["audio"]
        if report["missing"]:
            index["audio_missing"] = [m["file"] for m in report["missing"]]
    (sd / "stories.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    name = spec.get("name") or "your gadget"
    if index["audio"]:
        readme = README_AUDIO.format(name=name, model=model or "a text-to-speech model", voice=voice or "default")
    else:
        readme = README_TEXT.format(name=name)
        if audio and report["missing"]:
            readme += f"\nNote: audio generation was attempted but {len(report['missing'])} files failed; see stories.json audio_missing.\n"
    (sd / "README.txt").write_text(readme, encoding="utf-8")
    report["index"] = "sdcard/stories.json"
    report["words"] = {s["id"]: s["words"] for s in index["stories"]}
    return report


__all__ = ["write_pack", "load_stories", "segments", "word_count", "name_slug", "build_index", "MIN_WORDS", "MAX_WORDS"]
