# Gadget Studio landing page

A single static page that explains Gadget Studio (working name) to people with an idea for a gadget and no
electronics skills: parents, gift-givers, teachers, makers and small brands in India.

Local files only. Nothing here is published or deployed.

```
landing/
  index.html            the page (all CSS and JS inline, icons are inline SVG)
  assets/shots/         real studio screenshots, cropped and downscaled (WebP)
  assets/img/           AI-generated concept images (JPEG)
  preview-desktop.png   full-page render at 1440 x 900 (headless Chromium on the dev VM)
  preview-phone.png     full-page render at 390 x 844, 2x pixel density
  README.md             this file
```

## Sections, in order

1. **Hero**: "Describe a gadget. We design, build and ship it.", one button, three short promises, the
   bedtime robot picture.
2. **How it works**: Prasanna's six-step dark card. Titles and sub-lines verbatim, in order: Imagine,
   Engineer, Manufacture, Receive ("Unbox it, switch it on, connect it"), Modify anytime, Deploy and repurpose.
   Steps 1-2 show real studio screens; steps 3-6 show AI-generated pictures.
3. **A real example**: the benchmark request as a chat bubble, then three real screens (questions, concepts,
   3D design to real size), the delivered price estimate, and one line on what else the request produced.
4. **Ideas**: who it is for, and three example designs from the studio (Momo, Nimbus, Mochi).
5. **Questions**: five short answers.
6. **Final call to action** and footer (says which pictures are AI-generated and that prices are estimates).

## Preview

Open `index.html` in a browser (on a Mac: `open index.html`). No build step and no server needed.
The Inter font loads from Google Fonts; offline, the page falls back to the system sans-serif.

## STUDIO_URL: where the buttons go

Every "Start with an idea" link carries `data-studio-link`. A small inline script at the bottom of
`index.html` sets their `href` from one constant:

```js
var STUDIO_URL = "/app";
```

The default `/app` assumes the web app will serve this page and mount the studio at `/app`. Change this one
value when that is wired. The links also have `href="/app"` in the HTML, so they work without JavaScript.

## Where each asset came from

### Real screenshots (`assets/shots/`)

Source paths are under `studio/_out/`. Most were cropped by `_out/landing/crop.py`; the two step screens were
re-cropped to 16:10 by `_out/landing/review-crop.py` (both run on the dev VM).

| File | Source | Shows |
|---|---|---|
| `questions.webp` | `r1-web-frontend/shots/80-dock-questions.png` | The two clarifying questions with tap-to-answer options |
| `concepts.webp` | `e2e/shots/04-chat-concepts.png` | The six-concept picker, Sleepy Bear selected |
| `design-3d.webp` | `e2e/shots/14-preview-dims-sizeref.png` | 3D preview with dimensions and an apple for size |
| `price.webp` | `e2e/shots/30-order.png` | Order panel: ₹18,999 estimate, 10-24 days (hidden on phones, where it is unreadable; the caption carries the same numbers) |
| `step-imagine.webp` | `e2e/shots/00-home.png` | Studio home: heading, prompt box and idea chips. The 940x380 crop sits on a 16:10 canvas of the screen's own background colour; the "Your projects" list is left out, no screen pixel is changed |
| `step-engineer.webp` | `e2e/shots/13-preview-transparent.png` | 3D preview with a see-through shell, cropped tight around the model |

Left out on purpose:

- The board, firmware-code and build-guide screens (now in `_out/landing/unused-shots/`): code and
  part labels such as "SH1106, I2C" are jargon for this audience. Their facts stay on the page as one line
  of text (plugs that fit one way, 58 of 58 tests, 18-step guide).
- `46-build-wiring.png`, `47-build-wiring-table.png` and `v3/manual/wiring.png`: the microphone is labelled
  as the speaker gasket (known bug).
- `r1-web-frontend/shots/81-dock-concepts.png`: the concept-tier screen, whose concepts are not robots.
- `questions.webp` comes from the earlier `r1-web-frontend` run of the same benchmark request, because the
  v3 run has no shot of the questions before they were answered. Same prompt, same questions; the answers
  chosen differ (that run picked 3 years old; the v3 order was for a 5-year-old).

### AI-generated concept images (`assets/img/`)

Model: **gemini-3-pro-image** (Nano Banana Pro) on Vertex AI, global endpoint, `generateContent` with TEXT+IMAGE
output at 2K, called from the dev VM. It was picked by a probe on 2026-11-01: it is the only Pro-tier image
model that answers on this project (Imagen 4/5 and newer Pro image ids return 404). Probe record:
`_out/landing/probe.json`. Prompts: `SPECS` and `EXTRAS` in `_out/landing/gen_images.py`, logged in
`_out/landing/gen-log.jsonl`; final conversions in `_out/landing/final-log.jsonl`; raw outputs in
`_out/landing/raw/`. Every image was made from a reference render of a real studio design, and each one
carries an "AI-generated" tag on the page.

| File | Reference render | Notes |
|---|---|---|
| `hero-bedtime-robot.jpg` | `e2e/v3/cad/renders/hero.png` (Sleepy Bear v3, the benchmark build) | Attempt 2 of 2 |
| `step-manufacture.jpg` | `e2e/v3/cad/renders/hero.png` + `e2e/v3/pcb/board-iso.png` | Attempt 3, plus a median-filter retouch that removed garbled board lettering; generic green board, not our purple one |
| `step-receive.jpg` | `e2e/v3/cad/renders/hero.png` | Attempt 3 |
| `step-modify.jpg` | `e2e/v3/cad/renders/hero.png` | Attempt 1 |
| `step-repurpose.jpg` | `e2e/v3/cad/renders/hero.png` | Attempt 1 |
| `gallery-bedtime-robot.jpg` | `examples/bedtime-robot/_out/cad/renders/hero.png` (Momo) | Different design from the hero's Sleepy Bear |
| `gallery-cloud-lamp.jpg` | `examples/cloud-lamp/_out/cad/renders/hero.png` (Nimbus) | "NIMBUS" is engraved on the real design (`spec.json` decor) |
| `gallery-desk-buddy.jpg` | `examples/desk-buddy/_out/cad/renders/hero.png` (Mochi) | Attempt 2 |

No human faces anywhere: hands only in the manufacture and receive pictures; the only faces are the
gadgets' own screen eyes and printed decorations.

## Every number and claim on the page, and its source

Paths are under `studio/_out/e2e/v3/` unless noted.

| On the page | Source |
|---|---|
| About ₹18,999 for one unit, delivered in India, GST included | `order/quote.json`: `delivered_price_inr.pilot_one_off` = 18999, `includes` list |
| Estimate, pending review; nothing charged or reserved | `quote.json` `status` = `ESTIMATE_REVIEW_REQUIRED`, `binding` = false; the Order panel text |
| About 10 to 24 days to arrive | `quote.json` `lead_time_days` low 10, high 24 |
| ₹11,499 each at 100+ a month (only inside the price screenshot) | `quote.json` `delivered_price_inr.steady_state` |
| USB-C phone charger not in the box | `quote.json` `excludes`: USB-C power adapter |
| About 142 × 110 × 188 mm | `BUILD_GUIDE.md` ("about 142 mm wide × 110 mm deep × 188 mm tall") |
| 18-step illustrated build guide | `BUILD_GUIDE.md` ("The build has 18 steps") |
| 58 of 58 software tests passed | `SOFTWARE_SETUP.md` "Native unit tests: 58/58 passed"; `firmware/test-results.txt` |
| Six concepts, Sleepy Bear picked | `e2e/shots/04-chat-concepts.png` |
| Plugs fit only one way | `manual/wiring.png` notes ("Every JST-PH plug is keyed and fits one way") |
| Connect to home Wi-Fi from your phone; parent PIN | `SOFTWARE_SETUP.md` section 2 |
| New version runs on trial, rolls back by itself; favourites kept apart | `SOFTWARE_SETUP.md` section 6; `firmware/src/gs_ota.cpp` |
| Mic on only while the button is held and the light is blue | `SOFTWARE_SETUP.md` section 3 |
| Voice clips stay on the memory card; nothing uploaded | `SOFTWARE_SETUP.md` section 4 and "Local control API" |
| Child-directed designs get extra safety and privacy review | `quote.json` reasons (child-directed and local voice capture review) |
| Download the parts list, build guide and design files | `web/app/src/components/Header.tsx` "Export project" |
| Momo, Nimbus and Mochi names and one-line descriptions | `studio/examples/*/spec.json` `name` and `tagline` |

No testimonials, customer counts, logos, ratings or invented statistics.

## Honest gaps to keep in mind

- Nothing has been built physically yet. `SOFTWARE_SETUP.md` says the firmware was "not flashed to a physical
  board in this build". The page describes the process and shows real design outputs; it never says a unit
  has shipped.
- 88 of 90 digital checks passed in the v3 build (two failed). The page does not mention the check count.
- Updates over Wi-Fi are off in the current build because no signing key was provided; USB flashing works.
  The page only claims trial start-up and automatic rollback, which the firmware already has.
- The blueprint (`docs/blueprint-v1.0.md`, "Recommended launch decision") says to avoid child-directed toys at
  launch. The page leads with the bedtime robot because it is the benchmark; decide before this goes public.
- The app's idea chips ("Cloud rain lamp", "Desk buddy with animated eyes") are different prompts from the
  example designs pictured, so the idea cards describe the pictured designs and do not link to a chip.

## Technical notes

- Valid HTML5, `lang="en-IN"`, own doctype and charset; no frameworks.
- Checked with headless Chromium on the dev VM at 1440 x 900 and 390 x 844 (`_out/landing/review-shoot.mjs`):
  no horizontal scroll, no failed requests, every image loads, Inter loads.
- Every image has `width`, `height` and alt text. All images are lazy-loaded except the hero.
- `prefers-reduced-motion` turns off transitions and smooth scrolling.
- One accent colour (green `#22C55E`; `#15803D` for green text on the light background), one typeface (Inter).
- Weight: about 1.5 MB for the HTML and the 14 images it uses (8 AI images about 1.4 MB, 6 screenshots
  about 0.1 MB), plus the Inter webfont from Google Fonts.
