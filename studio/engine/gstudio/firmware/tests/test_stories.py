from __future__ import annotations

import json
import re
import wave

from gstudio.firmware.stories import (MAX_WORDS, MIN_WORDS, PROMPTS, load_stories, name_slug, segments, slots, word_count,
                                      write_pack)


def test_six_original_stories_of_the_right_length():
    stories = load_stories()
    assert len(stories) == 6
    assert len({s["id"] for s in stories}) == 6
    for s in stories:
        assert MIN_WORDS <= word_count(s) <= MAX_WORDS, (s["id"], word_count(s))
        assert s["text"].count("{FAV}") >= 2
        assert s["text"].count("{FRIEND}") >= 1  # MAJ-33: a hero and a friend
        assert len(slots(s)) == len(segments(s)) - 1
        assert re.fullmatch(r"[a-z0-9_\-]{1,23}", s["id"])  # firmware StoryCatalog rule
        assert all(c.isascii() for c in s["text"])


def test_name_slots_start_sentences_so_clips_fit_natural_pauses():
    for s in load_stories():
        segs = segments(s)
        assert 2 <= len(segs) <= 12
        assert segs[0].startswith(s["title"] + ".")
        assert all(segs), "no empty segment"
        for before in segs[:-1]:
            assert before.rstrip()[-1] in '.!?"', f"{s['id']}: a name must start a sentence"


def test_slug_matches_the_firmware_rule():
    assert name_slug("Anne-Marie D'Souza") == "anne_marie_dsouza"
    assert name_slug("Little Star") == "little_star"
    assert name_slug("  Kabir  ") == "kabir"


def test_text_only_pack_is_deterministic_and_honest(tmp_path):
    spec = {"name": "Luna Owl"}
    settings = {"language": "en-IN", "default_hero": "little star", "names_for_audio": ["Anya"]}
    a = write_pack(tmp_path / "a", spec, settings)
    b = write_pack(tmp_path / "b", spec, settings)
    ja = (tmp_path / "a" / "stories.json").read_bytes()
    assert ja == (tmp_path / "b" / "stories.json").read_bytes()
    idx = json.loads(ja)
    assert idx["audio"] is False and a["audio"] is False
    assert idx["stories"][0]["files"][0] == f"stories/{idx['stories'][0]['id']}/s00.wav"
    assert idx["names"] == [{"name": "Anya", "file": "names/anya.wav", "name_audio": False}]
    readme = (tmp_path / "a" / "README.txt").read_text()
    assert "NO AUDIO yet" in readme and "Generate story audio" in readme


def test_audio_pack_writes_one_wav_per_segment(tmp_path):
    def fake_synth(text, style, path):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 240)

    rep = write_pack(tmp_path, {"name": "Luna Owl"}, {"default_hero": "little star"}, audio=True, synth=fake_synth,
                     voice="Achernar", model="test-model")
    idx = json.loads((tmp_path / "stories.json").read_text())
    assert rep["audio"] and idx["audio"]
    for st in idx["stories"]:
        for f in st["files"]:
            assert (tmp_path / f).exists(), f
    assert (tmp_path / "names" / "_default.wav").exists()
    assert (tmp_path / "names" / "_friend.wav").exists()
    assert idx["default_name"]["name_audio"] and idx["default_friend"]["name_audio"]
    assert "computer voice" in (tmp_path / "README.txt").read_text()
    # MIN-38: the four spoken prompts of the voice-clip flow (and hello), each a WAV the firmware parses.
    from gstudio.firmware.speech import wav_check_python

    for p in ("hold_to_tell", "tap_to_keep", "saved", "try_again", "hello"):
        f = tmp_path / "prompts" / f"{p}.wav"
        assert f.exists(), p
        assert wav_check_python(f)["ok"], p
    assert [p["id"] for p in idx["prompts"]] == list(PROMPTS)


def test_index_carries_slot_kinds_for_the_firmware():
    from gstudio.firmware.stories import build_index

    idx = build_index({"capture_mode": "parent_web"}, False, None, None)
    for st in idx["stories"]:
        assert st["slots"] and set(st["slots"]) == {"FAV", "FRIEND"}
        assert len(st["slots"]) == st["segments"] - 1
    assert idx["default_friend"] == {"name": "little bunny", "file": "names/_friend.wav", "name_audio": False}


def test_failed_audio_is_reported_not_hidden(tmp_path):
    def broken(text, style, path):
        raise RuntimeError("quota")

    rep = write_pack(tmp_path, {"name": "x"}, {}, audio=True, synth=broken, voice="v", model="m")
    idx = json.loads((tmp_path / "stories.json").read_text())
    assert rep["audio"] is False and idx["audio"] is False and idx["audio_missing"]
    assert "failed" in (tmp_path / "README.txt").read_text()
