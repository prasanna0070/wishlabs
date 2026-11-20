# Gadget sound: every gadget gets its own sound (Lyria on Vertex AI)

Each design gets a short theme composed by Lyria on Vertex AI from its own spec, and a signature chime cut from that
theme. The owner sees a Sound strip under the 3D view (Preview tab); read-only share links show the same sound, play
only. Code: `web/server/app/sound.py` (prompt, Lyria client, audio cuts, limits), `web/server/app/routes/sound.py`
(HTTP), `web/app/src/components/panel/SoundCard.tsx` + `web/app/src/lib/sound.ts` (UI).

## What happens

1. **Prompt.** `prompt_for(spec, project)` writes an instrumental music prompt from the design: its name and tagline,
   the behaviour template (story player, desk companion, ambient light, notifier clock, sensor display: each has its
   own mood, instruments and tempo), the audience (child-directed designs get gentle, soothing music and a stricter
   negative prompt), the body colour (hue and lightness become tone-colour words, with the colour names) and an LED
   shimmer when the design has an LED. The exact text sent is stored and shown in the UI. If Lyria refuses the prompt,
   it retries once with a general prompt that carries no user-written text (`prompt_source: "safe_template"`).
2. **Lyria.** The main model is **Lyria 3 Clip** (`lyria-3-clip-preview`, Public Preview) through Vertex AI
   `generateContent` on `global`: about 13 to 16 s for one 28 to 30 s MP3 (44.1 kHz, 192 kbps) plus two text parts,
   the lyrics (`<instrumental>`) and a caption in which Lyria describes what it composed. It takes no negative prompt,
   so the things to avoid are appended to the prompt ("Avoid vocals, singing, ..."). If it fails (any error, timeout or
   unreadable audio), the **fallback** **Lyria 2** (`lyria-002`, GA) composes instead, through the Vertex AI `:predict`
   endpoint in `us-central1`: about 28 s for one ~32.8 s clip of 48 kHz stereo 16-bit WAV (base64), with the negative
   prompt. Measured 2026-11-20 from the dev VM: Lyria 3 Clip 12.4 s, 13.3 s and 15.5 s; Lyria 2 27.7 s and 29.0 s.
3. **Cuts** (`compose`, about 0.3 s): the theme (leading silence trimmed, at most 30 s, faded out, loudness capped),
   the chime (2.4 s from the strongest musical onset: spectral flux weighted by how loud the next 0.8 s is, faded),
   a device chime (the same onset as 16 kHz mono 16-bit WAV, 2.0 s with its own fade: 64,044 bytes, within the 64 KB
   the firmware's clip upload takes) and 64 waveform peaks for the UI.
   Theme and chime are MP3 (lameenc, 128 kbps: about 450 to 480 KB and 39 KB); without lameenc they fall back to
   WAV. Lyria 3's MP3 is read with miniaudio.
4. **Storage.** `projects/<pid>/sound/v<n>/<sid>/` in the project store: `theme.mp3`, `chime.mp3`,
   `device-chime.wav`, the model output (`lyria.mp3` as it came, or `lyria.wav` unwrapped) and `sound.json` (with the
   model's full text). The folder sits beside the version
   folders, so the copies that seed a new version never carry it into that version's export.
5. **Project JSON.** `project.sound = {status, job, error, current, history (5), count}`; `current` has the prompt as
   sent, the negative prompt (Lyria 2 only), model, location, seed (Lyria 2), Lyria 3's caption, files (URL, seconds,
   bytes, peaks, chime onset), timings, `fallback_from` / `fallback_reason` when the fallback composed it, and the
   cost estimate. Each composition is also one usage turn (`action: "sound"`, `label: "Gadget sound (Lyria)"`) whose
   `calls` (`kind: "music"`) are the Lyria calls that returned audio, in `project.usage`, which the AI receipts read.

## When it runs

- **By itself once,** right after a design's first successful build (`maybe_auto`, called from `BuildManager.finalize`):
  only when the project has no sound state yet and no other version is done. It never runs again by itself.
- **On the owner's button**: "Compose its sound" (no sound yet) or "Compose again" in the Preview tab.

Projects built before this feature (the demo project included) have no sound until someone presses the button.

## Cost and limits (it spends money, so it fails closed)

- Estimates per call that returned audio (`cost_usd_est`, not billing records): Lyria 3 Clip USD 0.04 per 30 s clip
  (the Gemini API price list; the Vertex AI price of this preview model was not confirmed), Lyria 2 USD 0.06 per 30 s
  of music. A normal composition is one Lyria 3 call (USD 0.04); with the fallback, up to USD 0.10.
- One composition at a time per project (409 while one runs; a job older than 5 min counts as dead).
- Per signed-in browser session: `SOUND_SESSION_HOURLY` compositions per hour (default 5; in memory, per instance, so
  up to 3x with 3 instances).
- Whole service: `SOUND_DAILY_CAP` compositions per UTC day (default 40, about USD 2.40), counted in the store at
  `sound/budget/<day>.json` before any model call. Cap reached: 429. Store unreadable: 503. No Lyria call either way.
- Switched off (`SOUND=0`, or the scripted agent without `SOUND=1`): 503, the button is hidden, no automatic run.

## HTTP

| Route | Auth | What |
|---|---|---|
| `GET /api/projects/{pid}/sound` | studio login | `{enabled, status, sound, model, cost_usd_est, session_hourly, session_left}` |
| `POST /api/projects/{pid}/sound` | studio login | compose now; body `{"version": n}` optional (default: the current version if done, else the latest done one). 202, or 409 / 429 / 503 |
| `GET /api/projects/{pid}/sound/{sid}/{name}` | studio login | `theme.mp3`, `chime.mp3`, `device-chime.wav`, `lyria.wav`; HTTP Range (206) for `<audio>`; `?download=1` |
| `GET /api/share/{token}/sound/{sid}/{name}` | share token | the same files, read-only. `GET /api/share/{token}` rewrites the URLs in `project.sound` to these |
| `GET /api/device/{tok}/chime.wav` | device token | the current device chime (16 kHz mono 16-bit WAV, 2 s, 64,044 bytes), CORS open like name-audio, 60 requests/hour per token shared with name-audio |

The share link cannot compose, retry or change anything: there is no share route for it.

## Settings (environment)

| Variable | Default | |
|---|---|---|
| `SOUND` | on when `AGENT=gemini` | `0` switches the feature off |
| `LYRIA_MODEL` | `lyria-3-clip-preview` | main model; `lyria-3*` uses `generateContent`, anything else `:predict` |
| `LYRIA_LOCATION` | the model's own | `global` for Lyria 3 Clip; `us-central1` for `lyria-002` (not served on `global`) |
| `LYRIA_FALLBACK` | `lyria-002` | tried once when the main model fails; empty: no fallback |
| `LYRIA_TIMEOUT_S` | 120 | per HTTP attempt; 3 attempts on 429/5xx/network errors |
| `SOUND_DAILY_CAP` | 40 | compositions per UTC day, whole service |
| `SOUND_SESSION_HOURLY` | 5 | compositions per browser session per hour |
| `SOUND_CHIME_S` | 2.4 | chime length, 1 to 3 s |

`deploy/web.sh` needs no change: the defaults are right for production. The web image gains three wheels from
`web/server/requirements.txt` (`numpy`, `lameenc`, `miniaudio`; cp312 manylinux wheels checked on 2026-11-20). The web
service account (`gstudio-sa`) already has `roles/aiplatform.user`, which covers both `:predict` and
`generateContent`; the dev VM's service account made the test calls in the same project.

### Lyria models (checked 2026-11-20)

The Model Garden in `us-central1` lists `lyria-002` (GA), `lyria-3-clip-preview` and `lyria-3-pro-preview` (Public
Preview). Google's model page says the Lyria 3 models run on `global`, return MP3 (44.1 kHz, 192 kbps; Clip: 30 s,
Pro: up to 184 s), have an instrumental mode and no negative prompt. Lyria 3 Pro writes whole songs; a 30 s theme only
needs Clip. To go back to Lyria 2 only: `LYRIA_MODEL=lyria-002` (and `LYRIA_FALLBACK=` to drop the fallback).

## The gadget (device tie-in)

The firmware does not play the chime yet. What exists and what is missing:

- **Exists:** `GET /api/device/<token>/chime.wav` serves a WAV in the format the firmware takes for spoken name clips
  (16-bit mono WAV, at most 64 KB, through `POST /api/name_clip`): 16 kHz, 2.0 s, 64,044 bytes. The firmware already plays WAV files from
  the microSD card with `gsaudio::play_file(path)` (`engine/gstudio/firmware/templates/common/src/gs_audio.h`), and
  the parent page (`templates/common/data/parent.html`) already fetches clips from the server
  (`name_audio_url = PUBLIC_URL/api/device/<token>/name-audio`) and uploads them to the gadget (`POST /api/name_clip`).
- **Missing (the hook):** (1) a boot chime in `templates/common/src/main.cpp`, after `s_app->begin(millis())`:
  `if (!gsaudio::busy()) gsaudio::play_file("/chime.wav");` (designs with a speaker and a card; `play_file` returns
  false without them); (2) a way onto the card: copy `device-chime.wav` to the card root as `chime.wav` by hand, or a
  `POST /api/boot_chime` upload target in `gs_net.cpp` that the parent page fills from `/api/device/<token>/chime.wav`
  the way it fills name clips. Both need the firmware native tests and an on-device check before the UI may claim that
  the gadget plays its chime.

## Tests

- `web/server/tests/test_sound.py` (Lyria mocked, 19 tests): prompt and its Lyria 3 form, nested-RIFF WAV and MP3
  input, the cuts (onset found within 60 ms, 80 ms through MP3; device chime format and size; fade), HTTP ranges,
  owner/share/device routes, 409/429/503/401/404, usage records, failure, refusal and fallback paths, Lyria 3's
  caption, the automatic run after the first build only.
- `web/app/src/lib/sound.test.ts` (vitest): the helpers.
- `web/server/scripts/lyria_probe.py models|generate`: lists Lyria models; one real composition (about USD 0.06).
- `web/server/scripts/sound_e2e.py` against a running server with the real model: build, automatic sound, owner and
  share playback, device chime. `web/server/scripts/sound_ui_check.cjs`: Playwright check and screenshots of the strip
  (owner) and the share page (desktop and phone).
