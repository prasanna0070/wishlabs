"""Dev probe (VM only, makes Vertex calls): synthesise a few story segments with a TTS style, transcribe them and
print the word error rate. Used to choose the story-card style line (MAJ-18).

    python -m gstudio.firmware.tests.tts_probe <out_dir> [style_key ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

from gstudio.firmware.speech import GeminiStt, wer
from gstudio.firmware.stories import STORY_STYLE, load_stories, segments
from gstudio.firmware.tts import GeminiTts

STYLES = {
    "current": STORY_STYLE,
    "old": ("Read this part of a bedtime story for a young child in India, in a warm, soft, slow Indian English voice. "
            "Gentle pauses at full stops. It continues straight after a child's name."),
}
PICK = [("night_train", 2), ("little_diya", 2), ("mango_tree", 1), ("houseboat_moon", 3), ("monsoon_cloud", 3)]


def main() -> int:
    out = Path(sys.argv[1])
    keys = sys.argv[2:] or ["current"]
    out.mkdir(parents=True, exist_ok=True)
    tts = GeminiTts(voice="Achernar", language="en-IN", cache_dir=out / "tts-cache")
    stt = GeminiStt(cache=out / "stt-cache")
    stories = {s["id"]: s for s in load_stories()}
    worst = 0.0
    for key in keys:
        for sid, i in PICK:
            text = segments(stories[sid])[i]
            p = out / f"{key}_{sid}_{i}.wav"
            tts.synth(text, STYLES[key], p)
            hyp = stt(p)
            w = wer(text, hyp)
            worst = max(worst, w)
            print(f"{key:8} {sid}:{i:<2} wer={w:.3f} ref={text[:60]!r} hyp={hyp[:80]!r}", flush=True)
    print(f"tts model {tts.model}; worst {worst:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
