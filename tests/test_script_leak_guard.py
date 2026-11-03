"""Guards against the model leaking its own instructions into a published video.

Measured 2026-11-02: a jephter render shipped with these as its FIRST TWO spoken lines —

    target 85, max 95).
    **Final Polish:**

— and because the first script line becomes the video's title, `target 85, max 95).` was published as
the public title of `pPpfLrRgOzU`. The TTS read both lines aloud and the captions displayed them, so the
leak was in the video *and* on the channel page. `clean_lines()` only dropped lines starting with `#`
and one-word fragments, which is why a two-word fragment with a bracket sailed through.

These tests use lines that actually occurred, so the guard is measured rather than imagined.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import x_script as xs  # noqa: E402
import youtube_upload as yt  # noqa: E402

LEAKED = """target 85, max 95).
**Final Polish:**
I told myself that real safety meant relying on no one.
But some nights, your voice lingered long after you stopped speaking.
Note: keep the sentences short.
Here is the script:
Some mornings, I found myself listening for you before I was awake.
"""

SPEECH = [
    "I told myself that real safety meant relying on no one.",
    "But some nights, your voice lingered long after you stopped speaking.",
    "Some mornings, I found myself listening for you before I was awake.",
]


def test_the_two_lines_that_really_leaked_are_rejected():
    assert xs.is_speech("target 85, max 95).") is False, "this line was published as a title"
    assert xs.is_speech("**Final Polish:**") is False, "this line was spoken and captioned"


def test_ordinary_speech_is_still_accepted():
    for line in SPEECH:
        assert xs.is_speech(line) is True, line
    # A short fragment is a normal part of this channel's voice, so it must survive.
    assert xs.is_speech("It never did.") is True
    assert xs.is_speech("Not anymore.") is True


def test_clean_lines_keeps_only_the_three_sentences():
    out = xs.clean_lines(LEAKED, want=6)
    assert out == SPEECH, out


def test_markdown_and_headings_are_rejected():
    for bad in ("**Final Polish:**", "## Notes", "`spoken words`", "__emphasis__", "A heading:"):
        assert xs.is_speech(bad) is False, bad


def test_instruction_prefixes_are_rejected():
    for bad in ("Note: keep it short", "Here is the script", "Target 90 words",
                "Word count is fine", "Output only the spoken words", "Final polish below"):
        assert xs.is_speech(bad) is False, bad


def test_short_or_letterless_lines_are_rejected():
    for bad in ("", "   ", "85", "…", "a", "123"):
        assert xs.is_speech(bad) is False, bad


def test_a_bracketed_number_range_is_rejected_but_a_real_sentence_is_not():
    assert xs.is_speech("target 85, max 95).") is False
    assert xs.is_speech("max 95, min 85 is what I need.") is False
    # Numbers inside a normal confession are fine — this channel talks about time and nights.
    assert xs.is_speech("I waited until 3 am, then I stopped waiting.") is True


def test_the_word_index_the_prompt_uses_is_rejected():
    """Seen on 2026-11-03 in emmanuel's 17:38 render: it was one dry run from becoming the title."""
    polluted = "(15) moments (16) without (17) being (18) asked. (19)"
    assert xs.is_speech(polluted) is False
    assert xs.is_speech("Put (20) down (21) whatever (22) is (23) in (24) your hands.") is False
    # A plain number in ordinary prose is still speech.
    assert xs.is_speech("I counted 3 times before I stopped.") is True
    assert xs.is_speech("She left the note on the table and never came back.") is True


def test_clean_lines_drops_a_polluted_line_and_keeps_the_rest():
    text = "\n".join([
        "(15) moments (16) without (17) being (18) asked. (19)",
        "Stay gentle when you are exhausted.",
        "Choose them on the hard days because easy days require no real devotion.",
        "Real love is measured by what you give when you have nothing to gain.",
    ])
    lines = xs.clean_lines(text, 4)
    assert lines == [
        "Stay gentle when you are exhausted.",
        "Choose them on the hard days because easy days require no real devotion.",
        "Real love is measured by what you give when you have nothing to gain.",
    ], lines


def test_the_title_skips_a_leaked_line_instead_of_shipping_it():
    meta = yt.metadata_for("jephter", {"lines": LEAKED.splitlines()})
    title = meta["snippet"]["title"]
    assert title == SPEECH[0].rstrip("."), title
    assert "target" not in title.lower(), "the leaked instruction became a public title once"


def test_the_title_falls_back_when_nothing_usable_is_left():
    meta = yt.metadata_for("emmanuel", {"lines": ["target 85, max 95).", "**Final Polish:**"]},
                           channel_name="Brown Cinema")
    title = meta["snippet"]["title"]
    assert title and "target" not in title.lower(), title
    assert "Brown Cinema" in title, f"a safe fallback names the channel, got {title!r}"


def test_the_description_does_not_carry_a_leak_either():
    meta = yt.metadata_for("jephter", {"lines": SPEECH})
    desc = meta["snippet"]["description"]
    assert desc.startswith(SPEECH[0]), desc[:80]
    assert "target 85" not in desc
