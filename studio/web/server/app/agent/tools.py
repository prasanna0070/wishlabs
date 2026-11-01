"""Agent tool declarations (ARCHITECTURE §10) as JSON Schema, and which tools each stage allows."""
from __future__ import annotations

SPEC_PARAM = {
    "type": "object",
    "description": "A complete gadget-spec/1 document that follows the spec schema in the system prompt exactly "
                   "(all required keys, library ids only, millimetres).",
}

TIERS = ("expected", "different", "wildcard")

FACT = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "description": "The fact, in plain words."},
        "source": {"type": "string", "enum": ["user", "suggested"],
                   "description": "user = the user said or chose it; suggested = your default or proposal."},
        "evidence": {"type": "string",
                     "description": "Short basis. For source user, quote the user's stored words exactly, e.g. "
                                    "'You said: \"<their exact answer>\"'. For suggested, give the reason, e.g. "
                                    "'Default: <reason>'."},
    },
    "required": ["value", "source", "evidence"],
}

PROJECT_NAME = {"type": "string",
                "description": "Only on your first reply: a 2-4 word project name, e.g. '<Adjective> <Subject> <Role>'; never a name from another product."}
DEFAULTS = {"type": "array", "maxItems": 5, "items": {"type": "string"},
            "description": "Defaults you chose instead of asking, each short and changeable, e.g. "
                           "'Country: India (default)', 'Power: USB-C adapter'."}

TOOLS: dict[str, dict] = {
    "ask_user": {
        "description": "Ask the user clarifying questions, usually in ONE round, only about what blocks the design. "
                       "Each question can offer chips; a free-text box is always shown. Disclose your defaults "
                       "instead of asking about them. Ends your turn; the answers come back as the next message.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "One or two short plain sentences, no exclamation marks: what you will "
                                           "make and the defaults you chose, e.g. 'I'll plan a small USB-powered "
                                           "<subject> and use India for parts sourcing; both choices can be "
                                           "changed.'"},
                "defaults": DEFAULTS,
                "project_name": PROJECT_NAME,
                "questions": {
                    "type": "array", "minItems": 1, "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "short snake_case id, e.g. 'age'"},
                            "question": {"type": "string", "description": "The question, short and plain."},
                            "consequence": {"type": "string",
                                            "description": "One sentence, shown directly under this question, "
                                                           "saying what this answer changes in the design; name the "
                                                           "question's topic in it (not 'This determines...')."},
                            "options": {"type": "array", "maxItems": 4, "items": {"type": "string"},
                                        "description": "0-4 short chip answers in everyday words (none for "
                                                       "open questions such as a city name)."},
                            "allow_free_text": {"type": "boolean"},
                        },
                        "required": ["id", "question", "consequence", "options"],
                    },
                },
            },
            "required": ["questions"],
        },
    },
    "propose_concepts": {
        "description": "Propose 6 concepts, 2 per tier: 'expected' (the obvious cute take on the subject), "
                       "'different' (a fresh but sensible idea), 'wildcard' (surprising, still buildable). Each is a "
                       "complete valid spec with a different body family or silhouette, its own feature layout and a "
                       "character face. The app validates each spec, checks the set against the concept rules "
                       "(subject kept, face, no shared layout), renders it from CAD and makes a product photo of "
                       "exactly that render. Concepts that fail come back with errors: resend ONLY those, "
                       "corrected, with the same titles (or `replaces`). Ends your turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "Two plain sentences in your own words, no exclamation marks: the first "
                                           "asks the user to choose a shape of the subject they named ('Choose the "
                                           "<subject> shape you prefer from the concepts.'); the second states what "
                                           "all concepts share (the behaviour, and what does not move)."},
                "defaults": DEFAULTS,
                "project_name": PROJECT_NAME,
                "concepts": {
                    "type": "array", "minItems": 1, "maxItems": 6,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string",
                                      "description": "A cute name of two or three words that names the subject "
                                                     "when the concept is one (e.g. '<Adjective> <Subject>')."},
                            "tier": {"type": "string", "enum": list(TIERS)},
                            "blurb": {"type": "string", "description": "One sentence on the shape and its character, "
                                                                        "naming the subject."},
                            "replaces": {"type": "string",
                                         "description": "Only in a repair round: the earlier title this concept "
                                                        "replaces, when you rename it."},
                            "spec": SPEC_PARAM,
                        },
                        "required": ["title", "tier", "blurb", "spec"],
                    },
                },
            },
            "required": ["message", "concepts"],
        },
    },
    "write_brief": {
        "description": "Write (or rewrite) the product brief for the selected concept, for the user to review "
                       "before building. Every fact is tagged with its source (user or suggested) and a short "
                       "evidence note. The app adds Size and Parts from the spec itself. Ends your turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "One or two sentences: the brief is ready, what it includes, and any "
                                           "setup the user should know. End with: Choose **Build this** to proceed."},
                "brief": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": FACT,
                        "purpose": FACT,
                        "functions": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
                            "type": "object",
                            "properties": {"title": {"type": "string"}, "value": {"type": "string"},
                                           "source": {"type": "string", "enum": ["user", "suggested"]},
                                           "evidence": {"type": "string"}},
                            "required": ["title", "value", "source", "evidence"]}},
                        "interaction": FACT,
                        "privacy_safety": FACT,
                        "power": FACT,
                        "details": {"type": "array", "maxItems": 8, "items": {
                            "type": "object",
                            "properties": {"topic": {"type": "string"}, "value": {"type": "string"},
                                           "source": {"type": "string", "enum": ["user", "suggested"]},
                                           "evidence": {"type": "string"}},
                            "required": ["topic", "value", "source", "evidence"]},
                            "description": "Other decisions worth reviewing, e.g. Story language, Sourcing country."},
                        "limits": {"type": "array", "items": FACT,
                                   "description": "What it will not do, and what still needs a physical check."},
                    },
                    "required": ["title", "summary", "purpose", "functions", "interaction", "privacy_safety",
                                 "power", "limits"],
                },
                "spec": {**SPEC_PARAM, "description": "Optional. The final spec to build if it differs from the "
                                                       "selected concept (e.g. behaviour settings from the answers). "
                                                       "Omit to build the selected concept's spec unchanged."},
            },
            "required": ["message", "brief"],
        },
    },
    "build_design": {
        "description": "Build the approved brief's design (CAD, PCB, firmware, manual, price). Only when the user "
                       "has clearly asked to build. Ends your turn; the app reports progress and the result.",
        "parameters": {
            "type": "object",
            "properties": {"spec": SPEC_PARAM,
                           "message": {"type": "string", "description": "One short line, e.g. 'Building <name> now.'"}},
            "required": ["spec"],
        },
    },
    "update_design": {
        "description": "Change the built design and make a new version: send the FULL modified spec (start from "
                       "the current spec in the state block) and a short change summary. When the state block names "
                       "a selected part, change only that part. Ends your turn.",
        "parameters": {
            "type": "object",
            "properties": {"spec": SPEC_PARAM,
                           "change_summary": {"type": "string",
                                              "description": "Under 10 words, e.g. 'Bigger ears, pink body'."},
                           "message": {"type": "string",
                                       "description": "One or two sentences for the user about the change."}},
            "required": ["spec", "change_summary"],
        },
    },
    "say": {
        "description": "Post a short chat message without ending your turn (use plain text replies when you have "
                       "nothing else to do).",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
}

TERMINAL = {"ask_user", "propose_concepts", "write_brief", "build_design", "update_design"}

LABELS = {
    "ask_user": "Preparing questions",
    "propose_concepts": "Designing concepts",
    "write_brief": "Writing the brief",
    "build_design": "Starting the build",
    "update_design": "Updating the design",
    "say": "Replying",
}


def allowed_tools(stage: str, questions_left: int, has_selected: bool, has_version: bool,
                  forced: str | None = None) -> list[str]:
    if forced:
        return [forced, "say"]
    if stage == "clarify":
        names = ["propose_concepts", "say"] + (["ask_user"] if questions_left > 0 else [])
    elif stage == "concepts":
        names = ["propose_concepts", "say"] + (["ask_user"] if questions_left > 0 else [])
        if has_selected:
            names.append("write_brief")
    elif stage == "brief":
        names = ["write_brief", "build_design", "propose_concepts", "say"]
    elif stage == "building":
        names = ["say"]
    else:  # ready | error
        names = ["update_design", "say", "ask_user", "propose_concepts"] if has_version else \
            ["build_design", "write_brief", "propose_concepts", "say"]
    return names


def declarations(names: list[str]):
    from google.genai import types

    return [types.FunctionDeclaration(name=n, description=TOOLS[n]["description"],
                                      parameters_json_schema=TOOLS[n]["parameters"]) for n in names]
