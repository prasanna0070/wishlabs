# Gadget Studio: architecture and contracts (v1)

"Gadget Studio" is a working name. This is a Plugbook-experiments project. It carries no Quarktex branding
and no third-party branding.

**Goal.** A Cloud Run web app where a non-technical person describes a gadget and gets output **as good as
the benchmark's or better**. The benchmark's output for "I want a cute robot that tells my daughter bedtime
stories and remembers her favorite characters" is our benchmark. That output was:
- a chat with clarifying questions, concept images and an approved brief;
- a parametric CadQuery enclosure with STEP, STL and BREP exports;
- a parts list with Indian sources;
- an illustrated 10-step build manual;
- a wiring list with a pin map;
- tested software;
- version history, and an export of everything.

The UI has Preview, Parts, Order, Build (Assembly | Wiring) and Code tabs. Reference material:
- `docs/ref/benchmark-ui.md`: the UI tour.
- `docs/ref/benchmark-output-contract.md`: every file, schema and quality criterion.
- The raw benchmark export, extracted and **untrusted**, under
  `/private/tmp/claude-501/-Users-prasanna-os-brain/a52fdb81-fabb-42b2-ab4e-72b3ad52d494/scratchpad/benchmark/`.

We must build it **only with our component catalog**: the blueprint §3.3 rows, made concrete in
`library/modules.json`. The blueprint's architecture is:
- one ESP32-S3 core board on a standard backplane PCB
- keyed JST-PH cables to plug-in modules
- a custom 3D-printed shell
- USB-C 5 V power, indoor, desk-sized

Where we beat the benchmark:
- a real, routed, DRC-checked backplane PCB (KiCad project and Gerbers);
- a compiled ESP32-S3 firmware with unit tests;
- an instant delivered price (blueprint §8, reusing `../sim/price_engine.py`);
- concept images that are photoreal renders of the actual buildable geometry;
- direct parameter editing as well as chat editing.

## 0. Rules for every agent

- **Heavy compute never runs on the laptop.** Use `tools/vm.sh` for Python CAD/KiCad/VTK runs, PlatformIO,
  npm installs/builds, test suites, renders and slicing:
  - `tools/vm.sh sync` pushes local sources.
  - `tools/vm.sh run '<cmd>'` runs from `studio/` with the toolchain env.
  - `tools/vm.sh pull <rel>` brings results back so you can look at PNGs with Read.

  The VM mirrors `studio/` at the same absolute path. Write outputs under a directory named `_out/` (sync
  excludes it and it is git-ignored). Light work stays local: editing, grep, json validation and git.
- VM toolchain: Python 3.12 venv `/opt/gs/venv` (cadquery 2.8, OCP 7.9, vtk 9.6, pyvista, trimesh, shapely,
  manifold3d, matplotlib, fastapi, google-genai, google-cloud-storage, platformio 6.2, pygltflib).
  `pcbnew` 9.0.9 is importable from the system site-packages. Also installed:
  - `kicad-cli` 9.0.9 with the standard symbol, footprint and 3D libraries in `/usr/share/kicad/`;
  - Freerouting 2.5.0: `$GS_JAVA -jar $GS_FREEROUTING_JAR` (Java 25);
  - PlatformIO core in `/opt/gs/pio`, with espressif32 + Arduino warmed for `esp32-s3-devkitc-1`;
  - `prusa-slicer` 2.7.2, `xvfb-run`, node 22, and headless Chromium for Playwright at
    `/opt/gs/pw` (`PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers`).
- Several agents share the VM and the tree. **Only write inside the paths you own** (see §2). Read
  anything. Do not run `git commit`, `git checkout` or `git stash`; the orchestrator commits.
- **Provenance or silence.** Generated text (guides, notes, captions) never invents a dimension, pinout,
  price or test result. Unverified items are labelled the way the benchmark does it: "Physical checks still
  needed", "estimate", "not modeled". Never claim a physical test happened.
- Code is Python 3.12 (engine, servers) and TypeScript (frontend). Prefer small pure functions, type hints and
  pytest tests per module. Put tests next to your package under `tests/`.
- Coordinates everywhere: **millimetres, +Z up, front faces -Y, the gadget stands on the table plane Z=0, +X is
  the viewer's right when looking at the front (the robot's left)**. Origin is the
  centre of the footprint at the table plane.

## 1. System overview

```
browser SPA (web/app)  ──>  gstudio-web (Cloud Run, public, login gate)
   mic 16 kHz PCM  <──WS──>   ├─ voice wish: Gemini Live (Vertex, us-central1) ──> submit_wish ──> project + 1st turn
                              ├─ agent: Gemini on Vertex (global) with tools ──> design spec
                              ├─ concepts: builder /preview render ──> Nano Banana photoreal image
                              ├─ project store: GCS  gs://$BUCKET/projects/<pid>/...
                              └─ calls (ID token) ──> gstudio-builder (Cloud Run, private, heavy image)
                                                        engine `gstudio`: spec -> electrical -> CAD | PCB | firmware
                                                        -> print slice -> order quote -> manual -> checks -> package
                                                        writes outputs + status.json to GCS
```

## 2. Repository layout and ownership

All paths are relative to `studio/`.

| Path | Owner | What |
|---|---|---|
| `docs/` | orchestrator (ref/ by recon agents) | contracts and references |
| `library/modules.json` (+ `modules-a/b.json`) | library agents, then foundation | module library (merged by foundation) |
| `library/backplane-v1.json` | foundation | standard backplane/cartridge definition (§6) |
| `engine/gstudio/spec.py`, `library.py`, `electrical.py`, `normalize.py`, `version.py` | foundation | spec models, validation, implied-part completion, pin/slot allocation |
| `engine/gstudio/cad/` | cad | shells, decor, apertures, mounts, internals, cover, fasteners, cables, exports, checks, preview render |
| `engine/gstudio/pcb/` | pcb | KiCad schematic + board from electrical.json, Freerouting, DRC/ERC, Gerbers, renders, GLB |
| `engine/gstudio/firmware/` | firmware | PlatformIO project generator (templates), compile, native unit tests, story pack |
| `engine/gstudio/manual/` | manual | build steps, wiring steps, rendered illustrations, wiring diagram, BUILD_GUIDE.md |
| `engine/gstudio/order/`, `engine/gstudio/pipeline.py`, `engine/gstudio/cli.py`, `engine/gstudio/checks.py`, `engine/gstudio/package.py`, `builder/` | pipeline | orchestration, slicing, quote, BOM/PARTS_EVIDENCE, digital checks, manifest/zip, builder service + Dockerfile |
| `web/server/` | web-backend | FastAPI app, agent, GCS store, auth, builder client, Dockerfile |
| `web/app/` | web-frontend | React + Vite + TypeScript + three.js SPA |
| `examples/<slug>/spec.json` | foundation creates; cad may tune `body`, `decor`, `features[].at` | fixtures; `_out/` holds generated outputs |
| `deploy/` | pipeline + web-backend | Cloud Build / Cloud Run scripts |
| `tools/vm.sh` | orchestrator | VM helper |

The Python package root is `engine/` (`pip install -e engine` or `PYTHONPATH=engine`). Each stage is a
function with this signature:

```python
def run(ctx: "BuildContext") -> dict   # returns a JSON-able stage summary; raises StageError(msg, details) on hard failure
```

`BuildContext` lives in `gstudio/context.py` (owned by foundation):
- `spec: Spec`: the normalized pydantic model;
- `lib: Library`;
- `out: Path`: the version output root;
- `electrical: dict | None`: filled after the electrical stage;
- `progress(stage_id: str, message: str, percent: float | None)`;
- `artifacts: dict`: paths that stages publish for later stages, e.g. `ctx.artifacts['cad.components']`;
- `options: dict`, e.g. `{"stories_audio": False, "fast": False}`.

Stage entry points:
- `gstudio.electrical.run`
- `gstudio.cad.run`
- `gstudio.cad.preview(spec, lib, out) -> {"png": path, "glb": path, "warnings": [...]}` (shell only, about 20 s or less)
- `gstudio.pcb.run`
- `gstudio.firmware.run`
- `gstudio.order.run` (slice and quote)
- `gstudio.manual.run`
- `gstudio.checks.run`
- `gstudio.package.run`

CAD, PCB and firmware are independent after electrical. Manual needs CAD and electrical. Order needs CAD
(print STLs).

CLI: `python -m gstudio build examples/bedtime-robot/spec.json examples/bedtime-robot/_out [--only cad,pcb] [--fast]`
and `python -m gstudio preview <spec> <outdir>`.

## 3. Design spec `gadget-spec/1` (the contract between the LLM agent and the engine)

The LLM writes this. The engine validates and normalizes it. Pydantic models live in `gstudio/spec.py`.
`Spec.model_json_schema()` is given to the LLM. Unknown keys are rejected. Every numeric field has bounds.
Validation errors must be short and actionable, because the LLM reads them and retries.

```jsonc
{
  "schema": "gadget-spec/1",
  "name": "Luna Owl",                       // product name shown to the user
  "slug": "luna-owl",
  "tagline": "A sleepy owl that tells bedtime stories",
  "audience": {"child_directed": true, "age_years": 5},
  "region": "IN",
  "colors": {"body": "#8CC9C0", "accent": "#F4F1EA", "detail": "#2B3A42", "body_name": "pale teal"},
  "body": {
    "family": "silhouette",                // "silhouette" | "revolve" | "blob"
    // silhouette: closed FRONT-VIEW outline (x,z) mm, counter-clockwise, smooth spline through points, flat
    // bottom segment on z=0 required (two consecutive points with z=0). Extruded along Y by depth, centred on y=0.
    "outline": [[-60,0],[60,0],[78,40],[70,120],[40,170],[0,182],[-40,170],[-70,120],[-78,40]],
    "depth": 120,
    "edge_round": 12,                      // fillet radius on front and rear perimeter edges; engine reduces on failure and reports actual
    // revolve: half profile (r,z) from (0,0) bottom centre to (0,h) top centre, revolved about Z; squash_y scales depth
    "profile": null, "squash_y": 1.0,
    // blob: union of primitives, each filleted; good for head-on-body bots and ears
    "primitives": null,                    // [{"kind":"rounded_box|sphere|ellipsoid|cylinder|capsule","center":[x,y,z],"size":[sx,sy,sz],"radius":r}]
    "wall": 2.4,                           // 2.0..3.5
    "split": "rear_cover",                 // "rear_cover" (silhouette default) | "base_plate" (revolve/blob default)
    "feet": 4
  },
  "decor": [                               // cosmetic relief on the body surface; positions are front-view (x,z) projected along +Y onto the surface
    {"type": "face_plate", "shape": "circle", "at": [0,120], "size": [80,80], "depth": 1.6},
    {"type": "eye", "style": "dot|oval|arc_closed|arc_happy|star|heart", "at": [-16,128], "size": [9,9], "relief": "engrave|emboss", "depth": 0.8},
    {"type": "eye", "style": "arc_closed", "at": [16,128], "size": [9,9], "relief": "engrave", "depth": 0.8},
    {"type": "mouth", "style": "smile|open|small_o|cat", "at": [0,112], "size": [20,7], "relief": "engrave", "depth": 0.8},
    {"type": "blush", "at": [-26,116], "size": [8,5], "relief": "emboss", "depth": 0.6},
    {"type": "ear", "style": "cat|bear|bunny|owl_tuft|antenna", "side": "both", "at": [40,172], "size": [26,30]},
    {"type": "text", "text": "ANYA", "at": [0,20], "height": 8, "relief": "engrave", "depth": 0.6}
  ],
  "features": [                            // functional apertures, each bound to ONE module instance from library/modules.json
    {"id": "story_button", "module": "<library id: big push button>", "face": "front", "at": [0,80], "label": "Story button"},
    {"id": "night_light", "module": "<library id: WS2812B mini>", "face": "front", "at": [0,100], "label": "Night light and listening light"},
    {"id": "speaker", "module": "<library id: 40 mm speaker>", "face": "front", "at": [0,40],
     "grille": {"pattern": "hex_holes|slots|concentric|dots_ellipse", "w": 70, "h": 34, "hole": 2.6, "pitch": 4.4}},
    {"id": "mic", "module": "<library id: INMP441>", "face": "front", "at": [0,64], "label": "Microphone hole"},
    {"id": "eyes", "module": "<library id: 0.96 OLED>", "face": "front", "at": [0,128], "label": "Animated eyes"}
  ],
  "electronics": {
    "core": "<library id: ESP32-S3 DevKitC-1 N8R8>",
    "extra_modules": [],                   // internal modules without an aperture (e.g. RTC, IMU); amp, microSD, USB-C power are implied
    "power": {"input": "usb_c_5v", "adapter_in_box": false}
  },
  "behaviour": {
    "template": "story_player",            // story_player | desk_companion | notifier_clock | ambient_light | sensor_display
    "summary": "Tap: a bedtime story starring her favourite character. Hold: say a new favourite; tap to keep it.",
    "settings": { /* template-specific, validated by the firmware package: see §8 */ }
  },
  "safety": {"flags": ["CHILD_DIRECTED", "LOCAL_VOICE_CAPTURE"], "notes": "..."}
}
```

**Normalization** (`gstudio.normalize`) runs before any stage. It adds implied items:
- the backplane;
- the USB-C power input;
- the amp if any speaker is present;
- the microSD module for the story_player template or any audio file playback;
- harness cables of the right pin count and length class;
- fasteners, inserts, feet and a cable-tie kit.

It assigns instance refs (DISP1, SPK1, MIC1, BTN1, LED1, AMP1, SD1, U1, BP1) and checks the template's
required modules. Hard errors are listed with fixes, for example "story_player needs a speaker feature".

**Fit rules** are checked in normalize and reported as errors the LLM can fix:
- the outline is a simple polygon;
- the overall size is between 60 and 260 mm on each axis;
- the inner floor region contains the cartridge footprint (§6) with 3 mm clearance;
- each feature module's depth fits behind its face at that point;
- features do not overlap each other or the decor cut-outs (with a 3 mm margin);
- the bed fits 250 × 210 × 210 mm. Otherwise the part is split or the spec is rejected with a message.

## 4. Module library `library/modules.json`

This is a JSON array of module objects written by the library agents. The schema is in their notes. Key fields:
- `id`, `catalog_row`, `role`, `dims_mm`, `mount`, `pins`, `module_connector`, `backplane_harness`
- `aperture`, `electrical`, `firmware.arduino_libraries`, `kicad.backplane_footprint`
- `sourcing_in[]`, `price_inr_planning`, `evidence`, `uncertain`

`gstudio.library.Library` loads it and offers:
- `get(id)`, `by_role(role)`, `aperture_modules()`
- `summary_for_llm()`: a compact table for the agent prompt
- `catalog_id_for_price(id)`: the map to `../sim/spec/catalog.json` ids, for the price engine

**Only modules in the library may appear in a spec.** That is the "limited set of components we have".

## 5. Output layout of one build version (`<out>` = local dir or `gs://$BUCKET/projects/<pid>/v<n>/`)

```
spec.json                normalized spec
project.json             gadget-project/1 {name, slug, version, created, engine_version, brief, spec_hash}
electrical.json          gadget-electrical/1 (§7)
build.json               gadget-build/1 (§9). Superset of the benchmark's build format.
PROJECT.md  BUILD_GUIDE.md  PARTS_EVIDENCE.md  SOFTWARE_SETUP.md
cad/  gadget_cad.py      standalone, editable parametric CadQuery source for THIS design (reads params.json; runs without the engine)
      params.json  assembly.step  assembly.glb (one node per component, node name == component name, PBR colours)
      components.json    {NAME: {kind: PRINT|PURCHASE|REFERENCE, label, ref?, module_id?, feature_id?, color, bbox:[xmin,xmax,ymin,ymax,zmin,zmax], volume_mm3, valid, solids, step: "assembly"}}
      anchors.json       {"<ref>.<pin|connector|screw>": [x,y,z], ...}  connector and pin positions for labels and wiring views
      cable-routes.json  [{id, from: "BP1.J_I2C1", to: "DISP1.CONN", cable: "JST-PH 4p 150 mm", diameter, length_mm, points}]
      components/<NAME>.brep
print/ PRINT_<Part>.stl (print orientation, on bed), TEST_<Coupon>.stl, slice-stats.json
pcb/   backplane.kicad_pro .kicad_sch .kicad_pcb, gerbers/*.gbr *.drl, backplane-gerbers.zip, bom.csv, cpl.csv,
       drc.json, erc.json, schematic.svg, schematic.pdf, board-top.png, board-bottom.png, board-top.svg, board-bottom.svg, board.glb, netlist.json
firmware/ platformio.ini  src/  include/pins.h  include/config.h  test/  data/  sdcard/  build-log.txt  firmware.bin  test-results.txt  size.json
manual/ *.png (step illustrations, wiring.png, wiring.svg, assembled.png, exploded.png, ports.png)
checks/ digital-checks.json  delivery-checks.json
order/  quote.json
manifest.json   {engine_version, created, files: [{path, bytes, sha256}]}
export.zip      everything above, under a top folder <slug>/
status.json     {state: queued|running|done|error, stages: [{id, label, state, started, ended, message}], percent, error?}
```

Component naming follows the benchmark:
- `PRINT_<CamelName>` for printed parts (`PRINT_Shell`, `PRINT_RearCover`, `PRINT_SpeakerRing`, `PRINT_Cartridge`, `PRINT_LedDiffuser`)
- `PURCHASE_<CamelName>` for bought parts (`PURCHASE_CoreBoard`, `PURCHASE_Backplane`, `PURCHASE_Speaker`, `PURCHASE_Button`, `PURCHASE_Feet`, `PURCHASE_CoverScrews`)
- `REFERENCE_<CamelName>` for display-only items (cable routes, keep-out zones)

`components.json[name].ref` links a component to its electrical instance ref.

## 6. Standard backplane v1 (the "core cartridge"): `library/backplane-v1.json`

There is one standard cartridge in every product. That is the blueprint's "the factory always gets the same
core assembly". The backplane PCB has a fixed outline, fixed M2.5 mounting holes and **fixed port slots with
fixed pin maps**. Each design populates only the slots it uses. The PCB stage generates that design's board
(same outline and slot positions, only populated parts placed and routed). Foundation writes the JSON, the PCB
agent realises it in KiCad, and the CAD agent models it.

The values below are constraints. Foundation finalizes the numbers using the library's measured devkit
dimensions and header pinout.

- Board: 2-layer FR-4, 1.6 mm, about 92 × 62 mm (or smaller if it fits), 4 × M2.5 holes about 3.5 mm from the corners.
- Mounting: on 4 printed bosses with M2.5 heat-set inserts. The backplane top is 8 mm above the inner floor.
- U1: ESP32-S3-DevKitC-1 N8R8 (library id CORE_ESP32S3_DEVKITC1_N8R8; Espressif lists no N16R8 DevKitC-1) on 2 × 1x22 2.54 mm female sockets. The devkit USB ports overhang the
  edge toward the robot's left for factory flashing.
- USB-C power: a 16-pin receptacle on the board edge facing the rear (+Y). It needs:
  - CC1 and CC2 each through 5.1 kΩ to GND;
  - a polyfuse (about 1.5 A hold) and a 5 V TVS diode;
  - 10 µF + 100 nF bulk capacitance;
  - a +5V rail feeding the devkit 5V pin. Check how the devkit's own USB 5 V is isolated and document it.
- A1: MAX98357A breakout socket (1x7 2.54) + J_SPK 2-pin JST-PH (speaker wires from the amp's terminal).
- S1: microSD SPI module socket (1x6 2.54), sharing the SPI bus with the display.
- Ports (JST-PH 2.0 vertical, keyed, per blueprint rows 06/07):
  - J_I2C1 and J_I2C2: 4p GND, 3V3, SDA, SCL
  - J_DISP: 8p GND, 3V3, SCK, MOSI, CS, DC, RST, BL. A 4p SSD1306 OLED uses J_I2C* instead.
  - J_MIC: 6p GND, 3V3, SCK, WS, SD, LR
  - J_IO1..J_IO3: 3p GND, 3V3, SIG
  - J_LED: 3p GND, 5V, DIN through a 74AHCT1G125 3.3 V to 5 V level shifter
- TP1..TP7: factory test pads (5V, 3V3, GND, EN, TXD0, RXD0, IO0), blueprint row 49.
- The JSON gives, per slot:
  - id, footprint, position (board coords mm, origin at the board's lower-left corner, top-side view), rotation and
    facing edge;
  - pins with nets and fixed ESP32 GPIO numbers;
  - which module roles may plug into it.

  It also gives the board outline, hole positions, the height envelope per slot, and the cartridge-to-shell
  transform rule: the board lies flat, centred in X, as far toward the rear as the shell allows, USB-C at the rear.

## 7. `electrical.json` (`gadget-electrical/1`, produced by `gstudio.electrical.run`)

```jsonc
{
  "schema": "gadget-electrical/1",
  "backplane": "backplane-v1",
  "core": {"ref": "U1", "module": "..."},
  "instances": [{"ref": "DISP1", "module": "...", "feature": "eyes", "slot": "J_I2C1", "harness": {"cable": "JST-PH 4p", "length_mm": 150, "wires": [{"module_pin": "GND", "slot_pin": 1, "net": "GND", "color": "black"}]}}],
  "slots_used": ["J_PWR", "U1", "A1", "S1", "J_I2C1", "J_MIC", "J_IO1", "J_LED", "J_SPK"],
  "gpio": {"I2C_SDA": 8, "I2C_SCL": 9, "I2S_SPK_BCLK": 0, "...": 0},    // signal -> GPIO, fixed by backplane-v1
  "buses": [{"type": "i2c", "sda": 8, "scl": 9, "devices": [{"ref": "DISP1", "addr": "0x3C"}]}, {"type": "i2s_tx", "...": 0}, {"type": "spi", "...": 0}],
  "nets": [{"name": "GND", "nodes": ["U1.GND", "J_PWR.A1", "..."]}],
  "connections": [{"from": "DISP1.SDA", "to": "BP1.J_I2C1.3", "via": "JST-PH 4p 150 mm", "color": "blue", "net": "I2C_SDA"}],
  "power": {"rails": [{"name": "+5V", "source": "USB-C via F1"}, {"name": "+3V3", "source": "devkit regulator"}],
            "budget": [{"ref": "AMP1", "ma_typ": 0, "ma_max": 0}], "total_ma_max": 0, "limit_ma": 0, "ok": true, "notes": ""}
}
```

## 8. Behaviour templates (firmware package)

Arduino framework on PlatformIO for `esp32-s3-devkitc-1`. Each build generates a project from
template + spec + electrical.json and compiles it on the builder (`pio run`). Native unit tests of the pure
logic run with `pio test -e native`. `include/pins.h` is generated from electrical.json.

Templates:

**story_player** (benchmark)
- Plays bedtime stories as audio files from microSD through the MAX98357A to the speaker.
- The story text has `{FAV}` slots, played as an inserted name clip.
- Favourites are remembered in NVS and microSD.
- The settings choose the capture mode:
  - `child_voice_clip` (hold the button and say the name; the LED shows listening; the clip is played
    back; tap to keep it; stored only on the microSD, never uploaded);
  - `parent_web` (the parent types names on a local Wi-Fi page; name clips come from a story pack);
  - `none`.
- Also: a night-light glow and timer, volume, stop on tap, a parent page (Wi-Fi AP or STA with mDNS) for
  favourites, erase and volume, and signed-OTA readiness with rollback (`esp_ota` with app rollback enabled).
- Story pack: a `sdcard/` folder with `stories.json` and audio files. Audio is optional at build time
  (Gemini TTS on the builder, behind an option). Text stories are always generated.

**desk_companion**
- An OLED or colour display face (expressions), a speaker for chimes, a button or touch control, an
  optional mic for clap detection, and a light.

**notifier_clock**
- NTP time on a display, an LED ring for notifications through a local HTTP/MCP-friendly JSON API, and a buzzer.

**ambient_light**
- An LED ring or strip with scenes, a light sensor for auto-dim, and touch or button control.

**sensor_display**
- Temperature, humidity and pressure from I2C sensors on a display, with a trend graph and a light warning.

Every template exposes the same small local JSON API for app and MCP control (`GET /api/state`,
`POST /api/action`). That is our "reprogram later" story.

## 9. `build.json` (`gadget-build/1`)

Keys:
- `schema`
- `purchased[]`:
  `{name, quantity, requiresSoldering, module_id, catalog_row, price_inr_planning:[lo,hi], price_inr_seen, listing:{url, vendor, seen_on, status}, notes, modelPart}`
- `printed[]`:
  `{name, file, material, color, grams, hours, supports, orientation_note}`
- `steps[]`:
  `{id, title, instruction (markdown, starts with **Use:** and **Tools:** lines), expected, parts:[component names], highlight:[component names], camera:{position:[x,y,z], target:[x,y,z]}, visuals:[{path, alt, caption}]}`
- `wiring`:
  `{runtime: "esp32-arduino", board: "esp32-s3-devkitc-1", code: "firmware/src/main.cpp", parts:[{id, name, kind, item, module_id, sizeMm}], connections:[{from, to, kind: wire|cable|socket, via, color, note}], steps:[{id, title, text, highlight:[...], labels:[anchor names], camera}]}`
- `tests[]`:
  `{title, expected, kind: digital|physical, status: passed|failed|not_run}`
- `firmware`:
  `{framework, board, compiled: bool, size, tests_passed, tests_total}`
- `pcb`:
  `{layers, size_mm, drc_errors, erc_errors, routed: bool, unrouted, files}`
- `order`:
  `{list_price_inr, status}`

Step IDs are stable slugs, so the checklist progress stays meaningful across versions.

## 10. Services

**gstudio-builder** (private Cloud Run, heavy image built from `builder/Dockerfile`; Ubuntu 24.04 + KiCad 9 PPA +
the same toolchain as the VM; 8 vCPU / 16-32 GiB, timeout 3600, concurrency 2):
- `GET /health`
- `POST /preview {spec}` -> `{ok, png_b64, glb_b64, warnings, errors}`
- `POST /validate {spec}` -> `{ok, errors, normalized}`
- `POST /build {project_id, version, spec, brief, options}`: runs the pipeline, mirrors the out dir to
  `gs://$BUCKET/projects/<pid>/v<n>/`, updates `status.json` there after every stage, and returns the summary.

**gstudio-web** (public Cloud Run, light image `web/server/Dockerfile`, which also bundles the built SPA). Access is a
login gate: the key is in Secret Manager `gstudio-access-key`, with an HMAC-signed cookie. The API is in
`docs/API.md`, owned by web-backend and consumed by web-frontend.

Project state is in `gs://$BUCKET/projects/<pid>/project.json`:
- `{id, name, created, updated, stage: clarify|concepts|brief|building|ready|error}`
- `chat:[{id, role, kind: text|choices|concepts|brief|progress|result, text, payload, ts}]`
- `brief`, `concepts:[{id, title, blurb, spec, render_url, photo_url}]`, `selected_concept`
- `versions:[{n, created, label, state, summary, spec_hash}]`, `current_version`
- `checklist:{assembly:{step_id: bool}, wiring:{}}`, `order_requests:[]`

**Agent** (web-backend): Gemini via google-genai on Vertex.
- The default model is `gemini-3.1-pro-preview` on `global`. The fast mode is `gemini-3.5-flash`. Temperature 1.0.
- Tools:
  - `ask_user(questions[{id, question, options[], allow_free_text}])`
  - `propose_concepts(concepts[{title, blurb, spec}])`: the server validates each spec, renders it via the
    builder's /preview, then asks Nano Banana (`gemini-3-pro-image`, global, responseModalities TEXT+IMAGE) for a
    photoreal product photo **of exactly that render** (the render is passed as the reference image);
  - `write_brief(brief)`, `build_design(spec)`, `update_design(spec, change_summary)`, `say(text)`
- The system prompt holds the spec JSON schema, the `summary_for_llm()` library table, design rules for cute,
  printable, safe consumer objects, blueprint safety rules, and one worked example spec.
- Validation errors from the builder go back to the model for up to 3 repair rounds.

**Voice wish** (web-backend, `app/voice_live.py`, API.md §7a): "Talk to make a wish". The browser streams the
microphone over a WebSocket; the server bridges it to a Gemini Live session (`gemini-live-2.5-flash-native-audio`,
`us-central1`, fallback `gemini-3.8-live`; input and output transcription on; credentials stay on the server). Live
asks at most 3 short questions, confirms, and calls `submit_wish(wish, details)`; the server creates the project with
`ProjectRepo.create` and runs its first turn through the normal chat path (`Turn`), so the design flow is not forked.
The transcript (and, if the speaker opts in, their trimmed recording) is stored as `project.voice` and shown by
`VoiceWishCard` in the project and on share links; the call is one `kind: "live"` entry in the usage receipts. Limits:
login, same origin, one call per sign-in, hourly caps per sign-in and per instance, 3 minutes per call, fail closed.

**Frontend** (React SPA): the layout replicates the benchmark's (see docs/ref/benchmark-ui.md):
- left: the chat;
- right: the tabs **Preview | Parts | Order | Build (Assembly, Wiring) | PCB | Code**;
- header: back, editable title, Project brief, Saved state, undo/redo (version step), Version history,
  Share, Export project;
- 3D viewer: three.js GLB, grid floor, soft shadow, orbit, bounding-box/dimensions toggle, x-ray and explode;
- Build steps: highlight that step's parts in 3D, with a checklist that is saved;
- Preview has a Tweak drawer (colour, scale, edge roundness, feature nudges, advanced JSON) for direct
  parameter editing, which creates a new version.

Branding: own look, no third-party or Quarktex marks. The design tokens are in `web/app/src/theme.css`:
- ink `#1B1F24`
- paper `#F6F5F1`
- panel `#FFFFFF`
- line `#E4E1D8`
- primary cobalt `#3046D9`
- success `#1F8A5B`
- warning `#B7791F`
- viewport `#ECEAE4`
- Inter for text, JetBrains Mono for code.

## 11. Deploy (project `your-gcp-project`, region `asia-south1`)

- Artifact Registry repo `gstudio`.
- Bucket `gs://gstudio-projects-000000000000` (uniform access, private).
- Service account `gstudio-sa`, with Vertex AI user, bucket object admin, invoker on the builder, and access
  to the key secret.
- Both images are built with Cloud Build (`gcloud builds submit`), never on the laptop.
