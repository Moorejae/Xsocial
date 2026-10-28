#!/usr/bin/env python3
"""Shared ffmpeg plumbing for the X video workflows.

Three things this exists to stop repeating, each of them a bug that already shipped somewhere:

1. **The wrong ffmpeg.** `imageio-ffmpeg`'s Linux build has no `drawtext`, so a bundled binary
   silently loses the watermark. This always prefers the system binary.
2. **`-v error` hiding the numbers.** `volumedetect`, `silencedetect` and `ebur128` all report at
   INFO level; suppressing output to keep the console quiet makes them return nothing, which once
   made a whole song pool read as "unknown".
3. **Windows path colons inside filter graphs.** `subtitles=C\\:path` needs escaping that is easy
   to get wrong. Callers run ffmpeg with `cwd=work` and pass relative paths instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def utf8_stdout() -> None:
    """Clip and song names here are full of emoji; a cp1252 console would crash on the first one."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ffmpeg() -> str:
    if os.environ.get("FFMPEG_BIN"):
        return os.environ["FFMPEG_BIN"]
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg not found on PATH (install it, or set FFMPEG_BIN)")
    return found


def ffprobe() -> str:
    if os.environ.get("FFPROBE_BIN"):
        return os.environ["FFPROBE_BIN"]
    found = shutil.which("ffprobe")
    if not found:
        raise SystemExit("ffprobe not found on PATH")
    return found


def run(args: list[str], *, cwd: Path | str | None = None, quiet: bool = True,
        timeout: float = 900.0) -> subprocess.CompletedProcess:
    """Run a command. `quiet` hides stdout but never stderr's tail on failure — silent failure is
    the failure mode this project cannot have."""
    r = subprocess.run([str(a) for a in args], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, errors="ignore", timeout=timeout)
    if r.returncode != 0 and not quiet:
        raise RuntimeError(f"{args[0]} failed rc={r.returncode}: {(r.stderr or '')[-600:]}")
    return r


def probe_dur(path: Path | str) -> float:
    r = run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        return 0.0


def levels(path: Path | str) -> dict:
    """Mean/peak dB via volumedetect (INFO level — do not pass -v error)."""
    r = run([ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
             "-af", "volumedetect", "-f", "null", "-"])
    mean_v = peak_v = None
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean_v = float(line.split(":")[1].strip().replace(" dB", ""))
        elif "max_volume" in line:
            peak_v = float(line.split(":")[1].strip().replace(" dB", ""))
    return {"mean_db": mean_v, "peak_db": peak_v}


def peak_gain_filter(path: Path | str, target_db: float = -1.5) -> str:
    """Peak-normalise with a hard limiter.

    **Never `loudnorm`.** It dropped ~3 s of audio on ffmpeg 7.1 in this exact pipeline; peak gain
    plus `alimiter` is what replaced it.
    """
    lv = levels(path)
    peak = lv["peak_db"]
    if peak is None:
        return "volume=1.0"
    return f"volume={target_db - peak:.2f}dB,alimiter=limit=0.95"
