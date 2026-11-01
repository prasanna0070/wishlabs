from __future__ import annotations


def make_brain(svc):
    if svc.settings.agent == "scripted":
        from .scripted import ScriptedBrain

        return ScriptedBrain(svc)
    from .gemini import GeminiBrain

    return GeminiBrain(svc)
