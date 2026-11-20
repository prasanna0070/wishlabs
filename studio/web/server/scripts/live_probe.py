"""Probe Gemini Live on Vertex AI: list the Live models the project can see, then open one session, send one text
turn and report the audio and transcript that come back. Costs one short Live call.

  python web/server/scripts/live_probe.py                       # default model and location
  LIVE_MODEL=gemini-live-2.5-flash LIVE_LOCATION=global python web/server/scripts/live_probe.py
  python web/server/scripts/live_probe.py --list                # only list models whose name contains "live"
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project")
MODEL = os.environ.get("LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
LOCATION = os.environ.get("LIVE_LOCATION", "us-central1")


def _token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


def list_live(location: str) -> list[str]:
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    names: list[str] = []
    page = ""
    for _ in range(20):
        url = f"https://{host}/v1beta1/publishers/google/models?pageSize=200&listAllVersions=true" + (
            f"&pageToken={page}" if page else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}",
                                                   "x-goog-user-project": PROJECT})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        for m in d.get("publisherModels", []):
            n = m.get("name", "").rsplit("/", 1)[-1]
            if "live" in n or "native-audio" in n:
                names.append(f"{n} [{m.get('launchStage', '?')}]")
        page = d.get("nextPageToken") or ""
        if not page:
            break
    return sorted(set(names))


async def one_turn(model: str, location: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=PROJECT, location=location)
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede"))),
        system_instruction="You are a friendly product designer. Answer in one short sentence.",
    )
    t0 = time.time()
    audio = 0
    text = []
    first = None
    async with client.aio.live.connect(model=model, config=cfg) as s:
        await s.send_client_content(turns=types.Content(role="user", parts=[types.Part(text="Say hello in five words.")]),
                                    turn_complete=True)
        async for m in s.receive():
            sc = m.server_content
            if sc and sc.model_turn:
                for p in sc.model_turn.parts or []:
                    if p.inline_data and p.inline_data.data:
                        first = first or time.time() - t0
                        audio += len(p.inline_data.data)
            if sc and sc.output_transcription and sc.output_transcription.text:
                text.append(sc.output_transcription.text)
    return {"model": model, "location": location, "audio_bytes": audio, "audio_seconds": round(audio / 48000, 2),
            "first_audio_s": round(first or 0, 2), "total_s": round(time.time() - t0, 2), "transcript": "".join(text)}


def main() -> int:
    if "--list" in sys.argv or "--all" in sys.argv:
        for loc in ("us-central1", "global"):
            try:
                print(loc, json.dumps(list_live(loc), indent=1))
            except Exception as e:  # noqa: BLE001
                print(loc, "list failed:", repr(e)[:300])
        if "--list" in sys.argv:
            return 0
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or [MODEL]
    ok = True
    for m in models:
        try:
            print(json.dumps(asyncio.run(one_turn(m, LOCATION))))
        except Exception as e:  # noqa: BLE001
            ok = False
            print(json.dumps({"model": m, "location": LOCATION, "error": repr(e)[:400]}))
    return 0 if ok else 1


if __name__ == "__main__" and "--tool" not in sys.argv:
    sys.exit(main())


# ---- function-calling probe: python web/server/scripts/live_probe.py --tool [model ...]
async def tool_turn(model: str, location: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=PROJECT, location=location)
    fn = types.FunctionDeclaration(
        name="submit_wish", description="Hand the confirmed wish to the design agent.",
        parameters_json_schema={"type": "object", "properties": {
            "wish": {"type": "string"},
            "details": {"type": "array", "items": {"type": "object", "properties": {
                "question": {"type": "string"}, "answer": {"type": "string"}}}}},
            "required": ["wish"]})
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"], tools=[types.Tool(function_declarations=[fn])],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction="You take gadget wishes. When the user asks you to submit, call submit_wish at once, then "
                           "say one short closing sentence.")
    t0 = time.time()
    calls, text, audio = [], [], 0
    async with client.aio.live.connect(model=model, config=cfg) as s:
        await s.send_client_content(turns=types.Content(role="user", parts=[types.Part(
            text="I want a small cloud lamp for my desk that glows blue when it will rain tomorrow. USB powered. "
                 "That is everything, please submit it now.")]), turn_complete=True)
        for _ in range(3):
            async for m in s.receive():
                if m.tool_call:
                    for fc in m.tool_call.function_calls or []:
                        calls.append({"name": fc.name, "args": fc.args, "id": fc.id, "t": round(time.time() - t0, 2)})
                        await s.send_tool_response(function_responses=[types.FunctionResponse(
                            id=fc.id, name=fc.name, response={"ok": True, "next": "Say the design is starting."})])
                sc = m.server_content
                if sc and sc.model_turn:
                    for p in sc.model_turn.parts or []:
                        if p.inline_data and p.inline_data.data:
                            audio += len(p.inline_data.data)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    text.append(sc.output_transcription.text)
            if calls and audio:
                break
    return {"model": model, "calls": calls, "transcript": "".join(text), "audio_s": round(audio / 48000, 2),
            "total_s": round(time.time() - t0, 2)}


if __name__ == "__main__" and "--tool" in sys.argv:
    for m in [a for a in sys.argv[1:] if not a.startswith("--")] or [MODEL]:
        try:
            print(json.dumps(asyncio.run(asyncio.wait_for(tool_turn(m, LOCATION), 60)), default=str))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"model": m, "error": repr(e)[:400]}))
