#!/usr/bin/env python3
"""Produce a ForChi Short using one of the two X writing formats, and deliver it.

ForChi's Shorts are written by `tools/_video_script.py`, voiced by `tools/_video_voice.py` and
assembled by `tools/_video_assemble.py`. Since 2026-11-01 that writer offers the two formats used on
the X channels — `confession` (jephter) and `devotion` (emmanuel) — and rotates between them when no
format is named. This script runs that whole chain end to end and puts the result in Downloads, which
is the owner's delivery convention.

    python scripts\\forchi_proof.py --format confession
    python scripts\\forchi_proof.py --format devotion
    python scripts\\forchi_proof.py --format both

Three things this does that a hand-run does not:

* **absolute paths for the narration wavs.** ForChi's concat demuxer resolves relative paths against
  the list file's directory, so a relative wav path fails with
  `Error opening input file …assemble_build\\narr.wav`. Measured 2026-11-01.
* **the voice cache is reused.** `render_script` skips any `pN.wav` that already exists, so a re-run
  after an assembly bug costs zero TTS calls.
* **verification before delivery** — duration, streams, and a blackdetect pass, because a file that
  exists is not a file that is right.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

X_ROOT = Path(__file__).resolve().parent.parent
FORCHI = Path(r"C:\Users\hp\forchi")
sys.path.insert(0, str(X_ROOT / "scripts"))

import x_ffmpeg as xf  # noqa: E402

FORMATS = ("confession", "devotion")


def load_forchi() -> dict:
    """Import ForChi's modules with its own .env applied, the way its entry points do."""
    sys.path.insert(0, str(FORCHI / "tools"))
    env = FORCHI / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    os.chdir(FORCHI)  # ForChi's paths are relative to its own root
    import _video_assemble as asm
    import _video_script as script
    import _video_stitch as stitch
    import _video_voice as voice
    return {"script": script, "voice": voice, "stitch": stitch, "assemble": asm}


def make(format_name: str, seed: int) -> dict:
    mods = load_forchi()
    text = mods["script"].generate_script_checked(style=format_name)
    print(f"\n--- {format_name} script ({len(text.split())} words) ---")
    for i, line in enumerate(text.strip().splitlines(), 1):
        if line.strip():
            print(f"  {i}. {line.strip()}")

    work = FORCHI / "temp_media" / f"proof_{format_name}_{hashlib.sha1(text.strip().encode()).hexdigest()[:8]}"
    # The work dir is content-addressed on the script. ForChi's `render_script` skips any `pN.wav`
    # that already exists, so a fixed directory silently reuses the PREVIOUS script's audio while the
    # captions come from the new one — measured 2026-11-01: a delivered proof showed the caption
    # "griefs", a word from neither script, because the audio and the text had diverged.
    rendered = mods["voice"].render_script(text, str(work), mode="whisper")
    phrases = mods["voice"].split_phrases(text, max_len=220)
    if len(rendered) != len(phrases):
        raise SystemExit(f"{format_name}: {len(rendered)} wavs for {len(phrases)} phrases — refusing")

    wavs = [os.path.abspath(r["wav"]) for r in rendered]
    clips = mods["stitch"].pick_clips(phrases, seed=seed)
    clips = [{**c, "path": os.path.abspath(c["path"])} for c in clips]

    name = f"forchi_{format_name}"
    out = mods["assemble"].assemble(phrases, wavs, clips, name)
    if not out or not Path(out).exists():
        raise SystemExit(f"{format_name}: assembly produced no file")
    return {"format": format_name, "script": text, "file": Path(out), "phrases": phrases}


def verify(path: Path) -> dict:
    dur = xf.probe_dur(path)
    r = subprocess.run([xf.ffprobe(), "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
                       capture_output=True, text=True, errors="ignore")
    streams = json.loads(r.stdout or "{}").get("streams", [])
    kinds = [(s.get("codec_type"), s.get("codec_name")) for s in streams]
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    b = subprocess.run([xf.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
                        "-vf", "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, errors="ignore")
    black = [ln for ln in (b.stderr or "").splitlines() if "black_start" in ln]
    return {
        "duration_s": round(dur, 2),
        "under_cap": dur <= 58.0,
        "streams": kinds,
        "size": f"{video.get('width')}x{video.get('height')}",
        "black_intervals": len(black),
        "bytes": path.stat().st_size,
    }


def deliver(path: Path, format_name: str) -> Path:
    downloads = Path.home() / "Downloads"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dst = downloads / f"forchi_{format_name}_{stamp}_{xf.probe_dur(path):.0f}s.mp4"
    shutil.copy2(path, dst)
    return dst


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Produce a ForChi Short in an X writing format")
    ap.add_argument("--format", choices=FORMATS + ("both",), default="both")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-deliver", action="store_true")
    a = ap.parse_args()

    wanted = FORMATS if a.format == "both" else (a.format,)
    results = []
    for fmt in wanted:
        made = make(fmt, a.seed)
        info = verify(made["file"])
        print(f"\n  {fmt}: {info['duration_s']}s  {info['size']}  {info['streams']}  "
              f"{info['bytes'] // 1024} KB  black={info['black_intervals']}")
        if not a.no_deliver:
            dst = deliver(made["file"], fmt)
            print(f"  delivered: {dst}")
        results.append({**{k: v for k, v in made.items() if k != "file"},
                        "file": str(made["file"]), **info})

    report = X_ROOT / "out" / "forchi_proof.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n  report: {report.relative_to(X_ROOT)}")
    return 0 if all(r["under_cap"] and r["black_intervals"] == 0 for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
