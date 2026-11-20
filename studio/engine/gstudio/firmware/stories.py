"""Story pack for the story_player template: <out>/firmware/sdcard/.

Always written (offline-safe, deterministic): stories.json (text, segments, file names) and README.txt.
With ctx.options["stories_audio"] the segment audio (and name clips) is synthesised with Gemini TTS on
Vertex and written as 16-bit mono WAV, one file per segment, which the firmware plays directly.

Card layout (FAT32, files at the card root):
  stories.json                 index the firmware reads (id, title, segments, slots) plus the full text
  stories/<id>/sNN.wav         one WAV per segment; a name is played between segments: the hero at {FAV}
                               slots, the friend at {FRIEND} slots
  names/<slug>.wav             spoken name clips for typed names (parent_web mode); names/_default.wav is the
                               default hero, names/_friend.wav the default friend
  prompts/*.wav                spoken guidance for the child (MIN-38): hello, hold_to_tell, tap_to_keep, saved,
                               try_again. The firmware plays a chime for any prompt the card lacks.
  fav/                         created by the device for the child's own voice clips in the opt-in
                               child_voice_clip mode (encrypted; never generated here)
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


SLOT = re.compile(r"\{(FAV|FRIEND)\}")

# TTS style lines (the Gemini-TTS "prompt"). Kept to a short description: a longer instruction with full sentences
# ("Gentle pauses at full stops. It continues straight after a child's name.") was read out loud in story parts, and the
# hint about a name made the voice invent one; the speech round trip (MAJ-18) caught both.
STORY_STYLE = "Warm, soft, slow bedtime storyteller voice, Indian English."
NAME_STYLE = "Warm, soft voice saying one name."
PROMPT_STYLE = "Warm, soft, slow voice speaking kindly to a young child, Indian English."

# Spoken guidance for the child (MIN-38). Played from the card; a chime stands in for any that is missing.
PROMPTS = {
    "hello": "Hello! Tap my button for a story.",
    "hold_to_tell": "Tell me a name.",
    "tap_to_keep": "Did you hear it? Tap my button to keep it.",
    "saved": "Saved! I will remember that name.",
    "try_again": "Let's try again. Hold my button and tell me a name.",
}


def segments(story: dict) -> list[str]:
    """Spoken text per audio segment, split at the {FAV} and {FRIEND} slots. Segment 0 starts with the title."""
    parts = SLOT.split(story["text"])[0::2]
    parts[0] = f"{story['title']}.\n\n{parts[0]}"
    return [re.sub(r"\s+", " ", p).strip() for p in parts]


def slots(story: dict) -> list[str]:
    """The name slot after each segment but the last: "FAV" (the hero) or "FRIEND"."""
    return SLOT.findall(story["text"])


def word_count(story: dict, sample_name: str = "Anya", friend_name: str = "Kabir") -> int:
    return len(story["text"].replace("{FAV}", sample_name).replace("{FRIEND}", friend_name).split())


def build_index(settings: dict, audio: bool, voice: Optional[str], model: Optional[str]) -> dict:
    stories = []
    for s in load_stories():
        segs = segments(s)
        stories.append({
            "id": s["id"], "title": s["title"], "segments": len(segs), "words": word_count(s),
            "slots": slots(s), "favourite_slots": s["text"].count("{FAV}"), "friend_slots": s["text"].count("{FRIEND}"),
            "files": [f"stories/{s['id']}/s{i:02d}.wav" for i in range(len(segs))],
            "segment_text": segs, "text": s["text"],
        })
    names = [{"name": n, "file": f"names/{name_slug(n)}.wav", "name_audio": False} for n in settings.get("names_for_audio") or []]
    # All prompts, whatever the capture mode: the card then works with either firmware build.
    prompts = [{"id": k, "text": v, "file": f"prompts/{k}.wav"} for k, v in PROMPTS.items()]
    return {
        "schema": "gadget-storypack/1",
        "language": settings.get("language", "en-IN"),
        "audio": audio,
        "audio_format": f"WAV, 16-bit PCM, mono, {SAMPLE_RATE} Hz" if audio else None,
        "voice": voice if audio else None,
        "tts_model": model if audio else None,
        "default_name": {"name": settings.get("default_hero", "little star"), "file": "names/_default.wav", "name_audio": False},
        "default_friend": {"name": settings.get("default_friend", "little bunny"), "file": "names/_friend.wav", "name_audio": False},
        "names": names,
        "prompts": prompts,
        "stories": stories,
    }


README_AUDIO = """Story card for {name}
========================

Copy everything in this folder to the root of a microSD card formatted as FAT32
(cards up to 32 GB are FAT32 out of the box). Then put the card in the gadget and restart it.

What is here
- stories.json: the six stories (text, and which audio file plays when).
- stories/<story>/sNN.wav: the story audio, one file per part. The hero's and the friend's names play between parts.
- names/: spoken name clips for the typed names, _default.wav (the hero when no favourite is saved) and
  _friend.wav (the friend when only one is saved).
- prompts/: short spoken instructions for the child.

The gadget keeps a fav/ folder. It stays empty unless this build records the child's own voice (an
opt-in setting); such clips are encrypted with a key kept only in the gadget, so they cannot be played
on a computer. Erase them from the parent page (Erase all favourites).

The audio was synthesised with {model} (voice {voice}). It is a computer voice, not a person.
Listen to a story yourself before bedtime use.
"""

README_TEXT = """Story card for {name}
========================

This card has the story TEXT but NO AUDIO yet. The gadget cannot read text aloud by itself, so until
the audio is added a tap plays a short low chime and the parent page says "no audio yet".

To finish the card:
1. In the Wish Labs studio, open this project's Code tab and choose "Generate story audio".
   (The build service makes the audio with a text-to-speech voice; it takes about a minute.)
2. Download the new story card folder and copy everything in it to the root of a FAT32 microSD card.
3. Put the card in the gadget and restart it.

What is here now
- stories.json: the six stories with their text, and the file names the audio will use.

The gadget keeps a fav/ folder. It stays empty unless this build records the child's own voice (an
opt-in setting); such clips are encrypted with a key kept only in the gadget.
"""


def write_pack(sd: Path, spec: dict, settings: dict, *, audio: bool = False,
               synth: Optional[Callable[[str, str, Path], None]] = None, voice: Optional[str] = None,
               model: Optional[str] = None, progress: Optional[Callable[[str, float], None]] = None) -> dict[str, Any]:
    """Write the card. synth(text, style, wav_path) produces one WAV (used when audio is True)."""
    sd.mkdir(parents=True, exist_ok=True)
    for sub in ("stories", "names", "prompts"):
        p = sd / sub
        if p.exists():
            for f in sorted(p.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            p.rmdir()
    index = build_index(settings, audio, voice, model)
    report: dict[str, Any] = {"audio": False, "stories": len(index["stories"]), "files": [], "missing": []}
    if audio and synth is not None:
        jobs: list[tuple[str, str, Path]] = []
        for st in index["stories"]:
            for i, text in enumerate(st["segment_text"]):
                jobs.append((text, STORY_STYLE, sd / st["files"][i]))
        name_style = NAME_STYLE
        jobs.append((index["default_name"]["name"], name_style, sd / index["default_name"]["file"]))
        jobs.append((index["default_friend"]["name"], name_style, sd / index["default_friend"]["file"]))
        for n in index["names"]:
            jobs.append((n["name"], name_style, sd / n["file"]))
        prompt_style = PROMPT_STYLE
        for pr in index["prompts"]:
            jobs.append((pr["text"], prompt_style, sd / pr["file"]))
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
        for n in [index["default_name"], index["default_friend"]] + index["names"]:
            n["name_audio"] = (sd / n["file"]).exists()
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


__all__ = ["write_pack", "load_stories", "segments", "slots", "word_count", "name_slug", "build_index", "PROMPTS",
           "STORY_STYLE", "NAME_STYLE", "PROMPT_STYLE",
           "MIN_WORDS", "MAX_WORDS"]
