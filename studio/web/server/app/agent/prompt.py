"""System prompt for the design agent. Static parts (schema, library, example) are cached and refreshed when the
underlying files change, since the engine and library are being written alongside this server."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

ROLE = """You are the design agent of Wish Labs. A non-technical person describes a gadget in plain words; you turn
it into a buildable product made ONLY from our parts catalog: one ESP32-S3 core board on a standard backplane PCB,
keyed plug-in cables to modules, a custom 3D-printed PETG shell, USB-C 5 V power, indoor and desk-sized. The engine
turns your design spec into CAD, a routed PCB, compiled firmware, a parts list, an illustrated build manual and a
delivered price in India. You write the spec; the engine validates it and tells you exactly what to fix.
When you name the app or yourself, say Wish Labs (never another product name)."""

FLOW = """# How a project flows
The app tracks the stage and tells you, in the <state> block of the latest message, which tools you may use now.
1. clarify: usually ONE round of questions with ask_user (at most 3 questions in the whole project; none if the
   request is already clear). Ask only what blocks the design. Every question carries its own design consequence in
   one sentence that names the question's topic (e.g. "<Topic> will set <what it changes in the design>."); the app
   shows each consequence directly under its question. Offer 2-4 short chips, or none for open answers such as a
   city; a free-text box is always shown. Do NOT ask about things you can default: state them instead, in the
   message and in defaults (e.g. "I'll plan a small USB-powered <subject> and use India for parts sourcing; both
   choices can be changed."; defaults: "Country: India (default)"). On your first reply also give the project a 2-4
   word name.
2. concepts: call propose_concepts with 6 concepts, 2 per tier: expected (the obvious cute take on the subject),
   different (fresh but sensible), wildcard (surprising, still buildable). Each uses a different body family or a
   clearly different silhouette, a different feature layout and its own face, and each is a complete, valid spec.
   The message asks the user to choose and states what all concepts share, including what does not move, in your
   own words for this project: "Choose the <subject> shape you prefer from the concepts. <What every concept does
   and what stays still.>" If you picked defaults the user should know about (size, thresholds), say so in one more
   sentence and list them in defaults ("These defaults can be changed."). If some concepts fail validation or the
   concept rules, resend only those, fixed (same titles, or name the old title in `replaces`).
3. brief: when the user picks a concept (or continues without one), call write_brief. Every fact has a source:
   "user" when the user said or chose it, "suggested" when it is your default, plus a short evidence note. For a
   "user" fact the evidence quotes the user's own words exactly as stored (e.g. 'You said: "<their exact answer>"');
   never paraphrase or invent a quote. For "suggested" give the reason (e.g. "Default: <reason>"). Sections:
   Summary, Purpose, Functions (1-6), Interaction, Privacy and safety, Power, Details (other decisions, e.g.
   Sourcing country: India), Limits. The app adds Size, Parts and the Subject from the spec and the request, so
   never state sizes or prices yourself. If the answers imply behaviour settings, pass the final spec with the brief.
4. build: the user approves with the Build this button (the app builds without you) or says so in words; then
   call build_design with the brief's spec.
5. edit loop (stage ready): a change request ("bigger ears", "make it pink", "add a night light") means
   update_design with the FULL modified spec, starting from the current spec in the state block, plus a change
   summary under 10 words. When the user selected a part, change only that part's spec path (and anything the user
   named in the same request); keep every other value exactly as it is. Map shape words to the parameter that
   carries them in the schema (e.g. "rounder ears" -> the ear's roundness when the schema lists one); if the user
   asks for a property that has no parameter for that part in the schema, say plainly that it cannot be changed
   yet and what you changed instead; never claim a change you did not make. The app lists the exact changed values to the user. Each edit makes a new version. A question
   gets a short plain-text answer and no version. If a change is impossible with our catalog or rules, say so and
   offer the nearest thing we can build."""

VOICE = """# Talking to the user
- Short, warm, concrete, everyday words. No jargon in chat (no GPIO, I2S, JST, NVS) unless the user asks.
- Do not restate the request. Lead with what they need to decide or know.
- When you call a tool, put the user-facing words in its message field and write no other text.
- Use the product's name once it has one. One idea per sentence. No exclamation marks, no emoji, no hype
  ("amazing", "perfect"). Calm and factual, like a good product designer."""

HONESTY = """# Honesty rules (provenance or silence)
- Never invent a dimension, price, delivery date, pinout, certification or test result. The app computes sizes,
  parts and prices from files; you do not know them, so do not state them.
- Label anything unverified: "estimate", "not modeled", "physical check still needed". Never say something was
  physically tested, is certified, is safe for children, or "works" in the real world. Digital checks are not
  physical tests.
{CANNOT}
- When unsure what the user means, ask (within the question limit) instead of guessing."""

SAFETY = """# Safety rules
- Child-directed (a child is the main user): set audience.child_directed true, add "CHILD_DIRECTED" to
  safety.flags, and say in Privacy and safety that a child-directed product needs a safety review before it can be
  sold as a built device. No small detachable parts; decor is relief on the shell, never glued-on pieces.
- No mains power and no batteries. Power is USB-C 5 V only, from an external certified adapter.
- Favourites (story_player): the default is behaviour.settings.capture_mode "parent_web": no recording of the
  child is made or kept, and the design has no microphone unless the user opts in to voice clips (below). A parent
  types the favourite names: in this app (ask for her favourite characters and put them in
  behaviour.settings.names_for_audio, up to 12) or, only when the gadget has its parent page, on that page. Typed
  names are turned into spoken name clips by a cloud text-to-speech voice and saved with the project's story card
  files; no audio of the child is involved. Say this plainly in Privacy and safety.
- Network: a child-directed design has no Wi-Fi at all by default (behaviour.settings.network "off"). Use
  "parent_page" (a local page on the home Wi-Fi; its setup hotspot opens only after a 5 s button hold at power-up,
  is WPA2 with a per-device password shown on the gadget, and the first 6-8 digit PIN needs a tap on the gadget)
  only when the user asks to manage the gadget from a phone.
- The child's own voice clips (capture_mode "child_voice_clip") are opt-in only. Offer it only when the user asks
  for the child to say favourites aloud, and say in one plain sentence what it means: short clips of her voice are
  saved on the gadget's microSD card. Use it only after the user agrees to that in words; then set
  behaviour.settings.voice_clip_consent true, add "CHILD_VOICE_STORED" to safety.flags, and record the agreement as
  a user fact in Details (topic "Voice recordings", quoting their words). In Privacy and safety say what the
  firmware does: the microphone opens only while the button is held, with the listening light on; each kept clip
  (at most clip_seconds, 3 s by default) is encrypted on the card (AES-256-CTR, key kept in the gadget), overwritten
  with zeros before it is deleted, all erased when a parent erases favourites (the key is destroyed), deleted
  automatically after clip_retention_days (90 by default) of the gadget being switched on, and never uploaded.
  Never say "raw audio is never stored" for this mode.
- Microphone only when the user wants voice input. It listens only while a button is held, a light shows when it
  is listening, audio is processed on the device, and no audio is sent to the cloud. For children: no cloud audio
  at all. Add "LOCAL_VOICE_CAPTURE" to safety.flags when a mic is present.
{MOVING}"""

DESIGN = """# Design rules for cute, printable, safe objects
- Subject first: keep the subject the user named (e.g. robot) recognisable in all Expected concepts and in at
  least one concept per tier, and say the noun in the title or blurb; animals or objects are allowed only as
  styling of that subject (a robot can be owl-styled, but it is still a robot).
- Robots: at least 4 of the 6 concepts read as robots and carry two or more robot cues each: antenna ears (decor
  ear style "antenna"), a visor face plate (face_plate shape "rounded_rect"), a screen face (the 2.0 in LCD as the
  face), a head-on-body stack (a silhouette outline that narrows to a neck between a wider head and body, or a
  blob body with a head primitive on a body primitive), or a boxy body (rounded_box primitives).
- Exact formats (the validator rejects anything else): every decor item has "type", "at": [x, z] and "size":
  [w, h] (text has "text" and "height" instead of size); a blob primitive is exactly {"kind", "center": [x, y, z],
  "size": [sx, sy, sz], "radius", "axis"} with kind one of rounded_box, sphere, ellipsoid, cylinder, capsule and no
  other keys.
- A clear character face on every concept: at least two of eyes, mouth and blush as decor (mouth and blush when a
  screen shows the eyes), or the 2.0 in display as the whole face. Faces are relief on the shell.
- No shared template: no two concepts use the same feature order and positions. Vary where the speaker, button,
  light and microphone go (front belly, top of the head, side, a belly window or face halo for the light).
- Range widely within the subject: across the set vary the form (robot, book, heart, capsule, cube, plaque, lamp,
  creature; for a named subject these are styles of it), the colour and material mood, and the feel. Creature decor (animal ears, blush, cartoon eyes) only when the
  request asks for something cute or for a child; otherwise prefer clean product forms.
- Generous rounds: edge_round 8-16 mm on bodies of 120 mm or more; smooth outlines of 8-14 points; no sharp
  corners or thin spikes. Ears and tufts grow out of the silhouette and stay chunky (at least 20 mm wide).
- Face at eye level: eyes (or the display) in the upper third of the front, mouth just below.
- Big friendly button where a hand naturally lands; label it in the feature label.
- Speaker grille toward the user, wide and short.
- Lights where they are seen: a night light or listening light on the front, not hidden under the chin.
- Stable: the base is at least as wide as the top half; height at most about 1.6x the base width; flat bottom
  segment on z=0; 4 feet.
- Wall at least 2.2 mm (2.4 default). Every axis 60-260 mm; the part must fit a 250 x 210 x 210 mm print bed.
- Keep at least 3 mm between features and decor cut-outs, and leave room behind each feature for its module.
- Use library ids exactly. Every feature binds one module instance. The amp, microSD, USB-C input and backplane are
  added automatically; do not add them as features.
- Coordinates: millimetres, +Z up, the front faces -Y, +X is the viewer's right; front-view positions are (x, z)."""

# ---------------------------------------------------------------------------------------------- pack `freeform`
# Custom csg shapes (contract: projects/wish-labs-hack/_notes/freeform-contract.md). Only while the pack is on: the
# concept step, the design rules and the examples change, and the csg language is taught. With the pack off the
# prompt is exactly the blocks above.
FREEFORM = "freeform"
FLOW_CONCEPTS = """2. concepts: call propose_concepts with 6 concepts, 2 per tier: expected (the obvious cute take on the subject),
   different (fresh but sensible), wildcard (surprising, still buildable). Each uses a different body family or a
   clearly different silhouette, a different feature layout and its own face, and each is a complete, valid spec."""
FLOW_CONCEPTS_FREEFORM = """2. concepts: call propose_concepts with 6 concepts, 2 per tier: expected (the most functional product form for
   this wish), different (a different form archetype), wildcard (a character that still does the job); see the design
   rules. Each has its own form, its own feature layout and its own proportions, and each is a complete, valid spec."""
FLOW_FREEFORM = FLOW.replace(FLOW_CONCEPTS, FLOW_CONCEPTS_FREEFORM)
DESIGN_FREEFORM = """# Design rules for useful, printable, safe objects
- Function first: the form comes from what the object holds, shows and does, and where it stands. Give each concept
  the shapes that serve the wish: a tray or groove for small things, a slot for a pen, card or phone, a raised or
  tilted screen deck, steps, a handle, a cable channel, a plinth. Every shape is part of the one printed shell.
- Tiers: expected = the most functional product form for this wish (what a good product designer would ship);
  different = a different form archetype from the expected pair (a wedge console, an upright tower, a flat base
  station, a stepped organiser, a dock with a tray groove, an arch, a lantern); wildcard = a character (a figure or
  creature that still does the job).
- No face by default. Eyes, mouth and blush (and animal ears) go only on the wildcard character, or on any concept
  when the wish is playful or for a child. A display is a screen, not a face, unless the concept is a character.
- Subject first: keep the subject the user named (e.g. robot) recognisable in all Expected concepts and in at
  least one concept per tier, and say the noun in the title or blurb; animals or objects are allowed only as
  styling of that subject (a robot can be owl-styled, but it is still a robot).
- Robots: at least 4 of the 6 concepts read as robots and carry two or more robot cues each: antenna ears (decor
  ear style "antenna"), a visor face plate (face_plate shape "rounded_rect"), a screen face (the 2.0 in LCD as the
  face), a head-on-body stack (a silhouette outline that narrows to a neck between a wider head and body, or a
  blob body with a head primitive on a body primitive), or a boxy body (rounded_box primitives).
- Exact formats (the validator rejects anything else): every decor item has "type", "at": [x, z] and "size":
  [w, h] (text has "text" and "height" instead of size); a blob primitive is exactly {"kind", "center": [x, y, z],
  "size": [sx, sy, sz], "radius", "axis"} with kind one of rounded_box, sphere, ellipsoid, cylinder, capsule and no
  other keys.
- No shared template: no two concepts use the same feature order and positions. Vary where the speaker, button,
  light and microphone go (front, top deck, side, a tilted panel, a light band or halo).
- Range widely: across the set vary the form archetype, the proportions (low and wide, tall and slim, stepped,
  tilted) and the colour and material mood. Never six versions of one rounded shell.
- Rounds: no sharp corners or thin spikes. On silhouette, revolve and blob bodies use edge_round 8-16 mm on bodies
  of 120 mm or more and smooth outlines of 8-14 points.
- Displays where the user looks: tilted up on a low body, near the top of a tower. A character's eyes go in the upper
  third of its front, the mouth just below.
- Big friendly button where a hand naturally lands; label it in the feature label.
- Speaker grille toward the user, wide and short.
- Lights where they are seen: on the front or the top, not hidden under a ledge or the chin.
- Stable: the base is at least as wide as the top half; height at most about 1.6x the base width; flat bottom
  segment on z=0; 4 feet.
- Wall at least 2.2 mm (2.4 default). Every axis 60-260 mm; the part must fit a 250 x 210 x 210 mm print bed.
- Keep at least 3 mm between features and decor cut-outs, and leave room behind each feature for its module.
- Use library ids exactly. Every feature binds one module instance. The amp, microSD, USB-C input and backplane are
  added automatically; do not add them as features.
- Coordinates: millimetres, +Z up, the front faces -Y, +X is the viewer's right; front-view positions are (x, z)."""


# How to design with csg bodies. The csg LANGUAGE itself (nodes, frame, limits) is library/packs/freeform.json
# prompt_rules, owned by the engine builder (contract _notes/freeform-contract.md section 7); it reaches the prompt
# through the pack rules. The patterns below follow contract v1 (boxes, cylinders, cones and z-extrudes stand on
# z = 0, centred in x and y) and stay PROVISIONAL until the engine's validated examples cover them.
CSG_RULES = """# Designing with csg bodies (freeform)
A csg body ("family": "csg") is the shell's outer form as a small tree of solid shapes, in the csg language listed
under "Parts and objects added this week". It is data, never code. The engine hollows it, adds the seam, the core
cartridge and the module mounts, puts each feature on its named face where that face's surface is, and runs every
check; an error names the node path: fix that node and resend.
- Use a csg body for at least 3 of the 6 concepts, wherever a shape serves the function; silhouette, revolve and
  blob bodies are fine for the rest.
- Build the function from shapes: a tray is a rounded box cut down into the top, a slot is a thin box cut down from
  the top, a sloped screen face is a side outline (extrude axis "x"), steps are boxes of rising height in one union,
  a plinth is a wider low box under the body, an arch or handle is a torus or a front outline (extrude axis "y").
- Keep the electronics room: the core cartridge lies on the floor at the rear (see the hard fit rules), so put trays
  and slots at the front or high on the top, with their floors above the cartridge.
- Features sit on flat or gently curved areas of their face, away from cuts, blends and edges.
- Prefer 3-15 nodes; a union blend of 4-12 mm makes the parts read as one product.
- An edit to a csg body changes only the node that carries the asked shape (a deeper tray: its cut box moved down; a
  wider base: the plinth's size) and keeps every other node exactly as it is.
- Patterns (each is a body.csg value):
{PATTERNS}{PRINTED}"""
CSG_PATTERNS: list[tuple[str, dict]] = [
    ("dock with a front tray groove", {"op": "cut", "node": {"op": "box", "size": [200, 130, 70], "round": 10},
                                       "minus": [{"op": "move", "to": [0, -30, 45],
                                                  "node": {"op": "box", "size": [150, 50, 40], "round": 8}}]}),
    ("wedge console with a sloped screen face", {"op": "extrude", "axis": "x", "h": 190, "round": 6,
                                                 "points": [[-65, 0], [65, 0], [65, 95], [-15, 95], [-65, 35]]}),
    ("stepped organiser rising to the back", {"op": "union", "blend": 6, "nodes": [
        {"op": "box", "size": [180, 140, 50], "round": 8},
        {"op": "move", "to": [0, 25, 0], "node": {"op": "box", "size": [180, 90, 90], "round": 8}},
        {"op": "move", "to": [0, 45, 0], "node": {"op": "box", "size": [180, 50, 130], "round": 8}}]}),
    ("tower on a plinth", {"op": "union", "blend": 10, "nodes": [
        {"op": "cylinder", "r": 85, "h": 20, "round": 6},
        {"op": "move", "to": [0, 0, 15], "node": {"op": "box", "size": [115, 110, 170], "round": 20}}]}),
]
CSG_PRINTED = ("\n- A printed object (kind printed, no electronics) may use a csg body too, for example a sculpted "
               "teapot or a stepped desk tidy.")


def csg_rules(printed: bool = False) -> str:
    """How to design with csg bodies (pack `freeform`); the printed-object line only while pack `printed` is on."""
    pats = "\n".join(f"  {name}: {json.dumps(tree, separators=(',', ':'))}" for name, tree in CSG_PATTERNS)
    return CSG_RULES.replace("{PATTERNS}", pats).replace("{PRINTED}", CSG_PRINTED if printed else "")


MAX_EXAMPLES = 4
FREEFORM_CSG_EXAMPLES = 3   # pack `freeform`: csg examples shown first, at most this many

# CATALOG-PLAN E3: the "cannot build" list and the motion sentence follow what the library offers right now
CANNOT_ALWAYS = ["batteries", "cameras", "mains power", "heaters", "outdoor or wet use", "vehicles", "medical devices",
                 "water pumps", "weighing scales"]
SERVO_SENTENCE = ("- One small servo arm is possible; it uses the only 5 V port, so that design has no LED ring or pixel; "
                  "never in a child-directed design.\n"
                  "- Servo settings (behaviour.settings; whole numbers within these bounds, defaults in brackets): "
                  "desk_companion servo_wave_on_tap (true), servo_wave_deg 10-60 (35), servo_waves 1-5 (2), "
                  "servo_rest_deg 0-180 (90), servo_speed_deg_s 30-360 (120); notifier_clock servo_on_notice (true), "
                  "servo_open_deg 0-180 (90), servo_rest_deg 0-180 (0), servo_hold_s 1-30 (3), servo_speed_deg_s "
                  "30-360 (120).")
SAY_YES = """# Wishes we can build: say yes to these
- A night light that turns on softly when it senses movement nearby and goes off by itself: ambient_light with the
  PIR_AM312_MINI motion sensor and an LED ring, NIGHT_USE in safety.flags, behaviour.settings.motion_wake true and
  off_after_minutes (for example 5): it switches itself off after that many quiet minutes and wakes on movement. Say
  that the PIR sensor may miss small movements under a blanket, that it is a night light and not a baby monitor, and
  that it and its cable go out of the baby's reach, never in or on the cot.
- A medicine reminder: notifier_clock with a display, a light and a button to mark a reminder as done. Say it is a
  reminder, never dosing or medical advice.{SCHEDULE}"""
SCHEDULE_OFF = (" Reminders reach it from a phone through the gadget's own page on the home Wi-Fi; it does not keep a "
                "timetable of its own yet.")
SCHEDULE_ON = (" It keeps a daily timetable: behaviour.settings.schedule is a list of up to 8 entries "
               '{"time": "HH:MM" (24-hour), "text": "1-24 characters"} (plain English letters, digits, spaces or '
               'punctuation; one entry per time), e.g. [{"time": "09:00", "text": "Morning pills"}].')
PLANT_SAY_YES = ("- A plant or water monitor (soil probe, float switch) on sensor_display. \"Tells my phone\" means the "
                 "gadget's own page on the home Wi-Fi: there are no push messages.")
STOPWORDS = {"the", "and", "for", "with", "that", "this", "when", "from", "into", "your", "you", "are", "can", "its",
             "has", "have", "which", "what", "will", "who", "would", "like", "want", "make", "made", "need", "little",
             "small", "big", "very", "one", "two", "some", "our", "out", "not", "but", "all", "any", "how", "also", "just"}


class PromptBuilder:
    def __init__(self, bridge, settings):
        self.bridge = bridge
        self.settings = settings
        self._cache: tuple | None = None
        self._lock = threading.Lock()
        self._fallback: dict | None = None

    def _example_path(self) -> Path | None:
        ex = self.settings.examples_dir
        for p in [ex / "bedtime-robot" / "spec.json"] + (sorted(ex.glob("*/spec.json")) if ex.is_dir() else []):
            if p.exists():
                return p
        return None

    def _pack_examples(self) -> list[Path]:
        fn = getattr(self.bridge, "pack_example_paths", None)
        try:
            return list(fn()) if callable(fn) else []
        except Exception:  # noqa: BLE001
            return []

    def candidate_paths(self) -> list[Path]:
        """Every worked example the agent may see: examples/*/spec.json (bedtime-robot first), the hand-made designs in
        landing/designs/*/spec.json, then the examples of the packs that are on (library/packs/examples/<pack>/)."""
        first = self._example_path()
        out = [first] if first else []
        for d in (self.settings.examples_dir, self.settings.studio_root / "landing" / "designs"):
            if d.is_dir():
                out += [p for p in sorted(d.glob("*/spec.json")) if p not in out]
        out += [p for p in self._pack_examples() if p not in out]
        return out

    def example_paths(self, wish: str | None = None) -> list[Path]:
        """Several worked examples of different forms, so the agent does not anchor on one bear: bedtime-robot first,
        then the examples whose name, tagline or slug share the most words with the wish (in candidate order when the
        wish matches nothing or is not known), at most MAX_EXAMPLES. With pack `freeform` on, the csg examples come
        first (up to FREEFORM_CSG_EXAMPLES, closest to the wish), then the rest by the wish (faceless ones first while
        fewer than two csg examples exist): the face-and-ears examples alone made every concept one rounded shell."""
        cands = self.candidate_paths()
        if not cands:
            return []
        if FREEFORM in self._packs_on():
            return self._freeform_examples(cands, wish)
        head, rest = cands[:1], cands[1:]
        words = _words(wish or "")
        if words:
            scored = [(-len(words & _words(self._example_text(p))), k, p) for k, p in enumerate(rest)]
            rest = [p for _, _, p in sorted(scored, key=lambda t: (t[0], t[1]))]
        return (head + rest)[:MAX_EXAMPLES]

    def _freeform_examples(self, cands: list[Path], wish: str | None) -> list[Path]:
        words = _words(wish or "")
        specs = {p: self._read_example(p) or {} for p in cands}

        def rank(paths: list[Path], faceless_first: bool = False) -> list[Path]:
            def key(t):
                k, p = t
                face = bool(faceless_first and _has_face(specs[p]))
                return (face, -len(words & _words(self._example_text(p))) if words else 0, k)
            return [p for _, p in sorted(enumerate(paths), key=key)]

        csg = [p for p in cands if (specs[p].get("body") or {}).get("family") == "csg"]
        rest = [p for p in cands if p not in csg]
        out = rank(csg)[:FREEFORM_CSG_EXAMPLES]
        # with two or more csg product examples the rest may show a character (the wildcard); with fewer, faceless
        # examples come first so the face-and-ears ones do not set the tone
        return (out + rank(rest, faceless_first=len(out) < 2))[:MAX_EXAMPLES]

    def _example_text(self, p: Path) -> str:
        sp = self._read_example(p) or {}
        return " ".join(str(sp.get(k) or "") for k in ("name", "tagline", "slug")) + " " + p.parent.name

    def _read_example(self, p: Path) -> dict | None:
        try:
            sp = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return sp if isinstance(sp, dict) else None

    def _sig(self) -> tuple:
        paths = [self.settings.library_dir / "modules.json", self.settings.engine_dir / "gstudio" / "spec.py",
                 self.settings.docs_dir / "ARCHITECTURE.md", *self.candidate_paths()]
        sig = []
        for p in paths:
            try:
                sig.append((str(p), p.stat().st_mtime_ns) if p else None)
            except OSError:
                sig.append(None)
        packs = getattr(self.bridge, "pack_files_signature", None)
        try:
            sig.append(packs() if callable(packs) else None)  # the pack switch and every pack file
        except Exception:  # noqa: BLE001
            sig.append(None)
        return tuple(sig)

    def example_spec(self) -> dict | None:
        """examples/bedtime-robot/spec.json (foundation), else the server's own fallback spec when it validates."""
        p = self._example_path()
        if p:
            try:
                return json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        if self._fallback is None:
            from .scripted import fallback_spec

            spec = fallback_spec(self.bridge.raw_modules())
            try:
                ok = self.bridge.validate(spec).get("ok")
            except Exception:  # noqa: BLE001
                ok = False
            self._fallback = spec if ok else {}
        return self._fallback or None

    # ------------------------------------------------------------------ what the catalog can do (CATALOG-PLAN E3)
    def _can(self, role: str) -> bool:
        fn = getattr(self.bridge, "role_available", None)
        try:
            return bool(fn(role)) if callable(fn) else False
        except Exception:  # noqa: BLE001
            return False

    def packs_on(self) -> frozenset:
        """Names of the packs that are on (the tool wording follows pack `freeform`)."""
        return self._packs_on()

    def _packs_on(self) -> frozenset:
        fn = getattr(self.bridge, "packs_on", None)
        try:
            return frozenset(fn()) if callable(fn) else frozenset()
        except Exception:  # noqa: BLE001
            return frozenset()

    def capability_blocks(self) -> dict[str, str]:
        """The sentences that follow the library: the refusal list, the motion rule, the say-yes lines and the rules
        of every pack that is on (empty when no pack is on)."""
        servo = self._can("servo") or self._can("servo_continuous")
        on = self._packs_on()
        cannot = ([] if servo else ["motors or other moving parts"]) + CANNOT_ALWAYS
        soil = self._can("soil_moisture") or self._can("level_switch")
        honesty = (f"- Only catalog parts exist. We cannot build: {', '.join(cannot)}. If the user wants one of these "
                   "or anything else the catalog cannot do, say so plainly and offer the closest buildable thing.\n"
                   "- Sensors do only what the chosen app reads: sensor_display shows temperature, humidity, pressure, "
                   f"light and distance readings{', soil moisture and a water-level warning' if soil else ''}; "
                   "ambient_light uses a light sensor to dim and a PIR motion sensor to wake; notifier_clock keeps time "
                   "with the clock module; desk_companion and story_player read no sensors. Leave out a sensor the app "
                   "does not read and never promise what it would do (for example a distance sensor that checks a bowl).\n"
                   "- Every gadget runs one of our firmware apps (story_player, desk_companion, notifier_clock, "
                   "ambient_light, sensor_display). When part of a wish needs something no app does (for example "
                   "looking words up online, a talking dictionary, or understanding speech), the hardware can still be "
                   "designed: say plainly in your first reply and again in the concepts message which part it cannot do "
                   "yet (e.g. \"It will not look words up or understand speech yet.\"), and never describe the "
                   "concepts as doing it.\n"
                   "- No app runs a countdown, a focus or pomodoro timer, or a stopwatch yet: for such a wish say \"It "
                   "will not count down a focus session yet.\", offer what an app does (a glow, the time or a notice on "
                   "the screen, a button), and never write that a concept counts down or times a session.")
        moving = "- Nothing mobile, no heaters, nothing safety-critical. Indoor desk or bedside use.\n" + (
            SERVO_SENTENCE if servo else "- No motors or other actuators.")
        # the timetable ships with hack/cat-b-sched: switch `schedule` (or the motion pack, whose examples need it)
        say_yes = SAY_YES.replace("{SCHEDULE}", SCHEDULE_ON if ("schedule" in on or servo) else SCHEDULE_OFF)
        if self._can("soil_moisture") or self._can("level_switch"):
            say_yes += "\n" + PLANT_SAY_YES
        rules = []
        fn = getattr(self.bridge, "pack_prompt_rules", None)
        try:
            for pack, items in (fn() if callable(fn) else []):
                rules += [f"- {r}" for r in items]
        except Exception:  # noqa: BLE001
            rules = []
        packs = ("# Parts and objects added this week (digital checks only; not built physically yet)\n" + "\n".join(rules)
                 if rules else "")
        return {"honesty": honesty, "moving": moving, "say_yes": say_yes, "packs": packs}

    def _static(self) -> tuple:
        """(text before the examples, text after) from the files; cached until any of them changes."""
        sig = self._sig()
        with self._lock:
            if self._cache and self._cache[0] == sig:
                return self._cache[1]
            schema = self.bridge.spec_schema_text()
            table = self.bridge.library_table()
            cap = self.capability_blocks()
            freeform = FREEFORM in self._packs_on()      # custom csg shapes: own concept step, design rules, csg language
            design = DESIGN_FREEFORM if freeform else DESIGN
            if "printed" in self._packs_on():  # printed objects (pack `printed`) may be as small as 20 mm per axis
                design = design.replace("Every axis 60-260 mm;", "Every axis 60-260 mm (a printed object: 20-260 mm);")
            blocks = [ROLE, FLOW_FREEFORM if freeform else FLOW, VOICE, HONESTY.replace("{CANNOT}", cap["honesty"]),
                      SAFETY.replace("{MOVING}", cap["moving"]), cap["say_yes"], design]
            if freeform:
                blocks.append(csg_rules("printed" in self._packs_on()))
            if cap["packs"]:
                blocks.append(cap["packs"])
            blocks += [
                "# Spec schema (gadget-spec/1). Unknown keys are rejected; every number has bounds.\n" + schema,
                "# Parts catalog (the only parts that exist). Use these ids exactly.\n" + table,
                "# Hard fit rules (from the engine; the validator rejects specs that break them)\n"
                + self.bridge.fit_rules_text(),
            ]
            head = "\n\n".join(blocks)
            self._cache = (sig, head)
            return head

    def system_prompt(self, wish: str | None = None) -> str:
        """The agent's system instruction. `wish` (the user's own words) picks which worked examples are shown."""
        head = self._static()
        blocks = []
        for p in self.example_paths(wish):
            sp = self._read_example(p)
            if sp is None:
                continue
            tmpl = (sp.get("behaviour") or {}).get("template") or "printed, no electronics"
            kind = f"{(sp.get('body') or {}).get('family')} body, {tmpl}"
            blocks.append(f"## {sp.get('name') or p.parent.name} ({kind})\n" + json.dumps(sp, separators=(",", ":")))
        if not blocks:
            ex = self.example_spec()
            blocks = [json.dumps(ex, separators=(",", ":"))] if ex else []
        ex_text = "\n".join(blocks) or "(No example available yet: follow the schema exactly.)"
        return head + "\n\n" + (
            "# Worked example specs (they validate: copy key names and value formats exactly. They show "
            "different forms; do not reuse one body, feature layout or decor for more than one concept)\n" + ex_text)


def _has_face(spec: dict) -> bool:
    return any(isinstance(d, dict) and d.get("type") in ("eye", "mouth", "blush") for d in spec.get("decor") or [])


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(text).lower()) if len(w) >= 3 and w not in STOPWORDS}


def wish_of(project: dict | None) -> str:
    """The user's own words that started the project (the first user text messages), for example selection. A project
    that started from an image adds what the agent read from it (the image brief's title, purpose, form and feature
    lines; never text_seen), so an image sent without words still picks the closest worked examples. The wish only
    ranks our own example files: none of its words reach the prompt."""
    out = []
    for m in (project or {}).get("chat") or []:
        if m.get("role") == "user" and m.get("kind") == "text" and str(m.get("text") or "").strip():
            out.append(str(m["text"]).strip())
            if len(out) >= 2:
                break
    brief = (project or {}).get("image_brief")
    if isinstance(brief, dict):
        out += [str(brief.get(k) or "").strip() for k in ("title", "purpose", "form_factor")]
        out += [str(f.get("text") or "").strip() for f in brief.get("features") or [] if isinstance(f, dict)]
    return " ".join(x for x in out if x)[:2000]
