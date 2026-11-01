#!/usr/bin/env python3
"""Label clips with an emotion, so a script can ask for the right picture.

The measured labels in `config/scenes/<wf>.json` answer "what does this shot look *like*" (bright,
warm, still). They cannot answer "what does it *feel* like", and that is the question a script
actually poses: a line about being afraid needs `fear`, not `cool + dark`.

So this pass asks a vision model for two things per clip:

| Field | Values |
| :--- | :--- |
| `emotion` | `happy` `sad` `excited` `angry` `calm` `love` `fear` `surprise` |
| `weather` | `clear` `rain` `snow` `storm` `fog` `night` `indoor` `none` |

`weather` is kept as its own axis rather than a ninth emotion: a rainy window can be `calm` or `sad`,
and a script that says "it rained the whole night we met" wants rain *and* whichever feeling the
line carries. Both are reported, so nothing is lost by splitting them.

Works on either library, so the same taxonomy covers this project and ForChi's Shorts pool:

    python scripts\\label_emotions.py --workflow jephter
    python scripts\\label_emotions.py --workflow emmanuel
    python scripts\\label_emotions.py --forchi
    python scripts\\label_emotions.py --workflow jephter --table   # print the table only
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_keys as gk  # noqa: E402
import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"
FORCHI = Path(r"C:\Users\hp\forchi")
FORCHI_SEGMENTS = FORCHI / "media" / "clips" / "segments"
FORCHI_LABELS = FORCHI / "media" / "clips" / "emotion_labels.json"
FORCHI_MANIFEST = FORCHI / "media" / "clips" / "manifest.json"

EMOTIONS = ("happy", "sad", "excited", "angry", "calm", "love", "fear", "surprise")
WEATHER = ("clear", "rain", "snow", "storm", "fog", "night", "indoor", "none")

PROMPT = """You label frames from a stock-video library used to build romantic short videos.

For EACH image, in the order given, answer:
- "emotion": exactly one of happy, sad, excited, angry, calm, love, fear, surprise.
  Judge the feeling the frame expresses: two people touching or gazing = love; tears, turning away,
  emptiness, grey stillness = sad; wide eyes, open mouth, hands raised = surprise; motion, dancing,
  running, laughter = excited; clenched hands, confrontation, hard faces = angry; darkness,
  isolation, running away, a threat in frame = fear; soft light, stillness, quiet faces = calm;
  smiling, light, playfulness = happy.
- "weather": exactly one of clear, rain, snow, storm, fog, night, indoor, none.
  Use "indoor" when the frame is clearly inside and weather is not visible. "night" when the scene is
  outdoors at night. "none" when nothing in frame suggests weather at all.

Rules:
- Pick the single strongest emotion. Do not hedge.
- Two people close together with warmth is love, not calm.
- Reply with JSON only, no prose:
{"labels":[{"emotion":"...","weather":"..."}, ...]}
One entry per image, same order, same count."""

MODEL = "gemini-3.6-flash"


def frame_of(clip: Path, out_dir: Path, width: int = 320) -> Path | None:
    """One JPEG from 25 % into the clip — past any fade, before any outro."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(str(clip).encode()).hexdigest()[:16]
    out = out_dir / f"{stem}.jpg"
    if out.exists() and out.stat().st_size > 512:
        return out
    dur = xf.probe_dur(clip)
    at = max(0.3, dur * 0.25)
    r = xf.run([xf.ffmpeg(), "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(clip),
                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)])
    return out if out.exists() else None


def vision(pool: gk.GeminiPool, parts: list[dict]) -> str:
    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": 0, "maxOutputTokens": 1600,
                                    "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    last = "no attempt"
    for i in range(5):
        key = pool.keys[i % len(pool.keys)]
        status, body = gk._request(f"{gk.BASE_URL}/models/{MODEL}:generateContent", key, payload,
                                   timeout=180)
        if status == 200 and isinstance(body, dict):
            text, why = gk._text_of(body)
            if text:
                return text
            last = f"empty ({why})"
        else:
            last = f"HTTP {status}"
    raise RuntimeError(last)


def label_dir(workflow: str, clips: list[Path], pool: gk.GeminiPool, batch: int = 8) -> dict:
    """Label a list of clips. Returns {file name: {emotion, weather}}."""
    frames_dir = ROOT / "out" / "_frames" / f"emo_{workflow}"
    results: dict[str, dict] = {}
    total = len(clips)
    for start in range(0, total, batch):
        chunk = clips[start:start + batch]
        parts: list[dict] = [{"text": PROMPT}]
        kept: list[Path] = []
        for c in chunk:
            jpg = frame_of(c, frames_dir)
            if not jpg:
                continue
            parts.append({"inlineData": {"mimeType": "image/jpeg",
                                         "data": base64.b64encode(jpg.read_bytes()).decode()}})
            kept.append(c)
        if not kept:
            continue
        try:
            raw = vision(pool, parts)
            data = json.loads(raw.strip().strip("`").removeprefix("json").strip())
            labels = data["labels"]
        except Exception as exc:
            print(f"  batch {start // batch + 1}: FAILED ({type(exc).__name__}: {str(exc)[:70]})",
                  flush=True)
            continue
        for clip, lab in zip(kept, labels):
            emotion = str(lab.get("emotion", "")).lower().strip()
            weather = str(lab.get("weather", "")).lower().strip()
            results[clip.name] = {
                "emotion": emotion if emotion in EMOTIONS else "calm",
                "weather": weather if weather in WEATHER else "none",
            }
        print(f"  batch {start // batch + 1}/{(total + batch - 1) // batch}: "
              f"{len(kept)} labelled ({len(results)}/{total})", flush=True)
    return results


def workflow_clips(workflow: str) -> tuple[list[Path], Path, dict]:
    wf = json.loads(CONFIG.read_text(encoding="utf-8"))[workflow]
    clips_dir = ROOT / wf["media_dir"]
    scenes = ROOT / "config" / "scenes" / f"{workflow}.json"
    data = json.loads(scenes.read_text(encoding="utf-8"))
    usable = {c["file"] for c in data["clips"] if c.get("usable")}
    clips = sorted(p for p in clips_dir.glob("*")
                   if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".m4v"} and p.name in usable)
    return clips, scenes, data


def run_workflow(workflow: str, batch: int) -> int:
    clips, scenes, data = workflow_clips(workflow)
    if not clips:
        raise SystemExit(f"{workflow}: no usable clips found")
    pool = gk.pool_for(workflow)
    print(f"{workflow}: labelling {len(clips)} clips", flush=True)
    labels = label_dir(workflow, clips, pool, batch)

    for c in data["clips"]:
        lab = labels.get(c["file"])
        if lab:
            c["emotion"] = lab["emotion"]
            c["weather"] = lab["weather"]
    data["emotions"] = {
        "labelled_at": date.today().isoformat(),
        "model": MODEL,
        "counts": dict(Counter(c.get("emotion") for c in data["clips"] if c.get("emotion"))),
        "weather_counts": dict(Counter(c.get("weather") for c in data["clips"] if c.get("weather"))),
    }
    scenes.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{workflow}: {len(labels)}/{len(clips)} labelled -> {scenes.relative_to(ROOT)}")
    print(f"  emotions: {data['emotions']['counts']}")
    print(f"  weather:  {data['emotions']['weather_counts']}")
    return 0


def run_forchi(batch: int) -> int:
    """Label ForChi's Shorts clip pool and record it where its stitcher can read it."""
    if not FORCHI_SEGMENTS.is_dir():
        raise SystemExit(f"ForChi segments not found: {FORCHI_SEGMENTS}")
    clips = sorted(p for p in FORCHI_SEGMENTS.glob("*.mp4"))
    if not clips:
        raise SystemExit("no segments to label")
    pool = gk.pool_for("jephter")   # a text/vision pool; the keys are the project's own
    print(f"forchi: labelling {len(clips)} segments", flush=True)
    labels = label_dir("forchi", clips, pool, batch)

    FORCHI_LABELS.write_text(json.dumps({
        "labelled_at": date.today().isoformat(),
        "model": MODEL,
        "source": "X/scripts/label_emotions.py",
        "taxonomy": {"emotion": list(EMOTIONS), "weather": list(WEATHER)},
        "counts": dict(Counter(v["emotion"] for v in labels.values())),
        "clips": labels,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"forchi: {len(labels)}/{len(clips)} labelled -> {FORCHI_LABELS}")

    # Merge into the existing manifest so ForChi's stitcher can filter on it without a code change:
    # every entry keeps its own fields and gains `emotion`/`weather_emo` (not `weather`, which already
    # means something else in that file).
    if FORCHI_MANIFEST.exists():
        try:
            manifest = json.loads(FORCHI_MANIFEST.read_text(encoding="utf-8"))
            merged = 0
            for entry in manifest:
                lab = labels.get(entry.get("file", ""))
                if lab:
                    entry["emotion"] = lab["emotion"]
                    entry["weather_emo"] = lab["weather"]
                    merged += 1
            FORCHI_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"forchi: merged into {FORCHI_MANIFEST.name} ({merged} entries)")
        except Exception as exc:
            print(f"  manifest merge skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"  emotions: {dict(Counter(v['emotion'] for v in labels.values()))}")
    return 0


def table(workflow: str | None, forchi: bool) -> int:
    if forchi:
        data = json.loads(FORCHI_LABELS.read_text(encoding="utf-8"))
        rows = [(k, v["emotion"], v["weather"]) for k, v in sorted(data["clips"].items())]
        out = ROOT / "out" / "forchi_emotion_table.md"
    else:
        wf = json.loads(CONFIG.read_text(encoding="utf-8"))[workflow]
        data = json.loads((ROOT / "config" / "scenes" / f"{workflow}.json").read_text(encoding="utf-8"))
        rows = [(c["file"], c.get("emotion", "-"), c.get("weather", "-"))
                for c in data["clips"] if c.get("emotion")]
        out = ROOT / "config" / "scenes" / f"{workflow}-labels.md"

    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {workflow if not forchi else 'forchi'} — clip emotion labels", "",
             f"{len(rows)} clips, labelled by `scripts/label_emotions.py`.", "",
             "| Clip | Emotion | Weather |", "| :--- | :--- | :--- |"]
    lines += [f"| {n} | **{e}** | {w} |" for n, e, w in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n-> {out}")
    return 0


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Emotion+weather labels for clip libraries")
    ap.add_argument("--workflow", choices=("jephter", "emmanuel"))
    ap.add_argument("--forchi", action="store_true", help="label ForChi's Shorts clip pool")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--table", action="store_true", help="print the table instead of labelling")
    a = ap.parse_args()

    if a.forchi:
        return table(None, True) if a.table else run_forchi(a.batch)
    if not a.workflow:
        ap.error("--workflow or --forchi is required")
    return table(a.workflow, False) if a.table else run_workflow(a.workflow, a.batch)


if __name__ == "__main__":
    raise SystemExit(main())
