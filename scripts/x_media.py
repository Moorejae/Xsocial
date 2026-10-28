#!/usr/bin/env python3
"""Label the clip and song pools for the two X workflows.

ForChi's clip manifest (`media/clips/manifest.json`) carries `usable`, `mood`, `color_temp`,
`brightness`, `style`, `subject`, `motion` per clip, and the stitcher picks from it with a
tone-contrast oscillation plus a freshness window so the same clip does not come back every day.
This is the same idea, but the labels are **measured** rather than eyeballed, because there are 54
and 43 clips and hand-labelling them is where a project like this quietly stops.

What is measured, per clip — one ffmpeg pass at 16x16 RGB, 4 fps:

| Field | How |
| :--- | :--- |
| `duration`, `width`, `height`, `fps` | ffprobe |
| `brightness` | mean luma across sampled frames -> `dark` (<60) / `mid` (<140) / `bright` |
| `color_temp` | mean R vs mean B -> `warm` (R-B > 12) / `cool` (B-R > 12) / `neutral` |
| `motion` | mean absolute frame-to-frame difference -> `low` / `mid` / `high` |
| `mood` | derived from the three above, overridable by hand in the JSON |
| `usable` | false when the file will not probe, is shorter than 2 s, or is not video |

Songs get `duration`, `mean_volume`, `peak_volume` and a derived `energy`; the renderer uses the
duration to choose a **different window** of the song for every video, which is how two videos end
up not sounding alike even when they share a track.

    python scripts\\x_media.py --label jephter
    python scripts\\x_media.py --label emmanuel --force

Output is committed (`config/scenes/<workflow>.json`, `config/songs/<workflow>.json`) so a run is
reproducible and a hand-correction survives. The media itself stays out of git.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "workflows.json"

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

SAMPLE_FPS = 4
SAMPLE_PX = 16


def ffmpeg() -> str:
    """System ffmpeg, never a bundled one: imageio-ffmpeg's Linux build has no drawtext."""
    for name in ("FFMPEG_BIN",):
        if os.environ.get(name):
            return os.environ[name]
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg not found on PATH (install it, or set FFMPEG_BIN)")
    return found


def utf8_stdout() -> None:
    """Clip names in these libraries are full of emoji and decorative script.

    A Windows console defaults to cp1252, so printing a filename raises UnicodeEncodeError and
    kills the run partway through a 54-file sweep. Replace unencodable characters instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ffprobe() -> str:
    found = shutil.which("ffprobe")
    if not found:
        raise SystemExit("ffprobe not found on PATH")
    return found


def probe(path: Path) -> dict:
    """Duration and stream facts. Returns {} when the file does not probe."""
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, errors="ignore",
    )
    if r.returncode != 0:
        return {}
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = any(s.get("codec_type") == "audio" for s in streams)
    try:
        duration = float(data.get("format", {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    try:
        num, den = (video.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration": round(duration, 3),
        "width": video.get("width") or 0,
        "height": video.get("height") or 0,
        "fps": round(fps, 3),
        "has_audio": audio,
        "has_video": bool(video),
    }


def sample_frames(path: Path, seconds: float) -> np.ndarray | None:
    """Mean RGB per sampled frame, shape (n, 3). Tiny on purpose: 16x16 at 4 fps."""
    limit = max(1.0, min(seconds, 60.0))  # 60 s of samples is plenty to characterise a clip
    r = subprocess.run(
        [ffmpeg(), "-v", "error", "-t", f"{limit:.2f}", "-i", str(path),
         "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_PX}:{SAMPLE_PX}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    frame_bytes = SAMPLE_PX * SAMPLE_PX * 3
    n = len(r.stdout) // frame_bytes
    if n == 0:
        return None
    arr = np.frombuffer(r.stdout[: n * frame_bytes], dtype=np.uint8)
    arr = arr.reshape(n, SAMPLE_PX * SAMPLE_PX, 3).astype(np.float32)
    return arr.mean(axis=1)


def classify_clip(path: Path) -> dict:
    meta = probe(path)
    entry = {
        "file": path.name,
        "usable": False,
        "duration": meta.get("duration", 0.0),
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "fps": meta.get("fps", 0.0),
        "has_audio": meta.get("has_audio", False),
        "brightness": "unknown",
        "color_temp": "unknown",
        "motion": "unknown",
        "mood": "neutral",
        "note": "",
    }
    if not meta.get("has_video") or entry["duration"] < 2.0:
        entry["note"] = "not video, or shorter than 2s"
        return entry

    frames = sample_frames(path, entry["duration"])
    if frames is None or len(frames) < 2:
        entry["note"] = "could not sample frames"
        return entry

    r, g, b = frames[:, 0], frames[:, 1], frames[:, 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mean_luma = float(luma.mean())
    warmth = float(r.mean() - b.mean())

    # motion: mean absolute difference between consecutive sampled frames, on luma only
    diff = np.abs(np.diff(luma)).mean()

    entry["brightness"] = "dark" if mean_luma < 60 else ("mid" if mean_luma < 140 else "bright")
    entry["color_temp"] = "warm" if warmth > 12 else ("cool" if warmth < -12 else "neutral")
    entry["motion"] = "low" if diff < 2.0 else ("mid" if diff < 6.0 else "high")
    entry["mood"] = derive_mood(entry["brightness"], entry["color_temp"], entry["motion"])
    entry["usable"] = True
    entry["note"] = f"luma {mean_luma:.0f}, warmth {warmth:+.0f}, motion {diff:.1f}"
    return entry


def derive_mood(brightness: str, color_temp: str, motion: str) -> str:
    """A romance-relevant mood, from the three measured axes.

    The picking side only needs to know how a clip *feels* against a line: a tender vow wants warm
    and soft, a confession wants bright and warm, a hard beat in the story wants cool and still.
    """
    if color_temp == "warm" and brightness in ("bright", "mid"):
        return "tender" if motion == "low" else "joyful"
    if color_temp == "warm" and brightness == "dark":
        return "longing"
    if color_temp == "cool" and brightness == "dark":
        return "melancholy"
    if color_temp == "cool" and brightness != "dark":
        return "calm"
    if motion == "high":
        return "restless"
    return "neutral"


def frame_jpeg(path: Path, out_dir: Path, at_fraction: float = 0.25, width: int = 360) -> Path:
    """One small JPEG from the middle-ish of a clip, for a vision pass.

    `out_dir` is explicit and lives under `out/` (git-ignored). The first version derived the folder
    from `path.parent.parent`, which for a clip in `jephtah/` is the **repository root** — so a
    vision pass dropped 97 frames into a public repo. Never infer a scratch path from a source path.
    """
    dur = probe(path).get("duration", 0)
    at = max(0.3, dur * at_fraction)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (path.stem[:60] + ".jpg")
    subprocess.run(
        [ffmpeg(), "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
        capture_output=True,
    )
    return out


VISION_PROMPT = """You are labelling frames from a stock-video library used for romantic short videos.

For EACH image, in the order given, answer with:
- "subject": "couple" (two people together, touching, looking at each other), "single" (one person),
  "scenery" (no people — landscape, object, texture, room), or "other".
- "mood": exactly one of tender, joyful, longing, melancholy, calm, restless, neutral.
- "baked_text": true if the frame contains visible burned-in text, a caption, a subtitle, a
  username or a logo that is part of the video itself (not something you add).

Rules:
- Judge only what is in the frame.
- "couple" requires two people. Two people far apart in a wide shot still count as couple.
- A frame with a person plus heavy text is still "single" or "couple"; set baked_text true.

Reply with JSON only, no prose:
{"labels":[{"subject":"...","mood":"...","baked_text":false}, ...]}
One entry per image, same order, same count."""


def label_subjects(workflow: str, batch: int = 8) -> int:
    """Add `subject`, a vision-checked `mood`, and `baked_text` to each clip.

    Measurement cannot tell a couple from a landscape or a warehouse still from a bedroom. For a
    romance channel that difference is the whole video, and burned-in captions are worse than a bad
    clip — they collide with our own captions and muddy the read. One frame per clip, batched, is
    ~7 requests for a 54-clip library.
    """
    import base64

    import gemini_keys as gk

    scenes_path = ROOT / "config" / "scenes" / f"{workflow}.json"
    if not scenes_path.exists():
        raise SystemExit(f"{scenes_path.relative_to(ROOT)} missing — run --label first")
    data = json.loads(scenes_path.read_text(encoding="utf-8"))
    wf = json.loads(CONFIG.read_text(encoding="utf-8"))[workflow]
    clips_dir = ROOT / wf["media_dir"]

    todo = [c for c in data["clips"] if c.get("usable")]
    print(f"  {workflow}: labelling {len(todo)} clips, {batch} per request")

    pool = gk.pool_for(workflow)
    by_file = {c["file"]: c for c in data["clips"]}

    # A text-model pool: reuse it for the vision call, which is the same endpoint.
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        parts: list[dict] = [{"text": VISION_PROMPT}]
        kept = []
        frames_dir = ROOT / "out" / "_frames" / workflow
        for c in chunk:
            jpg = frame_jpeg(clips_dir / c["file"], frames_dir)
            if not jpg.exists():
                continue
            parts.append({"inlineData": {"mimeType": "image/jpeg",
                                         "data": base64.b64encode(jpg.read_bytes()).decode()}})
            kept.append(c["file"])
        if not kept:
            continue
        try:
            raw = _vision_call(pool, parts)
            labels = json.loads(raw.strip().strip("`").removeprefix("json").strip())["labels"]
        except Exception as exc:
            print(f"  batch {start // batch + 1}: FAILED ({type(exc).__name__}: {str(exc)[:60]}) — "
                  f"keeping measured labels", flush=True)
            continue
        for fname, lab in zip(kept, labels):
            entry = by_file[fname]
            entry["subject"] = lab.get("subject", "other")
            entry["baked_text"] = bool(lab.get("baked_text"))
            if lab.get("mood"):
                entry["mood"] = lab["mood"]
        print(f"  batch {start // batch + 1}: {len(kept)} labelled", flush=True)

    subjects: dict[str, int] = {}
    for c in data["clips"]:
        subjects[c.get("subject", "unknown")] = subjects.get(c.get("subject", "unknown"), 0) + 1
    data["vision"] = {"labelled_at": date.today().isoformat(),
                      "subjects": subjects,
                      "baked_text": sum(1 for c in data["clips"] if c.get("baked_text"))}
    scenes_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  subjects: {subjects}")
    print(f"  clips with baked-in text: {data['vision']['baked_text']}")
    return 0


def _vision_call(pool, parts: list[dict]) -> str:
    """One multimodal call, rotated across the workflow's keys like every other request."""
    import gemini_keys as gk

    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": 0, "maxOutputTokens": 1400,
                                    "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    last = "no attempt"
    for i in range(4):
        keys = pool.keys
        key = keys[i % len(keys)]
        status, body = gk._request(f"{gk.BASE_URL}/models/gemini-3.6-flash:generateContent",
                                   key, payload, timeout=180)
        if status == 200 and isinstance(body, dict):
            text, why = gk._text_of(body)
            if text:
                return text
            last = f"empty ({why})"
        else:
            last = f"HTTP {status}"
    raise RuntimeError(last)


def classify_song(path: Path) -> dict:
    meta = probe(path)
    entry = {
        "file": path.name,
        "usable": False,
        "duration": meta.get("duration", 0.0),
        "mean_volume": None,
        "peak_volume": None,
        "energy": "unknown",
        "mood": "romantic",
        "note": "",
    }
    if not meta.get("duration") or meta["duration"] < 10.0:
        entry["note"] = "too short to score a video"
        return entry

    # volumedetect reports at INFO level, so -v error would hide the very numbers we parse.
    r = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, errors="ignore",
    )
    mean_v = peak_v = None
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            mean_v = float(line.split(":")[1].strip().replace(" dB", ""))
        if "max_volume" in line:
            peak_v = float(line.split(":")[1].strip().replace(" dB", ""))
    entry["mean_volume"] = mean_v
    entry["peak_volume"] = peak_v
    if mean_v is not None:
        entry["energy"] = "soft" if mean_v < -25 else ("mid" if mean_v < -16 else "loud")
        entry["usable"] = True
        entry["note"] = f"mean {mean_v:.1f} dB, peak {peak_v:.1f} dB"
    else:
        entry["note"] = "no audio stream found"
    return entry


def workflow_media(wf: dict) -> tuple[Path, Path]:
    clips = ROOT / wf.get("media_dir", "")
    songs = ROOT / wf.get("songs_dir", "")
    return clips, songs


def label(workflow: str, force: bool = False) -> int:
    workflows = json.loads(CONFIG.read_text(encoding="utf-8"))
    wf = workflows.get(workflow)
    if not wf:
        raise SystemExit(f"unknown workflow {workflow!r}")

    clips_dir, songs_dir = workflow_media(wf)
    scenes_out = ROOT / "config" / "scenes" / f"{workflow}.json"
    songs_out = ROOT / "config" / "songs" / f"{workflow}.json"
    scenes_out.parent.mkdir(parents=True, exist_ok=True)
    songs_out.parent.mkdir(parents=True, exist_ok=True)

    # ---- clips ----
    previous = {}
    if scenes_out.exists() and not force:
        previous = {e["file"]: e for e in json.loads(scenes_out.read_text(encoding="utf-8")).get("clips", [])}

    files = sorted(p for p in clips_dir.glob("*") if p.suffix.lower() in VIDEO_EXTS) if clips_dir.is_dir() else []
    if not files:
        print(f"  no clips found in {clips_dir}", file=sys.stderr)
    clips = []
    for i, path in enumerate(files, 1):
        if path.name in previous and previous[path.name].get("usable"):
            clips.append(previous[path.name])  # keep a hand-edited mood/usable
            continue
        entry = classify_clip(path)
        clips.append(entry)
        flag = "ok  " if entry["usable"] else "SKIP"
        print(f"  [{i}/{len(files)}] {flag} {path.name[:44]:<44} {entry['color_temp']:<8} "
              f"{entry['brightness']:<7} {entry['motion']:<5} -> {entry['mood']}", flush=True)

    usable = sum(1 for c in clips if c["usable"])
    scenes_out.write_text(json.dumps({
        "workflow": workflow,
        "media_dir": str(clips_dir.relative_to(ROOT)).replace("\\", "/"),
        "source": "scripts/x_media.py --label (measured, not hand-labelled)",
        "clips": clips,
    }, indent=2) + "\n", encoding="utf-8")

    # ---- songs ----
    prev_songs = {}
    if songs_out.exists() and not force:
        prev_songs = {e["file"]: e for e in json.loads(songs_out.read_text(encoding="utf-8")).get("songs", [])}
    song_files = sorted(p for p in songs_dir.glob("*") if p.suffix.lower() in AUDIO_EXTS) if songs_dir.is_dir() else []
    songs = []
    for i, path in enumerate(song_files, 1):
        # Reuse only a *successful* measurement. An unusable entry is a failed probe, not a
        # hand-correction, and caching it is how the whole song pool read "unknown" twice in a row.
        if path.name in prev_songs and prev_songs[path.name].get("usable"):
            songs.append(prev_songs[path.name])
            continue
        entry = classify_song(path)
        songs.append(entry)
        print(f"  [{i}/{len(song_files)}] {'ok  ' if entry['usable'] else 'SKIP'} "
              f"{path.name[:44]:<44} {entry['duration']:>6.1f}s {entry['energy']}", flush=True)

    songs_out.write_text(json.dumps({
        "workflow": workflow,
        "songs_dir": str(songs_dir.relative_to(ROOT)).replace("\\", "/"),
        "source": "scripts/x_media.py --label (measured)",
        "songs": songs,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\n{workflow}: {usable}/{len(clips)} clips usable, "
          f"{sum(1 for s in songs if s['usable'])}/{len(songs)} songs usable")
    print(f"  {scenes_out.relative_to(ROOT)}")
    print(f"  {songs_out.relative_to(ROOT)}")
    return 0


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description="Label clips and songs for one X workflow")
    ap.add_argument("--label", metavar="WORKFLOW")
    ap.add_argument("--vision", metavar="WORKFLOW",
                    help="add subject / baked_text / verified mood to a labelled pool")
    ap.add_argument("--force", action="store_true", help="re-measure even if labelled before")
    a = ap.parse_args()
    if a.vision:
        return label_subjects(a.vision)
    if not a.label:
        ap.error("--label WORKFLOW or --vision WORKFLOW is required")
    return label(a.label, a.force)


if __name__ == "__main__":
    raise SystemExit(main())
