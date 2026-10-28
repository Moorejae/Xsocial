#!/usr/bin/env python3
"""Write the script for one X workflow: a short, romance-focused monologue.

The register is the one the channels are built on — a couple, a moment, one honest sentence that
lands. Not a quote account, not motivational. It has to work **read aloud in under 50 seconds by a
slow, whispery narrator**, which is a stricter brief than "write something romantic":

| Constraint | Why |
| :--- | :--- |
| `max_words` 92 | at ~1.7 words/second a whispery read, ~85 words is 50 s. The cap is not decoration — the renderer drops phrases to obey it |
| `phrases` 6 | one cut and one caption block per phrase; fewer than 5 and there is no story, more than 8 and the cuts get frantic |
| second person, present tense | "you" makes it a memory the listener is inside of |
| no names, no brand, no emoji | it is read by a TTS engine, and a name in the text makes it someone else's story |
| ends on the image, not a moral | the last line is the one the renderer will never trim |

Two workflows, two voices, **one shared register** — the scripts are romance for both, the persona
differs in voice and phrasing habit, not in subject.

    python scripts\\x_script.py --workflow jephter
    python scripts\\x_script.py --workflow jephter --seed-topic "the last night before a move"
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_keys as gk  # noqa: E402
import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"

SYSTEM = """You write narration for vertical short videos about love, for one channel.

Rules, all of them hard:
- Second person. "You", "your". The listener is one half of a couple.
- Present tense or a very recent past. One moment, not a life story.
- Concrete and small: a kitchen light, a coat by the door, rain on a windscreen, a hand on a back.
  Never abstract ("true love is..."), never preachy, never a moral at the end.
- No names, no places a listener could not be, no brands, no emoji, no hashtags.
- No quotation marks and no stage directions. Only the words that will be spoken aloud.
- The last line is the image the video ends on. It must land without explaining itself.
- Plain words. No "utilise", no "nevertheless", no "in conclusion".

Output exactly the number of lines asked for, one sentence per line, nothing else.
No numbering, no blank lines, no commentary."""


def load_workflow(name: str) -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    if name not in data:
        raise SystemExit(f"unknown workflow {name!r}")
    return data[name]


def build_prompt(wf: dict, seed_topic: str | None, avoid: list[str]) -> str:
    v = wf.get("video", {})
    target, mx, n = v.get("target_words", 78), v.get("max_words", 92), v.get("phrases", 6)
    parts = [
        f"Write a {target}-word narration in exactly {n} lines for a 50-second vertical video.",
        f"Hard limit: {mx} words total. Aim for {target}.",
        "The moment: a small, specific scene between two people who love each other.",
    ]
    if seed_topic:
        parts.append(f"Use this as the situation: {seed_topic}")
    if avoid:
        parts.append("Do not reuse these recent openings: " + "; ".join(avoid[:5]))
    parts.append(f"Return {n} lines, one sentence each. Every line must be a complete sentence that "
                 f"ends with a full stop, and the last line must be able to stand alone as the end "
                 f"of the video.")
    return "\n".join(parts)


def clean_lines(text: str, want: int) -> list[str]:
    """Turn a model response into spoken sentences. Anything that is not speech is discarded."""
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", s)          # bullets and numbering
        s = s.strip().strip('"').strip("'").strip()
        if not s or s.startswith("#"):
            continue
        if not re.search(r"[A-Za-z]", s):
            continue
        if len(s.split()) < 3:                                  # "D", a stray header, a fragment
            continue
        lines.append(s)
    # If the model answered in one paragraph, split on sentence boundaries instead.
    if len(lines) < max(3, want - 2):
        joined = " ".join(lines)
        lines = [p.strip() for p in re.split(r"(?<=[.!?])\s+", joined) if p.strip()]
    return lines


def split_long(lines: list[str], max_words: int) -> list[str]:
    """A 30-word sentence cannot be one caption block or one cut. Break it at a comma."""
    out = []
    for s in lines:
        if len(s.split()) <= max_words:
            out.append(s)
            continue
        chunks, cur = [], []
        for piece in re.split(r"(?<=,)\s+", s):
            if cur and len(" ".join(cur + [piece]).split()) > max_words:
                chunks.append(" ".join(cur))
                cur = [piece]
            else:
                cur.append(piece)
        if cur:
            chunks.append(" ".join(cur))
        out.extend(c.strip().rstrip(",") + ("." if not c.strip().endswith((".", "!", "?")) else "")
                   for c in chunks)
    return out


DANGLING = {"the", "a", "an", "and", "but", "to", "of", "in", "on", "with", "your", "my", "that",
            "it", "his", "her", "their", "as", "at", "for", "from", "into", "against", "while"}


def complete_ending(lines: list[str]) -> bool:
    """A narration must not stop mid-sentence.

    Measured failure, 2026-10-28: one run ended on *"The radio plays"* (3 words, no full stop) and
    another on *"You just stand together in."* — the second was produced by the first version of this
    guard, which appended a period to a truncated line instead of rejecting it. Both render, both
    pass a duration check, and both publish a sentence that stops in the middle. So the ending is
    checked, and a broken one is **dropped**, never patched into looking whole.
    """
    if not lines:
        return False
    last = lines[-1].strip()
    if not last.endswith((".", "!", "?")):
        return False
    body = last.rstrip(".!?")
    if len(body.split()) < 4:
        return False
    return body.split()[-1].lower() not in DANGLING


def repair_ending(lines: list[str]) -> tuple[list[str], list[str]]:
    """Make the ending whole. Returns (lines, dropped_tail).

    A long line that merely lacks punctuation is a real sentence and gets its full stop. A short or
    dangling line is a truncation, and a truncation is removed — the previous sentence becomes the
    closing image, which is a far smaller loss than publishing half a sentence.
    """
    dropped: list[str] = []
    while lines and not complete_ending(lines):
        last = lines[-1].strip()
        words = last.rstrip(".!?").split()
        recoverable = (len(words) >= 5 and words and words[-1].lower() not in DANGLING)

        if recoverable:
            # A genuine sentence that lost its punctuation: close it. Prefer that to deleting a beat.
            lines[-1] = last.rstrip(" ,;:—-") + ("!" if last.rstrip().endswith("!") else ".")
            break

        dropped.insert(0, lines.pop())
        if len(lines) <= 2:
            break
    return lines, dropped


def write_script(workflow: str, seed_topic: str | None = None, temperature: float = 0.95) -> dict:
    wf = load_workflow(workflow)
    v = wf.get("video", {})
    want = v.get("phrases", 6)
    mx_words = v.get("max_words", 92)

    pool = gk.pool_for(workflow)
    avoid = recent_openings(workflow)
    prompt = build_prompt(wf, seed_topic, avoid)

    lines: list[str] = []
    for attempt in range(3):
        text = pool.generate(prompt, system=SYSTEM,
                             temperature=temperature if attempt == 0 else max(0.6, temperature - 0.15 * attempt),
                             max_output_tokens=2048)
        lines = clean_lines(text, want)
        lines = split_long(lines, max_words=18)

        # Trim to the phrase budget and the word cap, dropping from the middle so the opening hook
        # and the closing image both survive — the same rule the renderer applies to the audio.
        while len(lines) > want + 2 and len(lines) > 4:
            lines.pop(len(lines) // 2)
        while sum(len(s.split()) for s in lines) > mx_words and len(lines) > 3:
            lines.pop(len(lines) // 2)

        if complete_ending(lines):
            break
        print(f"  [{workflow}] attempt {attempt + 1}: ending is incomplete "
              f"({lines[-1] if lines else 'no lines'!r}) -> rewriting", file=sys.stderr)

    if not complete_ending(lines):
        lines, dropped = repair_ending(lines)
        if dropped:
            print(f"  [{workflow}] dropped {len(dropped)} truncated trailing line(s): "
                  f"{dropped!r}", file=sys.stderr)

    if not complete_ending(lines):
        raise gk.GeminiError(f"{workflow}: could not produce a complete ending after 3 attempts — "
                             f"refusing to publish half a sentence")

    # A single-word or punctuation-only fragment is noise from the model, and there is no line in
    # the narration where it belongs.
    lines = [ln for ln in lines if len(ln.split()) >= 3] or lines

    words = sum(len(s.split()) for s in lines)
    if words < 25:
        raise gk.GeminiError(f"{workflow}: script too short ({words} words) — refusing to build a video")

    return {
        "workflow": workflow,
        "opening": lines[0] if lines else "",
        "lines": lines,
        "words": words,
        "words_per_line": [len(s.split()) for s in lines],
        "seed_topic": seed_topic,
        "voice": wf.get("voice", {}),
    }


USAGE = ROOT / "out" / "script_usage.json"


def recent_openings(workflow: str) -> list[str]:
    try:
        data = json.loads(USAGE.read_text(encoding="utf-8"))
        return list(reversed(data.get(workflow, [])))[:8]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def remember_opening(workflow: str, opening: str) -> None:
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(USAGE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault(workflow, []).append(opening)
    data[workflow] = data[workflow][-60:]
    USAGE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Write a romance script for one X workflow")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--seed-topic", default=None)
    ap.add_argument("--out", default=None, help="write the script JSON here instead of printing")
    a = ap.parse_args()

    script = write_script(a.workflow, a.seed_topic)
    remember_opening(a.workflow, script["opening"])

    print(f"{a.workflow}: {script['words']} words in {len(script['lines'])} lines")
    for i, line in enumerate(script["lines"], 1):
        print(f"  {i}. {line}")

    if a.out:
        path = ROOT / a.out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
