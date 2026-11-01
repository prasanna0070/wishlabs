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
| `message.done` | `{seq, message: ChatMessage}` | a message is final and stored (any kind). Replace drafts by id. |
| `tool.start` | `{seq, call_id, name, label}` | `name` in `ask_user, propose_concepts, write_brief, build_design, update_design, say` |
| `tool.done` | `{seq, call_id, name, ok, summary, errors?}` | |
| `repair` | `{seq, round, errors}` | a spec failed validation and the model is fixing it (rounds 1..3) |
| `choices` | `{seq, message}` | clarifying questions (also sent as `message.done`) |
| `concepts.progress` | `{seq, concept_id, index, title, tier?, state, note?}` | per concept: `rendering`, `photo`, `ready`, `failed` (all 6 in parallel) |
| `concepts` | `{seq, message, concepts: [Concept]}` | concepts ready to pick |
| `brief` | `{seq, message, brief}` | brief ready for review |
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
| POST | `/api/projects/{pid}/share` | `{"token": "s_...", "url": "/s/s_..."}` (idempotent) |
| DELETE | `/api/projects/{pid}/share` | `{"ok": true}` (revokes; old links 404) |
| GET | `/api/share/{token}` | read-only `Project` (no `order_requests`, `share`, `turn`), `"read_only": true`, asset URLs already rewritten to `/api/share/{token}/...` |
| GET, HEAD | `/api/share/{token}/v/{n}/files/{path}`, `/api/share/{token}/v/{n}/export`, `/api/share/{token}/concepts/{cid}/{file}` | as the private routes |

The SPA route `/s/{token}` renders the project read-only.

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
