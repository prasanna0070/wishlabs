"""MIN-42: clarify layout and the no-exclamation lint."""
from __future__ import annotations

from app.agent.turn import clarify_text
from app.agent.voice import lint, opens_with_exclamation


def test_lint_rejects_exclamation_openers():
    assert opens_with_exclamation("Great! I'll plan a robot.")
    assert lint("Great! I'll plan a small USB-powered robot.") == "I'll plan a small USB-powered robot."
    assert lint("I would love to design this bedtime storyteller for her! I'll plan a small robot.") == \
        "I'll plan a small robot."
    assert lint("Done!") == "Done."
    assert lint("It glows softly! Then it fades.") == "It glows softly. Then it fades."
    assert lint("Choose **Build this** to proceed.") == "Choose **Build this** to proceed."
    assert not opens_with_exclamation(lint("Wow! Nice!"))


def test_each_consequence_sits_under_its_question():
    qs = [{"question": "How old is your daughter?", "consequence": "Her age will guide the story length."},
          {"question": "How should new favourite characters be added?",
           "consequence": "This determines whether the robot needs a microphone."}]
    t = clarify_text("I'll plan a small robot.", qs)
    assert t == ("I'll plan a small robot.\n\n**How old is your daughter?**\nHer age will guide the story length."
                 "\n\n**How should new favourite characters be added?**\nThis determines whether the robot needs a "
                 "microphone.")
    assert "length. This determines" not in t
