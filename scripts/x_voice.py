#!/usr/bin/env python3
"""Narrate a script for one X workflow, one audio file per phrase.

Why per phrase and not one long take
------------------------------------
ForChi renders each phrase separately (`p1.wav`, `p2.wav`, …) and that is what makes the assembly
controllable: the duration of each phrase is known exactly, so captions can be timed, cuts can be
placed on phrase boundaries, and a phrase that runs long can be dropped without re-rendering the
whole take. It also bounds the blast radius of a TTS failure to one line.

Voice backends
--------------
| Backend | State | Why |
| :--- | :--- | :--- |
| `gemini` | **default, works today** | the existing key pools reach `gemini-3.8-flash-tts`; no GPU, no HF Space, no reference clip needed |
| `f5` | planned | a true clone needs a reference clip per workflow. The interface is the same, so swapping later does not touch the renderer |

The two workflows must not sound the same. That is a config fact (`voice.name` + `voice.style`),
and `--audition` is how it is chosen rather than guessed: it renders the same line in several
candidate voices, measures pace and breathiness, and writes them side by side.

    python scripts\\x_voice.py --workflow jephter --audition
    python scripts\\x_voice.py --workflow jephter --say "I still remember that night." --out out/te.wav
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_keys as gk  # noqa: E402
import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"

# Candidates worth auditioning for a romantic, whispery read. Both workflows must end up on
# different entries; the audit in x_render refuses a build whose two voices are identical.
CANDIDATES = ("Aoede", "Callirrhoe", "Leda", "Achernar", "Despina", "Erinome",
              "Laomedeia", "Sulafat", "Vindemiatrix", "Pulcherrima")


def utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_workflow(name: str) -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    if name not in data:
        raise SystemExit(f"unknown workflow {name!r}")
    return data[name]


def pcm_to_wav(raw: bytes, mime: str, out_path: Path) -> Path:
    """Normalise whatever the TTS model returns into a real .wav on disk.

    Measured 2026-10-27: the three TTS models disagree about the format —
    `audio/wav` (already a container), `audio/l16; rate=24000; channels=1` and
    `audio/L16;codec=pcm;rate=24000` (bare signed 16-bit little-endian PCM). Bare PCM needs a
    44-byte header or every downstream tool reads it as noise.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mime_l = mime.lower()

    if "wav" in mime_l:
        out_path.write_bytes(raw)
        return out_path

    rate = 24000
    channels = 1
    for part in mime_l.split(";")[1:]:
        part = part.strip()
        if part.startswith("rate="):
            rate = int(part.split("=", 1)[1])
        elif part.startswith("channels="):
            channels = int(part.split("=", 1)[1])
    bits = 16 if ("l16" in mime_l or "pcm" in mime_l) else 16
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(raw)
    return out_path


def probe_dur(path: Path) -> float:
    return xf.probe_dur(path)


def audio_facts(path: Path) -> dict:
    """Duration, peak level and how much of the file is near-silence.

    Silence ratio is the cheap, honest proxy for "whispery": a whispered read sits further below
    full scale and carries more quiet between words than a projected one.
    """
    dur = xf.probe_dur(path)
    lv = xf.levels(path)
    r2 = xf.run([xf.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
                 "-af", "silencedetect=noise=-40dB:d=0.18", "-f", "null", "-"])
    quiet = sum(float(m.split("silence_duration:")[1].split()[0])
                for m in (r2.stderr or "").splitlines() if "silence_duration:" in m)
    return {
        "duration": round(dur, 2),
        "mean_db": lv["mean_db"],
        "peak_db": lv["peak_db"],
        "quiet_ratio": round(quiet / dur, 3) if dur else 0.0,
        "words_per_second": None,  # filled by the caller, which knows the word count
    }


WHISPER_CUE = "[speaking slowly and softly, almost in a whisper]"


def synthesize_phrase(pool: gk.GeminiPool, wf: dict, text: str, out_path: Path) -> Path:
    """Render one phrase.

    The cue is a **bracketed direction**, and that is not a stylistic choice — it is the only form
    measured to work. On 2026-10-27, transcribing the output back with an audio-capable model gave:

    | prompt shape | spoken back | level |
    | :--- | :--- | :--- |
    | `"<style>. <line>"` | the style text too — *"speak slowly and softly I still remember…"* | -19 dB |
    | `Say slowly: "<line>"` | the line only | -23.6 dB |
    | `[whispering] <line>` | **the line only** | -28.4 dB |

    A bracket (or parenthetical) cue is stripped from the speech and still changes the delivery: the
    read came back ~9 dB quieter and slightly slower, which is what a whisper *is*. Prepending the
    style as prose is read aloud, so it also silently lengthens every video.
    """
    voice_cfg = wf.get("voice", {})
    cue = voice_cfg.get("cue", WHISPER_CUE)
    prompt = f"{cue} {text}".strip() if cue else text
    raw, mime = pool.generate_audio(prompt, voice=voice_cfg.get("name", "Aoede"), style=None)
    return pcm_to_wav(raw, mime, out_path)


def say(workflow: str, text: str, out_path: Path, voice: str | None = None,
        style: str | None = None) -> Path:
    wf = load_workflow(workflow)
    if voice:
        wf = {**wf, "voice": {**wf.get("voice", {}), "name": voice}}
    if style:
        wf = {**wf, "voice": {**wf.get("voice", {}), "style": style}}
    pool = gk.pool_for(workflow)
    path = synthesize_phrase(pool, wf, text, out_path)
    print(f"  wrote {path} ({probe_dur(path):.2f}s)")
    return path


def audition(workflow: str) -> int:
    """Render the same line in several voices and report measurable differences."""
    wf = load_workflow(workflow)
    line = ("I still remember the way you looked at me that night, "
            "and how the whole room went quiet.")
    words = len(line.split())
    out_dir = ROOT / "out" / "audition" / workflow
    out_dir.mkdir(parents=True, exist_ok=True)

    pool = gk.pool_for(workflow)
    rows = []
    for voice in CANDIDATES:
        path = out_dir / f"{voice}.wav"
        try:
            synthesize_phrase(pool, {**wf, "voice": {**wf.get("voice", {}), "name": voice}}, line, path)
        except Exception as exc:  # a rejected voice name must not kill the audition
            print(f"  {voice:<14} FAILED: {type(exc).__name__}: {str(exc)[:70]}")
            continue
        facts = audio_facts(path)
        facts["words_per_second"] = round(words / facts["duration"], 2) if facts["duration"] else 0
        facts["voice"] = voice
        rows.append(facts)
        print(f"  {voice:<14} {facts['duration']:>5.2f}s  {facts['words_per_second']:>4.2f} w/s  "
              f"mean {facts['mean_db']:>6.1f} dB  quiet {facts['quiet_ratio']:>5.3f}")

    if not rows:
        print("no candidate voice rendered", file=sys.stderr)
        return 1
    (out_dir / "audition.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    slowest = min(rows, key=lambda r: r["words_per_second"])
    print(f"\n  slowest read: {slowest['voice']} ({slowest['words_per_second']} w/s) — best fit for a whisper")
    print(f"  files in {out_dir.relative_to(ROOT)}")
    return 0


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description="Narrate for one X workflow")
    ap.add_argument("--workflow")
    ap.add_argument("--say", metavar="TEXT")
    ap.add_argument("--out", metavar="PATH")
    ap.add_argument("--voice", help="override the configured voice for one render")
    ap.add_argument("--style", help="override the style direction for one render")
    ap.add_argument("--audition", action="store_true", help="compare candidate voices")
    a = ap.parse_args()
    if not a.workflow:
        ap.error("--workflow is required")
    if a.audition:
        return audition(a.workflow)
    if not a.say or not a.out:
        ap.error("--say and --out are required (or use --audition)")
    say(a.workflow, a.say, ROOT / a.out if not Path(a.out).is_absolute() else Path(a.out),
        a.voice, a.style)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
