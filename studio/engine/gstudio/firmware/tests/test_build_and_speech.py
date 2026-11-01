"""Build-log hygiene (MIN-19), the product / shared test split (MIN-18) and the speech round trip (MAJ-18)."""
from __future__ import annotations

import json
import wave
from pathlib import Path

from gstudio.firmware.build import parse_suites, scrub_paths
from gstudio.firmware.speech import SCOPE, check_card, roundtrip, wer, write_report
from gstudio.firmware.stories import write_pack

CORE = Path(__file__).resolve().parents[1] / "templates" / "common" / "lib" / "gs_core" / "src"


def test_logs_carry_no_machine_paths():
    fw = "/Users/someone/studio/web/server/_out/p/v3/firmware"
    log = (f"# project {fw}\n$ pio run\nCompiling /opt/gs/pio-shared/stage/0/proj/src/main.cpp\n"
           f"wrote {fw}/firmware-merged.bin\nhome /home/builder/.platformio\n")
    out = scrub_paths(log, [(fw, "."), ("/opt/gs/pio-shared/stage/0/proj", "<staging>")])
    assert "/Users/" not in out and "/home/builder" not in out
    assert "<staging>/src/main.cpp" in out and "./firmware-merged.bin" in out


def test_test_counts_split_product_and_shared():
    log = ("test/test_core/test_main.cpp:10:test_a:PASS\n"
           "test/test_net/test_main.cpp:11:test_b:PASS\n"
           "test/test_story_player/test_main.cpp:12:test_c:PASS\n"
           "test/test_story_player_clips/test_main.cpp:13:test_d:FAIL: Expected 1 Was 2\n"
           "test/test_net/test_main.cpp:14: test_e\t[IGNORED]\n")
    s = parse_suites(log)
    assert s["shared"] == {"passed": 2, "total": 2, "ignored": 1, "suites": ["test_core", "test_net"]}
    assert s["product"]["passed"] == 1 and s["product"]["total"] == 2
    assert s["product"]["suites"] == ["test_story_player", "test_story_player_clips"]


def test_wer():
    assert wer("The tree began to hum.", "the tree began to hum") == 0.0
    assert wer("one two three four", "one two four") == 0.25
    assert wer("a b", "") == 1.0
    # a disclosed spelling variant of the same spoken word is not an error
    assert wer("Chuk-chuk, chuk-chuk, went the train.", "Chug chug chug chug went the train") == 0.0


def _wav(path: Path, rate=24000, n=2400, ch=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * n * ch)


def test_card_check_and_round_trip_with_a_fake_transcriber(tmp_path):
    sd = tmp_path / "sdcard"
    write_pack(sd, {"name": "x"}, {"default_hero": "little star"}, audio=True,
               synth=lambda text, style, p: _wav(p), voice="Achernar", model="tts-test")
    idx = json.loads((sd / "stories.json").read_text())
    card = check_card(sd, CORE, tmp_path / "tools")
    assert card["ok"] and card["failed"] == 0 and card["files"] > 30
    texts = {}
    for st in idx["stories"]:
        for f, t in zip(st["files"], st["segment_text"]):
            texts[f] = t
    for n in [idx["default_name"], idx["default_friend"]]:
        texts[n["file"]] = n["name"]
    for p in idx["prompts"]:
        texts[p["file"]] = p["text"]

    def stt(path: Path) -> str:  # a perfect transcriber, except one dropped word in one segment
        rel = str(path.relative_to(sd))
        t = texts[rel]
        return " ".join(t.split()[1:]) if rel == "stories/mango_tree/s00.wav" else t

    trip = roundtrip(sd, stt, "stt-test", card["rows"])
    assert trip["scope"] == SCOPE == "synthetic voice, no speaker or child tested"
    seg = [r for r in trip["rows"] if r["kind"] == "segment"]
    assert all(r["wer"] <= 0.15 for r in seg) and trip["ok"]
    assert all(r["voice"] == "Achernar" and r["tts_model"] == "tts-test" and r["stt_model"] == "stt-test" for r in trip["rows"])
    rep = write_report(tmp_path, card, trip)
    on_disk = json.loads((tmp_path / "speech-roundtrip.json").read_text())
    assert rep["ok"] and on_disk["ok"] and on_disk["wav_check"]["files"] == card["files"]


def test_bad_wav_and_failed_transcription_fail_the_check(tmp_path):
    sd = tmp_path / "sdcard"
    write_pack(sd, {"name": "x"}, {}, audio=True, synth=lambda text, style, p: _wav(p), voice="v", model="m")
    _wav(sd / "stories" / "mango_tree" / "s01.wav", rate=16000)  # wrong rate
    card = check_card(sd, CORE, tmp_path / "tools")
    bad = [r for r in card["rows"] if not r["ok"]]
    assert not card["ok"] and len(bad) == 1 and "sample_rate 16000" in bad[0]["error"]

    def broken(path):
        raise RuntimeError("quota")

    trip = roundtrip(sd, broken, "stt-test", card["rows"])
    assert not trip["ok"] and "transcription failed" in trip["rows"][0]["error"]
