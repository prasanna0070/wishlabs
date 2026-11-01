# Gadget Studio landing page

A single static page that explains Gadget Studio (working name) to people with an idea for a gadget and no
electronics skills: parents, gift-givers, teachers, makers and small brands in India.

Local files only. Nothing here is published or deployed.

```
landing/
  index.html            the page (all CSS and JS inline, icons are inline SVG)
  assets/shots/         real studio screenshots, cropped and downscaled (WebP)
  assets/img/           AI-generated scenes made from real design renders (JPEG)
  assets/img/ideas/     life + studio photo for each of the six example designs (JPEG)
  designs/<slug>/       the six example designs: spec.json, render.png (engine preview), preview.glb
  preview-desktop.png   full-page render at 1440 x 900 (headless Chromium on the dev VM)
  preview-phone.png     full-page render at 390 x 844, 2x pixel density
  README.md             this file
```

## Sections, in order

1. **Hero**: "Describe a gadget. We design, build and ship it." (unchanged), one button, three short promises,
   then a lineup photo of all six example designs on one table, with a caption naming them left to right.
2. **How it works**: Prasanna's six-step dark card. Titles and sub-lines verbatim, in order: Imagine,
   Engineer, Manufacture, Receive ("Unbox it, switch it on, connect it"), Modify anytime, Deploy and repurpose.
   Steps 1-2 show real studio screens. Steps 3-6 each show a different gadget: the book light being assembled,
   the love-note heart unboxed, the desk bot beside a phone chat, the medicine reminder showing an update ring.
3. **A real example**: the benchmark request as a chat bubble, three real screens (questions, concepts,
   3D design to real size), the illustrative delivered price, and one line on what else the request produced.
   Kept as it was, honesty labels included; only the price card's layout changed (two columns on wide screens,
   so the card is no longer half empty).
4. **Ideas**: "Different people. Very different gadgets." Six cards (3 columns on desktop, 2 on tablets, 1 on
   phones). Each card has the life photo (the studio photo shows on hover; it is also the fallback if the life
   photo fails), an audience chip, the name, the one-liner, and "You might type" with an example sentence.
   Under the grid: "Example designs made with our design engine; photos are AI-generated scenes of those designs."
5. **Questions**: five short answers (unchanged).
6. **Final call to action** and footer (says which pictures are AI-generated and that prices are estimates).

Why: Prasanna's feedback on the previous version was "every thing look like same, have different gadgets like
creatives: once one is like a bot, other one can be a book light with speaker, the other one might be an ai gift
to his girlfriend something". Every pastel creature with ears is gone from the hero, the steps and the ideas; the
only creature left is the benchmark bedtime robot inside "A real example", because those are real screens.

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

## The six example designs (`designs/`)

Each one was designed as a `gadget-spec/1` JSON and checked with the FROZEN engine copy
(`projects/consumer-hardware-platform-stable/studio`, run on the dev VM with `PYTHONPATH=$PWD/engine`):
`gstudio validate` then `gstudio preview`. `render.png` is that preview render, byte-identical to
`_out/designs/<slug>/preview.png` in the stable copy. Only validate and preview ran: there is no full build,
no CAD internals check, no firmware compile and no INR price for any of these six.

| Slug | Name | Audience chip | Validate | Preview |
|---|---|---|---|---|
| `desk-bot` | Bolt Desk Bot | For your desk | ok, 0 errors, 0 warnings | ok, 54.7 s, 1 warning (grille clipped to the speaker opening) |
| `book-light` | Folio Book Light | For readers | ok, 0 errors, 0 warnings | ok, 51 s, 1 warning (bottom edge round reduced) |
| `love-note` | Love Note Heart | For someone you love | ok, 0 errors, 0 warnings | ok, 39 s, no warnings |
| `pill-reminder` | Dose Medicine Reminder | For grandparents | ok, 0 errors, 2 warnings (buzzer hint; RTC coin cell vs the no-battery rule) | ok, 24.7 s, 1 warning |
| `shop-greeter` | Swagat Shop Greeter | For your shop | ok, 0 errors, 1 warning (light sensor hint) | ok, 31.9 s, no warnings |
| `focus-cube` | Pomo Focus Cube | For exam season | ok, 0 errors, 1 warning (light sensor hint) | ok, 15.7 s, no warnings |

Names come from each `spec.json` `name`. The one-liners and the "You might type" sentences were written by the
design agents with each design (the sentence is an example of what a customer could type, not the input that
produced the design; the designs were written as specs directly). Work files for each design are in the stable
copy under `studio/landing/designs/<slug>/`.

## Where each asset came from

### Real screenshots (`assets/shots/`)

Source paths are under `studio/_out/`. Most were cropped by `_out/landing/crop.py`; the two step screens were
re-cropped to 16:10 by `_out/landing/review-crop.py` (both run on the dev VM). All six step pictures sit in
16:10 boxes; the 3:2 AI step pictures lose about 3% at top and bottom, the screenshots are shown whole.

| File | Source | Shows |
|---|---|---|
| `questions.webp` | `r1-web-frontend/shots/80-dock-questions.png` | The two clarifying questions with tap-to-answer options |
| `concepts.webp` | `e2e/shots/04-chat-concepts.png` | The six-concept picker, Sleepy Bear selected |
| `design-3d.webp` | `e2e/shots/14-preview-dims-sizeref.png` | 3D preview with dimensions and an apple for size |
| `step-imagine.webp` | `e2e/shots/00-home.png` | Studio home: heading, prompt box and idea chips. The 940x380 crop sits on a 16:10 canvas of the screen's own background colour; the "Your projects" list is left out, no screen pixel is changed |
| `step-engineer.webp` | `e2e/shots/13-preview-transparent.png` | 3D preview with a see-through shell, cropped tight around the model |

Left out on purpose (unchanged from the first version): the board, firmware-code and build-guide screens
(jargon for this audience; now in `_out/landing/unused-shots/`), the wiring screens with the known microphone
label bug, and the concept-tier screen. `questions.webp` comes from the earlier `r1-web-frontend` run of the same
benchmark request (same prompt and questions; that run picked 3 years old, the v3 order was for a 5-year-old).

### AI-generated scenes (`assets/img/`)

Model for all of them: **gemini-3-pro-image** (Nano Banana Pro) on Vertex AI, global endpoint,
`generateContent` with TEXT+IMAGE output at imageSize 2K, called only from the dev VM (main project), with the
engine preview render(s) as reference images at 1024 px or less. Every one carries an "AI-generated" tag on the
page. No human faces in any of them: hands and forearms only. I looked at every attempt and zoomed into the
gadgets, text and backgrounds before choosing.

Generator: `studio/landing/designs/_page/gen_page.py` in the stable copy (prompts in `SPECS`, corrections per
attempt in `EXTRAS`). Every call is logged in the stable copy's `studio/_out/page/gen-log.jsonl` (11 calls), the
final conversions in `studio/_out/page/final-log.jsonl`. The chosen raw PNGs are copied to
`studio/_out/landing/page-raw/` in this tree.

| File | Gadget and reference | Attempt used | What to know |
|---|---|---|---|
| `hero-lineup.jpg` (2400 x 1350, 360,868 bytes) | All six; references: an exact layout plus the six renders | 2 of 3 | See the hero note below |
| `step-manufacture.jpg` (1200 x 800) | Folio Book Light (`book-light` render) | 1 of 1 | The shell matches the render. The electronics on the tray are illustrative: a generic purple board and dev board with no lettering, a speaker, and an LED ring drawn larger and with more LEDs than the real 12-LED, 50 mm ring. An unbranded 3D printer behind. A filament spool at the right edge has a small out-of-focus label with nothing legible |
| `step-receive.jpg` (1200 x 800) | Love Note Heart (`love-note` render) | 3 of 3 | Attempts 1 and 2 laid the heart down in the box and distorted it. Attempt 3 stands it upright in front of the open kraft box, untied ribbon in front; its base reads a little narrower than in the render |
| `step-modify.jpg` (1200 x 800) | Bolt Desk Bot (`desk-bot` render) | 2 of 2 | Attempt 1 was mirrored (the orange button on the wrong side of the head), so it was rejected. The screen shows happy closed-eye arcs instead of the render's idle face, to show a change. The phone shows chat bubbles only, with no text, icons or status bar |
| `step-repurpose.jpg` (1200 x 800) | Dose Medicine Reminder (`pill-reminder` render) | 2 of 2 | Attempt 1 looked like a 3D render and had a sage cap. The screen shows an update ring in place of the idle face. Where the USB-C cable enters is illustrative |

**Hero note.** A first attempt that passed only the six renders invented six different gadgets (a rounded
astronaut robot, a book with the window on its cover, a boxy pill), so it was rejected. For attempts 2 and 3,
`studio/landing/designs/_page/lineup_layout.py` (stable copy) first built an exact layout: it used the frozen
engine's own renderer camera (`gstudio.cad.render._camera`, the preview's direction and size) to draw each
`preview.glb` as a flat silhouette, used that silhouette as the alpha mask over the unchanged `preview.png`
pixels, and placed the six cut-outs at true relative scale (2.05 px per mm) on flat stand-in colours
(`studio/_out/landing/page-raw/hero-layout.png`). Nano Banana was then asked to keep every gadget as placed and
only replace the surroundings and the light. Attempt 3 changed the heart to terracotta and turned the shop sign's
light window into a through-hole, so attempt 2 is used. What attempt 2 still gets wrong, all small at page size:
the hard late-afternoon sun throws cast shadows across the heart and the capsule's white end (which reads a
little grey); the robot's screen eyes are blue with highlights (the render's idle face, itself a render-only
decal, has white eyes with dark pupils); the heart's small engraved touch circle came out as a rounded square;
the capsule's screen shows a pill icon instead of the render's idle face (asked for, to match its ideas photos).
The hero box on the page trims plain wall from the top on phones (2:1) and desktops (21:9); no gadget is cut.

### Ideas photos (`assets/img/ideas/`)

Made by the six design agents with the same model and call shape, each from its own engine render as the
reference. Each design has a `-studio.jpg` (1200 x 1200) and a `-life.jpg` (1600 x 1200). Generator scripts are
in the stable copy under `studio/landing/designs/<slug>/` (under `work/` for pill-reminder and shop-greeter); raw PNGs are in `studio/_out/landing/ideas-raw/`.

| Design | Studio | Life | Notes from the design agent |
|---|---|---|---|
| desk-bot | attempt 2 of 3 | attempt 3 of 3, plus a recorded patch that removed two blurred tray icons from the out-of-focus monitor (scene only) | Monitor wallpaper is abstract blue ribbons with no logo or text |
| book-light | attempt 1 | attempt 1 | Window shown glowing warm |
| love-note | attempt 1 | attempt 2 (attempt 1 mirrored the gadget; one call returned no image; one replacement invented a different gadget) | Engraved "A + R" is real spec text |
| pill-reminder | attempt 1 | attempt 2, plus a recorded median filter on six small organiser tabs (scene only) | Screens show a pill icon, not the render's idle face |
| shop-greeter | attempt 1 | attempt 2 (attempt 1 had a blurred person at the frame edge) | "KIRAN STORES" and "WELCOME" are real spec text |
| focus-cube | attempt 2 of 3 | attempt 1 | Rounded edges drawn a little crisper than the render |

### Retired assets

The bedtime-robot hero, the three creature gallery images (Momo, Nimbus, Mochi) and the four old step images
(all showing the teal Sleepy Bear) are no longer used. They were moved, not deleted, to
`studio/_out/landing/retired-assets/`; their prompts and raws stay in `studio/_out/landing/gen_images.py` and
`studio/_out/landing/raw/`.

## Every number and claim on the page, and its source

Paths are under `studio/_out/e2e/v3/` unless noted.

| On the page | Source |
|---|---|
| ₹7,826, "Illustrative price" | Prasanna's choice on 2026-11-01 (see "Price shown on the page" below); not an engine quote |
| Estimate, pending review; nothing charged or reserved | `quote.json` `status` = `ESTIMATE_REVIEW_REQUIRED`, `binding` = false; the Order panel text |
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
| The six design names | `landing/designs/<slug>/spec.json` `name` |
| The six one-liners, audience chips and "You might type" sentences | Written by the design agents with each design (see "The six example designs"); behaviour as in each `spec.json` `behaviour.summary` |

No testimonials, customer counts, logos, ratings or invented statistics.

## Honest gaps to keep in mind

- Nothing has been built physically yet. `SOFTWARE_SETUP.md` says the firmware was "not flashed to a physical
  board in this build". The page describes the process and shows design outputs; it never says a unit has shipped.
- The six example designs are shells that validate and render. Some described behaviours need firmware the
  current templates do not have yet:
  - Love Note Heart: no template receives a new voice note from a phone over the internet; notes must be on the
    microSD card, and "glow when you message her" needs the phone on the same Wi-Fi. The glow window is round,
    not heart-shaped.
  - Dose Medicine Reminder: the spoken dose reminder is not in the `notifier_clock` template (chime and light
    are). Its clock module carries a coin cell, which breaks the blueprint's no-battery launch rule. It is big
    (about 248 × 132 × 104 mm).
  - Swagat Shop Greeter: nothing in `ambient_light` plays a chime on motion yet.
  - Pomo Focus Cube: no focus-timer template exists; flip-to-start, the countdown ring and the break chime need
    template work.
  - Folio Book Light: the rotary knob is not supported on backplane-v1, so it has one button; volume and glow are
    set on its local Wi-Fi page.
  - Bolt Desk Bot: the Pololu ToF sensor is import-only in India and Indian stock of the 2.0 inch LCD is not
    confirmed. It is about 130 × 104 × 194 mm, larger than commercial desk bots.
  - Every design is chunky because the backplane-v1 cartridge sets the floor size.
- 88 of 90 digital checks passed in the v3 benchmark build (two failed). The page does not mention the check count.
- Updates over Wi-Fi are off in the current build because no signing key was provided; USB flashing works.
  The page only claims trial start-up and automatic rollback, which the firmware already has.
- The blueprint (`docs/blueprint-v1.0.md`, "Recommended launch decision") says to avoid child-directed toys at
  launch. The hero no longer shows the bedtime robot; it is still the "A real example" request because it is the
  benchmark with real screens. Decide before this goes public.
- The app's idea chips in the step 1 screenshot ("Bedtime story robot", "Cloud rain lamp", "Desk buddy with
  animated eyes") are different prompts from the six example designs.

## Technical notes

- Valid HTML5, `lang="en-IN"`, own doctype and charset; no frameworks.
- Checked with headless Chromium on the dev VM (`studio/_out/landing/shoot-range.mjs`, pushed and run by
  `studio/_out/landing/push-range.sh`) at 1440 x 900, 390 x 844 and 375 x 812: no horizontal scroll
  (scrollWidth equals the viewport at all three), no failed requests, every image loads, Inter loads, and the
  studio photo appears on hover. Shots in `studio/_out/landing/shots-range/`.
- Every image has `width`, `height` and alt text (the hover-only studio layers have empty alt). All images are
  lazy-loaded except the hero.
- `prefers-reduced-motion` turns off transitions and smooth scrolling.
- One accent colour (green `#22C55E`; `#15803D` for green text on the light background), one typeface (Inter).
  Audience chips are neutral grey, not a second accent.
- Weight: 3,146,224 bytes (3.15 MB) for the HTML and the 22 image files it uses (hero 361 KB, 4 steps 572 KB,
  12 ideas photos 2.09 MB, 5 screenshots 75 KB), plus the Inter webfont from Google Fonts.

## Price shown on the page
The page shows **₹7,826, labelled "Illustrative price"**. Prasanna chose this on 2026-11-01 as an illustrative figure. It is not an engine quote. The real engine quote for the bedtime-robot benchmark was ₹18,999 one-off (₹11,499 steady state at volume), estimate pending review. The order-panel screenshot that showed ₹18,999 was removed so the page does not contradict itself.
