# Gadget Studio web API (v1.1)

Owner: web-backend (`web/server/`). Consumer: web-frontend (`web/app/`). Server: FastAPI app `web/server/app`
(ASGI entry `app.asgi:app`), served by `gstudio-web` on Cloud Run. The SPA is served by the same origin, so every
call is same-origin and the session cookie travels automatically.

**Changes in v1.1 (2026-11-01, from a live benchmark tour, `docs/ref/benchmark-ui.md` §4-§5):**
- clarify: one round of questions; chips are optional (0-4); a free-text box is always allowed; the agent discloses
  `defaults` instead of asking; answers can be skipped (§3).
- concepts: 6 per round, 2 per `tier` (`expected`, `different`, `wildcard`), rendered in parallel; "Continue without
  an image" (`skip_concepts`) (§2.1, §3).
- brief: every fact is `{value, source: "user"|"suggested", evidence}`; new `summary` and `details[]` (§2.1).
- part-scoped edit: `POST /api/projects/{pid}/parts/{component}/edit` (§5.4).
- during builds: `status.draft_url` (live 3D draft GLB) and `status.messages[]` (plain-English progress lines), plus a
  `build.message` SSE event (§3.1, §4).
- `project.usage`: tokens and estimated cost per turn (§2.1). `project.renamed` event; `name_source`.

Conventions:
- JSON bodies, UTF-8. Times are ISO-8601 UTC strings with a `Z` suffix (`2026-11-01T02:03:50Z`).
- Ids: project `p_<12>`, chat message `m_<10>`, concept `c_<10>`, order request `o_<10>`, share token `s_<24>`.
- Errors: non-2xx with `{"error": {"code": "<slug>", "message": "<human sentence>", "details": {...}}}`.
  Codes: `unauthorized` (401), `not_found` (404), `conflict` (409, e.g. a turn or build is already running),
  `invalid` (422; `details.errors` is a list of strings), `rate_limited` (429), `unavailable` (503).
- Every `/api/*` route needs the session cookie except `POST /api/login`, `GET /api/health` and `/api/share/*`.
  Non-`/api` paths serve the SPA (`index.html` fallback for client-side routes), no auth.
- Interactive schema: `/api/docs` (OpenAPI; bodies are free-form JSON there, this file is the contract).

## 1. Auth

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/login` | `{"key": "<access key>"}` | 200 `{"ok": true, "expires": "<iso>"}` + `Set-Cookie: gs_session=...; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000` (+ `Secure` on https). 401 on a bad key. 429 after 10 bad keys from one IP in 10 minutes. |
| POST | `/api/logout` | none | 200 `{"ok": true}`, cookie cleared |
| GET | `/api/me` | none | 200 `{"authenticated": true, "expires": "<iso>"}` or 401 |
| GET | `/api/session` | none | always 200: `{"authenticated": true, "expires": "<iso>"}` or `{"authenticated": false, "expires": null}`; `Cache-Control: no-store`. The SPA's start-up probe, so a signed-out visitor gets no 401 in the browser console. Adds `"share_url"` only when `SHARE_URL` is set, and `"voice": false` only when `LIVE_ENABLED=0` (the SPA then hides its Talk buttons). |
| GET | `/api/health` | none | 200 `{"ok": true, "version": "0.1.0", "builder": "remote\|local\|fake", "agent": "gemini\|scripted", "store": "gcs\|local", "engine": true}` |

The key comes from env `ACCESS_KEY` (Cloud Run maps Secret Manager `gstudio-access-key` into it), or is read from
Secret Manager directly when the env is unset. The cookie is `v1.<expiry>.<nonce>.<hmac-sha256>`; the HMAC key is
derived from the access key, so rotating the key logs everyone out. Treat any 401 from `/api/*` as "show login".

## 2. Projects

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/projects` | none | `{"projects": [ProjectSummary]}`, newest `updated` first |
| POST | `/api/projects` | `{"name"?: str, "mode"?: "pro"\|"fast"}` | 201 `Project` (stage `clarify`, empty chat). Then send the prompt with `POST .../chat`. |
| GET | `/api/projects/{pid}` | none | `Project` (finalizes a finished build first, see §4 Reconcile) |
| PATCH | `/api/projects/{pid}` | `{"name"?: str, "mode"?: "pro"\|"fast"}` | `Project`. A rename sets `name_source: "user"`; the agent never renames after that. |
| DELETE | `/api/projects/{pid}` | none | `{"ok": true}` (soft delete) |
| PATCH | `/api/projects/{pid}/checklist` | `{"assembly"?: {step_id: bool\|null}, "wiring"?: {...}}` (merged; null removes) | `{"checklist": {...}}` |
| PUT | `/api/projects/{pid}/brief` | `{"brief": {...fields to change}}` | `Project`; a new brief version, unapproved; edited facts become `source: "user"` |

`ProjectSummary` = `{id, name, created, updated, stage, current_version, thumb_url}`. `thumb_url` is the current
version's `manual/assembled.png` when done, else the selected concept's photo (or render), else `null`.

### 2.1 `Project` (`gs://$BUCKET/projects/<pid>/project.json`, schema `gstudio-web-project/1`)

```jsonc
{
  "schema": "gstudio-web-project/1",
  "id": "p_7h3k2m9q4x1z",
  "name": "Bedtime Story Robot",
  "name_source": "agent",                  // auto (from the first prompt) | agent (named by the agent once) | user
  "created": "2026-11-01T01:58:34Z",
  "updated": "2026-11-01T02:22:46Z",
  "stage": "clarify",                      // clarify | concepts | brief | building | ready | error
  "mode": "pro",                           // pro = gemini-3.1-pro-preview, fast = gemini-3.5-flash
  "turn": null,                            // {"id","started"} while an agent turn runs, else null
  "questions_asked": 2,                    // capped at 3 over the whole project
  "chat": [ChatMessage],
  "concepts": [Concept],                   // every concept ever proposed (each concepts message lists its own ids)
  "selected_concept": "c_8ff736dbac",
  "brief": Brief,                          // latest brief version, or null
  "brief_history": [Brief],
  "versions": [Version],                   // oldest first
  "current_version": 3,                    // what the UI shows; undo/redo move it
  "checklist": {"assembly": {"print-shell": true}, "wiring": {}},
  "order_requests": [OrderRequest],
  "share": {"token": "s_...", "created": "..."},
  "usage": {                               // null until the first model call
    "basis": "Token counts from Vertex AI usage metadata. cost_usd_est uses configured per-unit rates ... not billing data.",
    "turns": [{"turn_id": "t_...", "ts": "...", "action": "text", "mode": "pro",
               "calls": [{"model": "gemini-3.1-pro-preview", "kind": "text", "input_tokens": 21000, "output_tokens": 900,
                          "thinking_tokens": 1500, "cached_tokens": 0, "images": 0, "seconds": 14.2, "cost_usd_est": 0.0708}],
               "input_tokens": 21000, "output_tokens": 900, "thinking_tokens": 1500, "cached_tokens": 0, "images": 0,
               "cost_usd_est": 0.0708}],    // last 200 turns
    "totals": {"turns": 4, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0,
               "images": 6, "cost_usd_est": 0.0}
  }
}
```

`ChatMessage` = `{id, role: "user"|"assistant", kind, text, payload, ts}`. `text` is always present (plain text with
light Markdown: bold, bullets). Kinds and payloads:
- `text`: user payload `null` or `{"action": "...", ...}` (see §3; `select_concept` adds `concept_id` and
  `image_url` so the bubble can show the chosen image above "My choices: Moon Hug"; `edit_part` adds
  `component`, `label`, `spec_path`); assistant payload `null`.
- `choices`: `{"questions": [Question], "defaults": ["Country: India (default)"], "answered": false, "answers"?: [...], "skipped"?: bool, "free_text"?: str}`.
  `Question` = `{id, question, consequence, options: [0..4 strings], allow_free_text}`. Show the chips (if any) and
  always a free-text box. `text` is the lead sentence plus the consequences (benchmark style).
- `concepts`: `{"concept_ids": [...6], "tiers": {"expected": [id, id], "different": [...], "wildcard": [...]}, "selected": null, "defaults": [...]}`.
  Render three labelled groups (Expected / Different ideas / Wildcards), then "Continue without an image" and a
  free-text "Other..." box (plain chat text).
- `brief`: `{"brief_version": 2}` (the brief is `project.brief` or `brief_history[v-1]`). Show **Build this** only on
  the latest brief while `stage == "brief"`.
- `progress`: `{"version": 3, "status": Status}`, one per build, updated in place; its final `status.messages` are
  the build's commentary lines.
- `result`: `{"version": 3, "summary": BuildSummary}`; `text` ends with "Physical checks still needed:" (§5.3).
- `tweak` (role user): `{"version": 4, "patch": {...}, "scale": 1.05, "base_version": 3}`.
- `error`: `{"code": "...", "retryable": true, "version"?: n}`.

`Concept`:

```jsonc
{
  "id": "c_8ff736dbac",
  "title": "Moon Hug",
  "tier": "expected",                      // expected | different | wildcard
  "blurb": "A crescent moon that hugs a round face; the story button is its belly.",
  "spec": {/* gadget-spec/1 as the agent wrote it (validated) */},
  "render_url": "/api/projects/p_.../concepts/c_.../render.png",   // CAD preview render of this spec
  "photo_url": "/api/projects/p_.../concepts/c_.../photo.png",     // product photo of exactly that render (png/jpg/webp), or null
  "glb_url": "/api/projects/p_.../concepts/c_.../preview.glb",     // shell-only preview GLB, or null
  "state": "ready",                       // rendering | ready | failed
  "warnings": [],
  "photo_note": null                      // e.g. "Photo generation failed; showing the CAD render."
}
```

Image `alt` should be `"<title>: <blurb>"`. Show `photo_url` when present, else `render_url`.

`Brief` (provenance per fact; Size and Parts are computed by the server, never written by the model):

```jsonc
{
  "version": 2, "concept_id": "c_8ff736dbac", "title": "Moon Hug",
  "summary":        {"value": "A bedside robot that tells short offline stories...", "source": "suggested", "evidence": "From your request"},
  "purpose":        {"value": "...", "source": "user", "evidence": "You chose 5 yo old"},
  "size":           {"value": "About 156 W x 120 D x 182 H mm", "w_mm": 156, "d_mm": 120, "h_mm": 182,
                     "source": "suggested", "evidence": "Body envelope computed from the design geometry; ..."},
  "functions":      [{"title": "Bedtime stories", "value": "Tap the big button for...", "source": "user", "evidence": "You asked for bedtime stories"}],
  "interaction":    {"value": "...", "source": "suggested", "evidence": "..."},
  "privacy_safety": {"value": "...", "source": "suggested", "evidence": "..."},
  "power":          {"value": "USB-C 5 V from a certified adapter...", "source": "suggested", "evidence": "..."},
  "details":        [{"topic": "Sourcing country", "value": "India", "source": "suggested", "evidence": "Default; can be changed"}],
  "parts":          [{"module_id": "SPK_40MM_4R_3W", "name": "...", "role": "speaker", "qty": 1, "implied": false,
                      "source": "suggested", "evidence": "From the parts catalog"}],
  "limits":         [{"value": "Stories are pre-written, not made up live.", "source": "suggested", "evidence": "..."}],
  "safety_flags": ["CHILD_DIRECTED"],
  "spec": {/* the gadget-spec/1 this brief builds */},
  "created": "...", "approved": false, "approved_at": null, "edited_by_user"?: true
}
```

Section order for the review card: Summary, Purpose, Size, Function 1..n, Interaction, Privacy & safety, Power,
Details, Parts, Limits. Badge `source` ("From you" / "Suggested") and show `evidence` on hover or expand.

`Version` = `{n, created, label, source: "build"|"update"|"tweak"|"restore", state: "queued"|"running"|"done"|"error",
summary, spec_hash, brief_version, base_version, restored_from?, percent, error, started, ended, finalized, thumb}`.

`OrderRequest` = `{id, created, version, pincode, quantity, note, status: "requested", status_text, quote}`.

## 3. Chat turns (SSE)

`POST /api/projects/{pid}/chat` streams one agent turn as Server-Sent Events. `EventSource` cannot POST, so read it
with `fetch` and a stream reader:

```ts
const res = await fetch(`/api/projects/${pid}/chat`, {method: "POST",
  headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
if (!res.ok) { const e = await res.json(); /* 401 / 404 / 409 / 422 with {error} */ }
const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
// split on "\n\n"; each block has "event: <type>" and "data: <json>"; lines starting with ":" are pings.
```

Request body, exactly one of:

| Body | Meaning | Stored user message text |
|---|---|---|
| `{"text": "I want a cute robot..."}` | free text: first prompt, a typed answer, an edit request, a question | the text |
| `{"text": "my friend made this", "attachments": [{"id": "u_..."}]}` | a message with up to 4 of the project's uploaded images (§7b); `text` may be empty when there is an image | the text, or `Here is what I have in mind.`; the message gains `attachments` |
| `{"answers": [{"question_id": "age", "value": "5 yo old"}, {"question_id": "voice", "value": "yes"}]}` | chip or typed answers to the latest `choices` message (`value` may be free text) | `My choices:\n5 yo old, yes` |
| `{"answers": [], "skipped": true}` | skip the questions, use the agent's defaults (`answers` may be partial with `skipped: true`) | `My choices:\nUse your suggestions` |
| `{"action": "select_concept", "concept_id": "c_..."}` | pick a concept | `My choices:\nMoon Hug` |
| `{"action": "skip_concepts"}` | Continue without an image (the agent starts from the first Expected concept) | `My choices:\nContinue without an image` |
| `{"action": "approve_brief"}` | **Build this** (builds the latest brief; no model call) | `My choices:\nBuild this` |
| `{"action": "regenerate_concepts"}` | fresh concepts | `Show me different concepts` |
| `{"action": "retry_build"}` | rebuild the current version after an error | `Try the build again` |
| `{"action": "edit_part", "component": "PURCHASE_StoryButton", "instruction": "move it up a little"}` | part-scoped edit (same as §5.4) | `Edit Story button: move it up a little` |

Optional on any body: `"mode": "pro"|"fast"` (sticks to the project) and `"follow_build": true` (default: after a
build starts, keep relaying its progress on this stream until it ends). 409 `conflict` while another turn runs. The
turn runs server-side as a task: if the client disconnects it still completes and its messages are saved.

### 3.1 Events

Every `data` is JSON with a `seq` that increases by 1 within a stream.

| event | data | When |
|---|---|---|
| `turn.start` | `{seq, turn_id, started, user_message: ChatMessage}` | first event (start the "Working for 0m 1s" timer from `started`) |
| `status` | `{seq, text}` | activity line: "Thinking", "Checking the concepts against the parts catalog", "Rendering 6 concepts", "Building" |
| `project.renamed` | `{seq, name}` | the agent named the project (first reply only, never after a user rename) |
| `message.start` | `{seq, message_id, role: "assistant", kind: "text"}` | streamed assistant text begins |
| `text.delta` | `{seq, message_id, delta}` | append to that message |
| `thinking.delta` | `{seq, text}` | a thought summary from the design agent (Gemini `include_thoughts`): show it in the turn's "Show thinking" panel, never in a message; it is not stored in the chat |
| `message.done` | `{seq, message: ChatMessage}` | a message is final and stored (any kind). Replace drafts by id. |
| `tool.start` | `{seq, call_id, name, label}` | `name` in `ask_user, propose_concepts, write_brief, build_design, update_design, say, record_image_brief` |
| `tool.done` | `{seq, call_id, name, ok, summary, errors?}` | |
| `repair` | `{seq, round, errors}` | a spec failed validation and the model is fixing it (rounds 1..3) |
| `choices` | `{seq, message}` | clarifying questions (also sent as `message.done`) |
| `concepts.progress` | `{seq, concept_id, index, title, tier?, state, note?}` | per concept: `rendering`, `photo`, `ready`, `failed` (all 6 in parallel) |
| `concepts` | `{seq, message, concepts: [Concept]}` | concepts ready to pick |
| `brief` | `{seq, message, brief}` | brief ready for review |
| `image_brief` | `{seq, ...ImageBrief, image_brief: ImageBrief}` | the agent read the user's images (§7b); also stored as `project.image_brief` |
| `build.started` | `{seq, version, message}` | a build was queued (`message` is the `progress` message) |
| `build.progress` | `{seq, version_n, status: Status}` | status changed (stage, percent, a new draft GLB, a new line) |
| `build.message` | `{seq, version_n, text, ts?, stage?, derived?}` | one plain-English progress line; show as a chat paragraph |
| `result` | `{seq, version, message}` | build finished; `message.kind` is `result` (done) or `error` |
| `project` | `{seq, project}` | full snapshot, once at the end of every turn |
| `error` | `{seq, code, message, retryable}` | the turn failed; an `error` chat message is also stored |
| `done` | `{seq}` | last event |

A `: ping` comment is sent every 15 s while idle.

### 3.2 Project event stream

`GET /api/projects/{pid}/stream` is an `EventSource`-compatible GET stream. It sends `project` first, then
`build.progress`, `build.message` and `result` for every unfinished version, a final `project`, and `done`. Open it
after a tweak, a restore, or a page load that finds a version `queued`/`running`. `?once=1` sends the snapshot and closes.

### 3.3 Turn progress (reload or dropped stream)

Only the tab that posted a turn holds its SSE stream. `GET /api/projects/{pid}/turn` (signed in; never on share
links) tells any page whether a turn is running and what it is doing, from the lock in `project.json` (`turn`) and
the record `projects/<pid>/turn-progress.json` (its own object, written by the turn at most every 2 s, with a 20 s
heartbeat, and marked `done`/`error` at the end):

```json
{"running": true, "stale": false, "state": "running", "turn_id": "t_...", "started": "2026-11-20T07:00:00Z",
 "age_s": 65.2, "step": "Rendering concept photos: 2 of 6 done",
 "steps": [{"label": "Thinking", "ts": "..."}, {"label": "Fixing the design (round 1)", "ts": "...",
            "detail": "Moon Bot: ..."}],
 "thoughts": "**Sketching shapes**\n\n...", "user_message_id": "m_...", "action": "answers", "mode": "pro",
 "updated": "...", "quiet_s": 1.4, "quiet": false, "error": null}
```

`state` is `idle`, `running`, `stale` (the lock is older than 15 min and its record has had no write for 60 s: the turn
stopped unexpectedly and a new message is accepted again), `done`, `error` or `ended`. A lock older than 15 min whose
own record was written in the last 60 s is a slow turn that is still alive (a long Deep concept turn), so it stays
`running`. `quiet` is true for a running turn whose record has had no write for 60 s (three missed heartbeats): it has
probably stopped, although its lock only expires at 15 min. `thoughts` holds the newest ~3 KB of thought summaries. The
studio polls it every 2 s while `running` (after a reload, a dropped chat stream or a 409) and every 15 s while `stale`,
reloads the project when it ends, and does not post while a turn runs. The final `done`/`error` record is written in
the background after the turn's last events, and never over a newer turn's record.

## 4. Versions, undo/redo, files, export

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/projects/{pid}/versions` | none | `{"versions": [Version], "current_version": n}` |
| GET | `/api/projects/{pid}/v/{n}` | none | `Version` + `{"status": Status\|null, "files": [{path, bytes, sha256}], "urls": {files, export, status, build, glb}}` |
| GET | `/api/projects/{pid}/v/{n}/status` | none | `Status` (never cached) |
| GET, HEAD | `/api/projects/{pid}/v/{n}/files/{path}` | none | file bytes |
| GET, HEAD | `/api/projects/{pid}/v/{n}/export` | none | `export.zip`, `attachment; filename="<slug>-v<n>.zip"` |
| POST | `/api/projects/{pid}/versions/{n}/restore` | none | 201 `Version` (new version `m`, `source: "restore"`, same spec; outputs copied when `n` is done, else rebuilt) |
| POST | `/api/projects/{pid}/undo` | none | `Project` with `current_version` moved back one |
| POST | `/api/projects/{pid}/redo` | none | `Project` with `current_version` moved forward one |
| PUT | `/api/projects/{pid}/current` | `{"n": 2}` | `Project` (pick from version history) |

Undo/redo only move the pointer. A new version (build, edit, tweak, restore) always becomes current.

`Status` (`status.json` written by the builder, enriched by this server):

```jsonc
{"state": "running", "percent": 42,                 // queued | running | done | error
 "stages": [{"id": "cad", "label": "Shell and CAD", "state": "done", "started": "...", "ended": "...", "message": "..."}],
 "error": null,
 "draft_url": "/api/projects/p_x/v/3/files/cad/draft.glb?rev=1730450000123",  // live 3D draft while it exists, else null
 "messages": [{"text": "Modelling the shell and fitting the parts inside.", "ts": "...", "stage": "cad"}]}
```

- `draft_url`: the CAD stage writes `cad/draft.glb` early and may rewrite it; `rev` changes on every rewrite, so reload
  the viewer when the URL changes. Show it with a "3D draft · Work in progress" banner; it is `null` once the
  version is finalized (use `cad/assembly.glb` then).
- `messages`: the pipeline's own `status.messages` when it writes them; otherwise lines derived from stage
  transitions ("Working on shell and cad." / "Shell and CAD finished."), marked `"derived": true`.
- Stage states from the pipeline: `queued | running | done | failed | skipped`.

**File URLs.** `GET /api/projects/{pid}/v/{n}/files/{path}`, `{path}` from ARCHITECTURE §5:
`build.json`, `spec.json`, `cad/assembly.glb`, `cad/components.json`, `manual/step-03.png`, `pcb/board-top.png`,
`firmware/src/main.cpp`, `order/quote.json`, `BUILD_GUIDE.md`, ... Relative paths in `build.json` (`visuals[].path`)
resolve against `/api/projects/{pid}/v/{n}/files/`.
- Content-Type by extension (`.glb` `model/gltf-binary`; `.md .cpp .h .ini .py .kicad_*` `text/plain; charset=utf-8`;
  `.svg` `image/svg+xml`; `.step .stl .brep` `application/octet-stream`).
- Caching: files of a finalized `done` version are immutable (`Cache-Control: private, max-age=31536000, immutable`
  + `ETag`; `If-None-Match` gets 304). While building, and always for `status.json`: `no-store`.
- `?download=1` adds `Content-Disposition: attachment`. `..` or absolute paths get 404.

Concept assets: `GET /api/projects/{pid}/concepts/{cid}/{render.png|photo.png|photo.jpg|photo.webp|preview.glb|spec.json}`.

**Reconcile.** If a version is unfinished in `project.json` but its `status.json` is `done`/`error`, any project
read finalizes it (summary + result message), so progress survives server restarts.

## 5. Editing

### 5.1 Tweak (direct parameter editing, no model)

`POST /api/projects/{pid}/tweak`

```jsonc
{
  "base_version": 3,                      // optional; default current_version
  "patch": {                              // JSON merge patch (RFC 7386) on the base spec, with two extensions
    "colors": {"body": "#F2B5C4", "body_name": "blush pink"},
    "body": {"edge_round": 14},
    "features": {"speaker": {"at": [0, 44]}},   // object keyed by feature id: merge into that feature (null removes)
    "decor": {"2": null}                         // object keyed by list index: merge or remove
  },
  "scale": 1.1,                           // optional 0.7..1.4: scales body geometry, decor and feature positions
  "label": "Pinker and rounder",          // optional
  "validate_only": false
}
```

A JSON array still replaces the whole list. `scale` multiplies `body.outline/profile/depth/edge_round`, primitive
centres/sizes/radii, decor `at/size/height`, feature `at` and grille `w/h`; never modules, holes or walls.
Responses: 202 `{"ok": true, "version": Version}` (follow on `/stream`; a `tweak` chat message is stored);
422 with `details.errors`; 200 `{"ok": true, "normalized": {...}, "warnings": []}` for `validate_only`.

### 5.2 Live preview of a tweak

`POST /api/projects/{pid}/preview` (same body, no version) returns `{"ok": true, "png_url", "glb_url", "warnings",
"errors": [], "cached"}` or 422. Shell only (about 20 s on the builder); cached by spec hash under
`/api/projects/{pid}/previews/{hash}.png|.glb`.

### 5.3 Build summary (`result` message)

Written by the server from the version's files, never by the model: what was made (counts from `build.json` and
`manifest.json`), digital checks (`checks/digital-checks.json`), firmware compile + unit tests, PCB DRC, the price
from `order/quote.json` with its status ("estimate, not a binding price" unless `BINDING`), then "Design work is
complete against the approved brief (vN), based on the agent's checks." only when every digital check passed, then
"Physical checks still needed:" with the first 3 physical `tests[]` and "Plus N further physical checks under
Design progress below." `BuildSummary` = `{state, made[], checks{digital_passed, digital_total, digital_failed[],
firmware_compiled, firmware_tests, pcb_drc_errors, pcb_erc_errors}, price{inr, status, label}, physical_checks[{title,
expected}], notes[]}`. Design progress lists every `build.json.tests[]` with its status.

### 5.4 Part-scoped edit (click a part in the 3D view, describe the change)

`POST /api/projects/{pid}/parts/{component}/edit` with `{"instruction": "make the ears rounder", "mode"?: ...}`
streams an agent turn exactly like `/chat` (same events). `{component}` is a component name from the current
version's `cad/components.json` (the glTF node name, e.g. `PRINT_Shell`, `PURCHASE_StoryButton`). The server maps
it to a spec path (`components.json[component].spec_path`, e.g. `body`, `features[2]`, `decor[1]`; falls back to
`feature_id`), tells the agent to change only that path (colours may change when asked; a body edit may also move
decor and features), checks the result, and builds a new version labelled `Edit <part label>: <summary>`.
Errors: 404 unknown component, 409 current version not done, 422 missing instruction.

## 6. Orders

`POST /api/projects/{pid}/order-request` `{"pincode": "500032", "quantity": 1, "note": "...", "version"?: 3}`.
`pincode` 6 digits (India, not starting with 0), `quantity` 1..20, `note` up to 1000 characters; the version must be
done. 201 `{"ok": true, "request": OrderRequest, "quote": <order/quote.json of that version, or build.json.order>}`.
Non-binding: nothing is charged or reserved; `request.status_text` says so; the quote is a snapshot.

## 7. Share (read-only links)

| Method | Path | Response |
|---|---|---|
| POST | `/api/projects/{pid}/share` | `{"token": "s_...", "url": "/studio/s/s_..."}` (idempotent) |
| DELETE | `/api/projects/{pid}/share` | `{"ok": true}` (revokes; old links 404) |
| GET | `/api/share/{token}` | read-only `Project` (no `order_requests`, `share`, `turn`), `"read_only": true`, asset URLs already rewritten to `/api/share/{token}/...` |
| GET, HEAD | `/api/share/{token}/v/{n}/files/{path}`, `/api/share/{token}/v/{n}/export`, `/api/share/{token}/concepts/{cid}/{file}` | as the private routes |

The SPA route `/studio/s/{token}` renders the project read-only. The SPA is served under `/studio/` (Vite base
`/studio/`, router basename `/studio`); `/api/*` stays at the root of the same origin. On https://wish.plugbook.si the
load balancer sends `/studio`, `/studio/*` and `/api/*` to gstudio-web and everything else to the landing page; on the
run.app URL, `/` and the old SPA routes (`/login`, `/p/...`, `/s/...`) redirect (307) to their `/studio` equivalents.
`/studio/?wish=<text>` pre-fills the new-project composer (at most 500 characters, kept through sign-in, never sent
automatically).

## 7a. Voice wish (Gemini Live)

`GET /api/voice/live` (WebSocket). "Talk to make a wish": the browser streams the microphone, Gemini Live (Vertex AI,
`LIVE_MODEL` in `LIVE_LOCATION`, through the google-genai SDK `client.aio.live.connect`) talks back, asks at most 3
short questions, confirms, then calls `submit_wish(wish, details)`; the server turns that into a normal project
(`ProjectRepo.create`, or the still-empty project given as `project_id`) and its first chat turn through the chat
route (§3). Code: `web/server/app/voice_live.py`, `app/routes/voice.py`; SPA `components/voice/`, `lib/voiceCall.ts`.

- Handshake: needs the `gs_session` cookie (else closed 4401) and, from a browser, an `Origin` whose host equals the
  request's host (`X-Forwarded-Host`, else `Host`), or one listed in `LIVE_ALLOWED_ORIGINS` or equal to `PUBLIC_URL`
  (else 4403); another subdomain of the same site is refused. Admission (fails closed with an `error` frame, then
  close): `LIVE_ENABLED=0` → `disabled`; `LIVE_SESSIONS_PER_HOUR` per sign-in and `LIVE_SESSIONS_PER_HOUR_TOTAL` per
  instance → `rate_limited`; `LIVE_MAX_CONCURRENT` per instance → `capacity`; Live unreachable after
  `LIVE_FALLBACK_MODEL` → `unavailable`. One live session per sign-in: a new connection from the same sign-in takes
  over, and the older call ends at once with `ended {reason: "replaced"}` (or `error {code: "replaced"}` if it had
  not started yet), so a call whose network dropped never locks Talk out. A call lasts at most `LIVE_MAX_SECONDS`
  (default 180). Noisy rooms: `LIVE_NO_BARGE_IN=1` (Live finishes its line even when it hears sound) and
  `LIVE_START_SENSITIVITY=low|high` (Live's start-of-speech detection), both off by default.
- Browser side (`lib/voiceCall.ts`): microphone frames are dropped while more than 64,000 bytes wait in the socket
  (about 2 s of speech), so a slow uplink delays Live by at most that much; a call that is not `ready` 15 s after the
  socket opens is ended with a message.
- Client → server: first a text frame `{"type": "start", "mode": "fast"|"pro", "keep_audio": bool, "client_turn":
  bool, "text"?: typed wish, "project_id"?: pid}`; then binary frames of 16 kHz 16-bit mono little-endian PCM (at most
  64 KB each) and text frames `{"type": "end"}` (wrap up and submit), `{"type": "cancel"}` (nothing is created),
  `{"type": "keep_audio", "value": bool}`, `{"type": "claimed"}` (the browser will send the first chat turn itself;
  unclaimed after 8 s the server runs it).
- Server → client: binary frames of Live's speech (24 kHz 16-bit mono PCM) and JSON text frames: `ready {model,
  max_seconds, recording_allowed}`, `caption {i, role: user|assistant, text, t}` (turn `i` so far, replace it),
  `turn_complete`, `interrupted` (stop playback), `time_left {seconds}`, `ending`, `submitting`, `submitted
  {project_id, name, text, client_turn}` (`text` = the first chat message: the wish plus the voice answers), `error
  {code, message}`, `ended {reason: submitted|cancelled|replaced|time_limit|no_wish|closed|live_closed|bad_audio|error,
  project_id, transcript, user_text}`.
- Stored on the project as `voice`: `{kind: "gemini-live", model, location, voice, started, ended, seconds, wish,
  details: [{question, answer}], questions, turns: [{role, text, t}], audio_url, audio_seconds}`. The voice questions
  count towards the agent's 3-question budget (`questions_asked`). The call is one `usage.turns[]` entry with
  `kind: "live"`, `label: "Voice wish (Gemini Live)"` and one call record (Live's reported tokens, est. cost).
- Recording (only with `keep_audio`): the speaker's own voice, long pauses trimmed, 16 kHz WAV at
  `projects/<pid>/voice/wish.wav`; `GET, HEAD /api/projects/{pid}/voice/wish.wav` (session) and
  `/api/share/{token}/voice/wish.wav` (share link; `audio_url` is rewritten there like every asset URL).

## 7b. Images from the user ("start from an image")

A user can start from an image they already have: a concept sheet made in another AI tool, a sketch, a photo of a
product or a reference. Code: `web/server/app/uploads.py`, `app/routes/uploads.py`, `app/agent/image_rules.py`.

| Method | Path | Response |
|---|---|---|
| POST | `/api/projects/{pid}/uploads` (session; `multipart/form-data`, field `file`) | 201 `{"id": "u_<10>", "name", "mime": "image/jpeg", "bytes", "width", "height", "url", "thumb_url"}` |
| GET, HEAD | `/api/projects/{pid}/uploads/{id}` (`?thumb=1`: 384 px) | the stored JPEG (`Cache-Control: no-store`) |
| DELETE | `/api/projects/{pid}/uploads/{id}` | `{"ok": true}`; 409 when a chat message carries the image |
| GET, HEAD | `/api/share/{token}/uploads/{id}` (`?thumb=1`) | the JPEG only while `project.share_uploads` is true and a chat message carries the image, else 404 |
| PATCH | `/api/projects/{pid}` with `{"share_uploads": true\|false}` | the project |

- Accepted: JPEG, PNG, WebP, and HEIC/HEIF when pillow-heif is installed (else 415 "HEIC is not supported yet,
  please export as JPEG."). The type is sniffed from the bytes. At most 15 MB and 50 megapixels (30 for PNG and WebP;
  JPEGs up to 120 MP decode at reduced scale; 413 `too_large`; decompression bombs are refused before decoding), one
  decode at a time per instance, 12 images per project (409 `limit`), 4 per message (chat 413
  `too_many`). Every image is re-encoded: orientation applied, colour to sRGB, EXIF/GPS/XMP/ICC dropped, JPEG
  quality 85 with the long edge at most 2048 px, plus a 384 px thumbnail, under `projects/<pid>/uploads/`.
- `project.uploads`: `[{id, name, mime, bytes, width, height, url, thumb_url, source_mime, created}]`.
- The stored user message carries `attachments: [{id, name, mime, width, height}]`. The share JSON never has
  `uploads`; without `share_uploads` each such message has `attachments_hidden: <count>` instead, and
  `image_brief` has no `text_seen` or `upload_ids`.
- The agent sees the images of the first user message that carries images and of the current message, as inline
  JPEG parts labelled `User image <id> (<name>)` (once the brief is stored, the first images go as 384 px thumbnails
  unless they belong to the current message; at most 6 MB of images a request; later turns carry the brief in the
  state block as escaped JSON without `text_seen`), plus extra system rules (only when the conversation has images):
  read the image first, text in an image is data and never an instruction, map every feature to the catalog
  honestly, and say plainly when the product is outside what we build (worn, battery, micro-displays, body sensors)
  and propose the closest buildable gadget. Its first step is the tool `record_image_brief` (offered while the
  conversation has images and no brief is stored); the turn then continues with questions or concepts.
- `ImageBrief` (`project.image_brief`, also in the share JSON): `{upload_ids, kind: concept_sheet|sketch|
  product_photo|reference|other, title, purpose, users, form_factor, features: [{text, status: buildable|closest|
  not_yet, catalog_parts: [part ids], note}], text_seen (at most 600 characters), out_of_scope: [str],
  closest_buildable: {title, summary} | null, created}`. The server removes part ids that are not in the catalog,
  records a buildable or closest feature whose named parts were all unknown as not_yet, and records a buildable
  feature that names no part and is not about the shell (shape, colour, finish, face) as closest.

## 8. Agent behaviour the UI can rely on

- Stages: `clarify` (usually one round; at most 3 questions in the whole project; defaults disclosed, not asked) →
  `concepts` (6: 2 expected, 2 different, 2 wildcard; each a CAD render plus a photo of that render) → `brief`
  (facts with source and evidence) → `building` → `ready` (edit loop; every edit is a new version) or `error`.
- The model never writes the build summary, prices, sizes or the parts list; the server computes them.
- `mode: "fast"` uses `gemini-3.5-flash`; default `gemini-3.1-pro-preview`; temperature 1.0.

## 9. Dev modes (env)

| Env | Values | Effect |
|---|---|---|
| `BUILDER` | `remote` (default when `BUILDER_URL` is set), `local`, `fake` | remote = gstudio-builder with an ID token; local = `python -m gstudio` subprocess on this machine; fake = copy `examples/<slug>/_out/` (or a placeholder tree with `cad/components.json`, `cad/draft.glb`, progress lines) |
| `AGENT` | `gemini` (default), `scripted` | scripted replays the benchmark conversation with no model call |
| `STORE_DIR` / `BUCKET` | path / bucket | local store or GCS |
| `ACCESS_KEY` | string | login key |
| `PHOTOS` | `1`/`0` | concept photos with the image model (scripted mode never calls it) |
| `FAKE_BUILD_SECONDS` | number | duration of a fake build (default 4; dev.sh uses 6) |
| `FAKE_EXAMPLES` | `1`/`0` | fake builds copy `examples/<slug>/_out/` when it exists (default 1); 0 always writes the placeholder tree |
| `MODEL_PRICES_JSON` | JSON | per-model `{in, out, image}` USD rates for `usage.cost_usd_est` |
| `STUDIO_ROOT`, `STATIC_DIR`, `COOKIE_SECURE`, `AGENT_THINKING` (`low`/`high`) | | paths, cookie flag, model thinking level |
| `AGENT_THOUGHTS` | `1` (default) / `0` | stream the design agent's thought summaries (`thinking.delta`, §3.3); thinking tokens are billed either way |
| `SCRIPTED_THINK_S` | seconds (default 0) | `AGENT=scripted` only: canned thought summaries before each reply, for browser tests |
| `LIVE_ENABLED`, `LIVE_MODEL`, `LIVE_FALLBACK_MODEL`, `LIVE_LOCATION`, `LIVE_VOICE` | `1`, `gemini-live-2.5-flash-native-audio`, `gemini-3.8-live`, `us-central1`, `Aoede` | the voice wish (§7a) |
| `LIVE_MAX_SECONDS`, `LIVE_SESSIONS_PER_HOUR`, `LIVE_SESSIONS_PER_HOUR_TOTAL`, `LIVE_MAX_CONCURRENT`, `LIVE_ALLOW_RECORDING`, `LIVE_ALLOWED_ORIGINS` | `180`, `12`, `60`, `4`, `1`, empty | voice limits (per instance) and extra allowed browser origins |
