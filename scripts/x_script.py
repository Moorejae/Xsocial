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

Rules that always apply:
- Only words that will be spoken aloud. No quotation marks, no stage directions, no emoji, no hashtags,
  no names, no brands.
- Do not describe a physical scene. No hands on necks, no kitchens, no rain on a windscreen, no bodies.
  This is spoken to someone, not narrated over action. Talk, do not stage.
- Short sentences. One to three lines may be a fragment ("It never did.", "Not anymore.").
- No rhyme, no slogans, no "in conclusion". Plain words.
- The last line is the line the video ends on. It must land without explaining itself.

Output exactly the number of lines asked for, one sentence per line, nothing else.
No numbering, no blank lines, no commentary."""


# Two channels, two verbs. jephter confesses; emmanuel instructs. Both in the owner's own register,
# taken from the scripts he supplied on 2026-11-01 as the reference for what "nice" means here.
CHANNEL_BRIEF = {
    "jephter": """This channel: first-person confession about how it feels to be loved, and to love back.

- Write as "I", speaking to an absent "you". The listener is the person being missed or loved.
- Present tense or very recent past. Interior and mindful: what I noticed, admitted, stopped fighting.
- Structure, in this order: a claim I made to myself; two or three observations that contradict it
  ("But some nights, ...", "Some mornings, ..."); then a turn ("Until one day ...", "And I realised ...");
  then a closing want, stated plainly, and short.
- The turn is the heart of it. Without the turn the script is just sadness.
- Do not give advice. Do not explain the feeling. Do not mention a location, an object or a body part
  unless it is the only way to say it.
- The last line is quiet and final, and usually says the opposite of the first line.""",

    "emmanuel": """This channel: how to love someone on purpose — intentionally, unconditionally, unapologetically.

- Write in the imperative, addressed to the listener: "Love them ...", "Put your phone down ...",
  "Stay soft ...". Not "I", not a story.
- Every line is something a person can actually do or decide, in that moment, for the person they love.
  Concrete behaviour, not a feeling described.
- Mostly short commands. Allow at most two longer sentences that explain with "because".
- No therapy vocabulary, no listicle tone, no "communication is key". Conviction, not advice-column.
- The last line names the standard — the measure of real love — instead of summarising the list.""",
}


def system_for(wf: dict, workflow: str = "") -> str:
    """Add this channel's brief to the shared rules.

    The two channels share a register and differ in *verb*: jephter confesses, emmanuel instructs. The
    brief lives in config (`script.focus` / `script.avoid`) when it is being tuned, and in the longer
    channel brief above when it describes the shape of the piece, because shape is hard to express in a
    config string.
    """
    parts = [SYSTEM]
    brief = CHANNEL_BRIEF.get(workflow)
    if brief:
        parts += ["", brief]
    script = wf.get("script") or {}
    extra = []
    if script.get("theme"):
        extra.append(f"- Subject: {script['theme']}.")
    if script.get("avoid"):
        extra.append(f"- Never: {script['avoid']}.")
    if extra:
        parts += ["", "Additionally for this channel:"] + extra
    return "\n".join(parts)


def load_workflow(name: str) -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    if name not in data:
        raise SystemExit(f"unknown workflow {name!r}")
    return data[name]


def build_prompt(wf: dict, seed_topic: str | None, avoid: list[str], workflow: str = "") -> str:
    v = wf.get("video", {})
    target, mx, n = v.get("target_words", 85), v.get("max_words", 95), v.get("phrases", 8)
    parts = [
        f"Write a {target}-word narration in {n} lines for a 50-second vertical video.",
        f"Hard limit: {mx} words total. Aim for {target}.",
        "No scene, no setting, no bodies — only the thing being said.",
    ]
    if workflow == "jephter":
        parts.append("Open with a claim the speaker made to themselves. Turn it near the end.")
    elif workflow == "emmanuel":
        parts.append("Open with a command. End by naming the standard, not by summarising.")
    if seed_topic:
        parts.append(f"Use this as the angle: {seed_topic}")
    if avoid:
        parts.append("Do not reuse these recent openings: " + "; ".join(avoid[:5]))
    parts.append(f"Return {n} lines, each one complete thought, about {target} words in total. "
                 f"Fragments of two or three words are allowed. Every line ends with a full stop "
                 f"and the last line must stand alone as the end of the video.")
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
        if len(s.split()) < 2:                                  # a stray header, a one-word fragment
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


# Words that essentially never end a sentence, and words that only look like they do.
STRICT_DANGLING = {
    "the", "a", "an", "and", "but", "or", "nor",
    "your", "my", "his", "her", "their", "our", "its", "no", "not", "very", "too",
    "such", "every", "each", "another", "both", "all", "much", "many", "most", "some", "any",
}
# Prepositions and infinitives legitimately end an elliptical sentence — "…before I remembered not
# to.", "…conversations they are not part of.", "Guard their secrets like your own." — but only in a
# line long enough to be deliberate.
LOOSE_DANGLING = {
    "to", "of", "in", "on", "at", "for", "as", "that", "which", "who", "while", "if", "from",
    "into", "by", "over", "under", "through", "about", "then", "when", "where", "with", "without",
    "because", "than",
}


def truncated(line: str) -> bool:
    """A line that stops on a word which cannot end a sentence is a cut-off, not a style.

    Three real failures shaped this: *"…requires no."* (a dangling determiner), *"You just stand
    together in."* (a 5-word line ending on a preposition), and — in the other direction —
    *"…before I remembered not to."*, a legitimate ellipsis that an over-eager list threw away and
    dropped the script to 49 words. So the strict set is checked always, and the loose set only on a
    short line, where a dangling word cannot be deliberate.
    """
    body = line.strip().rstrip(".!?,;:")
    words = body.split()
    if not words:
        return True
    last = words[-1].lower()
    if last in STRICT_DANGLING:
        return True
    return last in LOOSE_DANGLING and len(words) <= 8


def complete_ending(lines: list[str]) -> bool:
    """A narration must not stop mid-sentence.

    Measured failures, all real: *"The radio plays"* (no full stop), *"You just stand together in."*
    (produced by the first version of this guard, which appended a period to a truncation), and
    *"…requires no."* (a dangling determiner). So the ending is checked, and a broken one is
    **dropped**, never patched into looking whole.
    """
    if not lines:
        return False
    last = lines[-1].strip()
    if not last.endswith((".", "!", "?")):
        return False
    if len(last.rstrip(".!?").split()) < 2:
        return False
    return not truncated(last)


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
    prompt = build_prompt(wf, seed_topic, avoid, workflow)

    lines: list[str] = []
    for attempt in range(3):
        text = pool.generate(prompt, system=system_for(wf, workflow),
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

        if complete_ending(lines) and not any(truncated(ln) for ln in lines):
            break
        bad = [ln for ln in lines if truncated(ln)]
        print(f"  [{workflow}] attempt {attempt + 1}: {'truncated line(s) ' + repr(bad[:2]) if bad else 'incomplete ending'} "
              f"-> rewriting", file=sys.stderr)

    # A truncated line anywhere in the narration is cut from the script, not silently published:
    # "…because real devotion requires no." reads as a mistake even if it is never spoken last.
    kept = [ln for ln in lines if not truncated(ln)]
    if len(kept) != len(lines):
        print(f"  [{workflow}] dropped {len(lines) - len(kept)} truncated line(s)", file=sys.stderr)
        lines = kept

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
    lines = [ln for ln in lines if len(ln.split()) >= 2] or lines

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
