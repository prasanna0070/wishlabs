"""AGENT=scripted: replays the benchmark conversation with no model call, through the same tool executor, so the
frontend and the tests exercise every event type. Specs come from examples/bedtime-robot/spec.json (or a
built-in fallback that picks catalog ids by role)."""
from __future__ import annotations

import asyncio
import copy
import re

from .prompt import PromptBuilder

COLOURS = {"pink": ("#F2B5C4", "blush pink"), "blue": ("#9CC3E6", "sky blue"), "teal": ("#8CC9C0", "pale teal"),
           "yellow": ("#F4D58D", "butter yellow"), "purple": ("#C3B1E1", "lavender"), "green": ("#A8D5A2", "mint"),
           "white": ("#F4F1EA", "cream white"), "orange": ("#F6B98B", "apricot")}

# kind -> (library roles, preferred id fragments in order)
ROLE_HINTS = {
    "core": (("core", "controller"), ("ESP32S3",)),
    "button": (("button", "input"), ("ARCADE", "BUTTON")),
    "led": (("led", "light"), ("WS2812B_1PX", "WS2812")),
    "speaker": (("speaker", "audio_out"), ("SPK_40", "SPK")),
    "mic": (("mic", "audio_in"), ("INMP441",)),
    "display": (("display",), ("OLED_096", "OLED")),
}


def _pick(mods: list[dict], kind: str) -> str:
    roles, prefs = ROLE_HINTS[kind]
    cands = [m for m in mods if str(m.get("role", "")).lower() in roles]
    for frag in prefs:
        for m in cands:
            if frag.lower() in m["id"].lower():
                return m["id"]
    return cands[0]["id"] if cands else f"<library id: {kind}>"


def fallback_spec(mods: list[dict]) -> dict:
    return {
        "schema": "gadget-spec/1", "name": "Lullaby Pod", "slug": "lullaby-pod",
        "tagline": "A crescent moon that tells bedtime stories",
        "audience": {"child_directed": True, "age_years": 5}, "region": "IN",
        "colors": {"body": "#8CC9C0", "accent": "#F4F1EA", "detail": "#2B3A42", "body_name": "pale teal"},
        "body": {"family": "silhouette",
                 "outline": [[-60, 0], [60, 0], [78, 40], [70, 120], [40, 170], [0, 182], [-40, 170], [-70, 120],
                             [-78, 40]],
                 "depth": 120, "edge_round": 12, "profile": None, "squash_y": 1.0, "primitives": None, "wall": 2.4,
                 "split": "rear_cover", "feet": 4},
        "decor": [{"type": "face_plate", "shape": "circle", "at": [0, 120], "size": [80, 80], "depth": 1.2},
                  {"type": "eye", "style": "arc_closed", "at": [-16, 128], "size": [9, 9], "relief": "engrave",
                   "depth": 0.8},
                  {"type": "eye", "style": "arc_closed", "at": [16, 128], "size": [9, 9], "relief": "engrave",
                   "depth": 0.8},
                  {"type": "mouth", "style": "smile", "at": [0, 112], "size": [20, 7], "relief": "engrave",
                   "depth": 0.8}],
        "features": [
            {"id": "story_button", "module": _pick(mods, "button"), "face": "front", "at": [0, 75],
             "label": "Story button"},
            {"id": "night_light", "module": _pick(mods, "led"), "face": "front", "at": [0, 158],
             "label": "Night light and listening light"},
            {"id": "speaker", "module": _pick(mods, "speaker"), "face": "front", "at": [0, 32],
             "grille": {"pattern": "hex_holes", "w": 60, "h": 30, "hole": 2.6, "pitch": 4.4}},
            {"id": "mic", "module": _pick(mods, "mic"), "face": "front", "at": [-40, 75], "label": "Microphone hole"}],
        "electronics": {"core": _pick(mods, "core"), "extra_modules": [],
                        "power": {"input": "usb_c_5v", "adapter_in_box": False}},
        "behaviour": {"template": "story_player",
                      "summary": "Tap: a bedtime story starring her favourite character. Hold: say a new favourite.",
                      "settings": {}},
        "safety": {"flags": ["CHILD_DIRECTED", "LOCAL_VOICE_CAPTURE"],
                   "notes": "Child-directed: needs a safety review before sale as a built device."},
    }


TIERED = (
    ("expected", "Moon Bot", "A sleepy round robot with a crescent-moon face and the story button on its head.",
     ("#8CC9C0", "pale teal"), 1.0),
    ("expected", "Owl Bot", "A plump owl-styled robot with antenna ears and a glowing chest light.",
     ("#F4D58D", "butter yellow"), 0.94),
    ("different", "Cloud Robot", "A soft cloud-shaped robot with a sleepy smile below its face.",
     ("#C3B1E1", "lavender"), 1.08),
    ("different", "Bean Bot", "A rounded bean-shaped robot that leans back like it is listening.",
     ("#A8D5A2", "mint"), 0.9),
    ("wildcard", "Lantern Bot", "A little lantern robot with a face that glows while it reads.",
     ("#F6B98B", "apricot"), 0.86),
    ("wildcard", "Snail Shell", "A friendly snail whose shell holds the speaker.", ("#F2B5C4", "blush pink"), 1.12),
)


def variants(base: dict) -> list[tuple[str, str, str, dict]]:
    out = []
    for tier, title, blurb, colour, scale_x in TIERED:
        s = copy.deepcopy(base)
        s["name"] = title
        s["slug"] = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        s.setdefault("colors", {})["body"], s["colors"]["body_name"] = colour
        if s.get("body", {}).get("outline") and scale_x != 1.0:
            s["body"]["outline"] = [[round(x * scale_x, 1), z] for x, z in s["body"]["outline"]]
        out.append((tier, title, blurb, s))
    return out


class ScriptedBrain:
    kind = "scripted"

    def __init__(self, svc):
        self.svc = svc
        self.prompts = PromptBuilder(svc.bridge, svc.settings)

    def base_spec(self) -> dict:
        ex = self.prompts.example_spec()
        return ex if ex else fallback_spec(self.svc.bridge.raw_modules())

    async def run(self, turn) -> None:
        p = await turn.project()
        stage = p["stage"]
        last_user = next((m for m in reversed(p["chat"]) if m["role"] == "user"), None)
        text = (last_user or {}).get("text", "").lower()
        await asyncio.sleep(0)
        if turn.forced == "write_brief" or (stage == "brief" and "build" not in text and turn.forced is None):
            await turn.call_tool("write_brief", self._brief_args(p))
            return
        if turn.forced == "propose_concepts":
            await self._concepts(turn)
            return
        if stage == "clarify":
            if int(p.get("questions_asked") or 0) == 0 and len(p["chat"]) <= 1:
                await turn.call_tool("ask_user", {
                    "message": "I'll plan a small USB-powered bedside robot and use India for parts sourcing; both "
                               "choices can be changed.",
                    "defaults": ["Country: India (default)", "Power: USB-C adapter"],
                    "project_name": "Story Time Bot",
                    "questions": [
                    {"id": "age", "question": "How old is she?",
                     "consequence": "Her age will guide the story style and controls.",
                     "options": ["3 yo", "5 yo old", "8 yo"], "allow_free_text": True},
                    {"id": "voice", "question": "Should she say new favourites out loud?",
                     "consequence": "How favorites are entered will determine whether the robot needs a microphone.",
                     "options": ["yes", "no, I'll type them"], "allow_free_text": False}]})
                return
            await self._concepts(turn)
            return
        if stage == "concepts":
            await self._concepts(turn)
            return
        if stage == "brief":
            await turn.call_tool("build_design", {"spec": p["brief"]["spec"], "message": "Building it now."})
            return
        if stage == "building":
            await turn.call_tool("say", {"text": "It is still building. I will post the result here when it is done."})
            return
        # ready | error: edit loop (part-scoped edits arrive with turn.scope set)
        spec = await asyncio.to_thread(self._current_spec, p)
        if turn.scope and spec is not None:
            changed, summary = self._edit_part(spec, turn.scope.get("spec_path") or "", text)
            if changed:
                await turn.call_tool("update_design", {"spec": spec, "change_summary": summary,
                                                       "message": f"Done: {summary.lower()}."})
            else:
                await turn.call_tool("say", {"text": "In scripted mode a part edit understands colours, 'bigger', "
                                                     "'smaller', 'up', 'down', 'left' and 'right'."})
            return
        if spec is None:
            await turn.call_tool("say", {"text": "There is no built version to change yet."})
            return
        changed, summary = self._edit(spec, text)
        if not changed:
            await turn.call_tool("say", {"text": "In scripted mode I can change the colour (pink, blue, teal, yellow, "
                                                 "purple, green) or the size (bigger, smaller)."})
            return
        await turn.call_tool("update_design", {"spec": spec, "change_summary": summary,
                                               "message": f"Done: {summary.lower()}. Building the new version."})

    def _current_spec(self, p: dict) -> dict | None:
        n = p.get("current_version")
        if n is None:
            return None
        inp = self.svc.store.read_json(f"projects/{p['id']}/inputs/v{n}.json") or {}
        return copy.deepcopy(inp.get("spec"))

    @staticmethod
    def _edit(spec: dict, text: str) -> tuple[bool, str]:
        from ..tweak import scale_spec

        bits = []
        for word, (hexv, name) in COLOURS.items():
            if word in text:
                spec.setdefault("colors", {})["body"], spec["colors"]["body_name"] = hexv, name
                bits.append(f"{name} body")
                break
        if "bigger" in text or "larger" in text:
            spec.update(scale_spec(spec, 1.1))
            bits.append("10% bigger")
        elif "smaller" in text:
            spec.update(scale_spec(spec, 0.9))
            bits.append("10% smaller")
        return bool(bits), ", ".join(bits).capitalize()

    @staticmethod
    def _edit_part(spec: dict, path: str, text: str) -> tuple[bool, str]:
        m = re.fullmatch(r"(features|decor)\[(\d+)\]", path)
        if path == "body" or not m:
            return ScriptedBrain._edit(spec, text)
        item = spec[m.group(1)][int(m.group(2))]
        name = item.get("label") or item.get("id") or item.get("type") or path
        dx = {"left": -5, "right": 5}
        dz = {"up": 5, "down": -5}
        for w, v in dx.items():
            if w in text and item.get("at"):
                item["at"] = [item["at"][0] + v, item["at"][1]]
                return True, f"Moved {name} {w}"
        for w, v in dz.items():
            if w in text and item.get("at"):
                item["at"] = [item["at"][0], item["at"][1] + v]
                return True, f"Moved {name} {w}"
        if item.get("size") and ("bigger" in text or "smaller" in text):
            k = 1.15 if "bigger" in text else 0.87
            item["size"] = [round(v * k, 1) for v in item["size"]]
            return True, f"{'Bigger' if k > 1 else 'Smaller'} {name}"
        return ScriptedBrain._edit(spec, text)

    async def _concepts(self, turn) -> None:
        """Replays one fixed set. The scripted brain cannot redesign, so concept-rule repair rounds resend the same
        set until the app stops asking and shows it with its rule failures recorded (set_check)."""
        base = self.base_spec()
        cs = [{"tier": tr, "title": t, "blurb": b, "spec": s} for tr, t, b, s in variants(base)]
        args = {"message": "Choose the robot shape you prefer from the concepts. The proposed design uses short offline "
                           "stories with saved character names; its face and limbs stay still.",
                "concepts": cs}
        for _ in range(self.svc.settings.max_repair_rounds + 1):
            res = await turn.call_tool("propose_concepts", copy.deepcopy(args))
            if res.terminal or not res.response.get("set_failures"):
                return

    @staticmethod
    def _brief_args(p: dict) -> dict:
        from ..projects import find_concept

        c = find_concept(p, p.get("selected_concept")) or {"title": "Your gadget"}
        t = c["title"]
        U = lambda v, e: {"value": v, "source": "user", "evidence": e}  # noqa: E731
        S = lambda v, e: {"value": v, "source": "suggested", "evidence": e}  # noqa: E731
        return {"message": f"{t}'s brief is ready for review. It includes offline stories personalised with saved "
                           "character names and a parent page to correct or erase favourites. Choose **Build this** "
                           "to proceed.",
                "brief": {
                    "title": t,
                    "summary": S(f"{t}: a bedside robot that tells short offline bedtime stories with her saved "
                                 "favourite characters.", "From your request"),
                    "purpose": U("A bedside story companion for a five-year-old that tells short bedtime stories "
                                 "starring her favourite characters and remembers their names.",
                                 "You asked for bedtime stories and remembered favourites; you chose 5 yo old"),
                    "functions": [
                        {"title": "Bedtime stories", "value": "Tap the big button for a short offline story with "
                                                               "one of her saved characters.",
                         "source": "user", "evidence": "You asked for bedtime stories"},
                        {"title": "Remembers favourites", "value": "Favourite characters are kept on the device and "
                                                                    "used in later stories.",
                         "source": "user", "evidence": "You asked it to remember her favourite characters"},
                        {"title": "Night light", "value": "A soft glow during the story that fades out on a timer.",
                         "source": "suggested", "evidence": "Default for a bedside story device"}],
                    "interaction": S("One big button and one light. Tap to start or stop a story. A parent page on "
                                     "the home Wi-Fi edits favourites and volume.", "Default for a five-year-old"),
                    "privacy_safety": S("Name clips and favourites stay on the device and are never uploaded. This is "
                                        "a child-directed product, so it needs a safety review before it can be sold "
                                        "as a built device.", "Blueprint safety rules for child-directed products"),
                    "power": S("USB-C 5 V from a certified phone adapter (not in the box). No batteries.",
                               "Default: USB-C power only"),
                    "details": [{"topic": "Sourcing country", "value": "India", "source": "suggested",
                                 "evidence": "Default; can be changed"}],
                    "limits": [S("Stories are pre-written, not made up live.", "Offline story pack"),
                               S("The face and limbs do not move.", "No actuators in this design"),
                               S("Volume and warmth still need a physical check.", "Not tested physically")]}}
