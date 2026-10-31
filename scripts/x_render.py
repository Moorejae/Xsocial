#!/usr/bin/env python3
"""Render one vertical short for one X workflow.

The shape is ForChi's Shorts assembly, with the parts this project needs changed and nothing else:

| Stage | ForChi | Here |
| :--- | :--- | :--- |
| cap | `MAX_SEC = 58` | `video.max_seconds`, **50** |
| cuts | one clip per phrase | one clip per **cut** (~2.6-4.8 s), so 50 s carries 11-16 hard cuts |
| clip choice | tone-contrast oscillation + freshness | same, from the measured manifest `config/scenes/<wf>.json` |
| music | synthesised drone / piano library | the workflow's **own songs**, and a different window of the track each time |
| captions | one word per event, Ink Free | 3-word chunks, **Jost Bold** (REZMO's own face) |
| levels | peak gain + `alimiter`, never `loudnorm` | same — `loudnorm` dropped ~3 s of audio on ffmpeg 7.1 |
| fonts | relative path, `cwd=work` | same, because a Windows drive-letter colon inside a filter graph is a parse error |

The one requirement that is not ForChi's: **no two videos may sound the same.** A song is chosen with
a freshness window, and the 50-second excerpt is taken at a different offset every time — offsets
walk the track in golden-ratio steps, which spreads windows as far apart as possible instead of
creeping forward. The ledger in `out/<workflow>/song_usage.json` is what makes that true across
runs rather than only within one.

    python scripts\\x_render.py --workflow jephter --phrases-file out/jephter/script.json
    python scripts\\x_render.py --workflow jephter --seed 11 --keep-work
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_keys as gk  # noqa: E402
import x_ffmpeg as xf  # noqa: E402
import x_voice as xv  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"
GOLDEN = 0.6180339887498949  # the "most irrational" number: successive windows never line up


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------

def load_workflow(name: str) -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    if name not in data:
        raise SystemExit(f"unknown workflow {name!r}; known: {[k for k in data if not k.startswith('_')]}")
    return data[name]


def load_manifest(workflow: str, kind: str) -> list[dict]:
    path = ROOT / "config" / f"{kind}" / f"{workflow}.json"
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} missing — run: python scripts\\x_media.py --label {workflow}")
    key = "clips" if kind == "scenes" else "songs"
    entries = json.loads(path.read_text(encoding="utf-8")).get(key, [])
    usable = [e for e in entries if e.get("usable")]
    if len(usable) < 4:
        raise SystemExit(f"{workflow}: only {len(usable)} usable {key} — refusing to build a repetitive video")
    return usable


def workflow_key(workflow: str) -> str:
    """The key used for the ledger and output dirs. `jephter` and the media folder `jephtah` differ;
    the ledger must follow the workflow, not the folder, or the two would drift."""
    return workflow


# --------------------------------------------------------------------------------------
# clip choice: tone contrast + freshness, as ForChi's stitcher does it
# --------------------------------------------------------------------------------------

MOOD_HINTS = [
    (("warm", "soft", "hold", "close", "breathe", "gentle", "quiet", "still"), "tender"),
    (("light", "laugh", "smile", "morning", "coffee", "kitchen", "open"), "joyful"),
    (("wait", "miss", "gone", "empty", "cold", "alone", "late", "rain", "leave"), "longing"),
    (("remember", "know", "truth", "promise", "always", "never"), "calm"),
]


def mood_for(text: str, default: str = "tender") -> str:
    t = text.lower()
    for words, mood in MOOD_HINTS:
        if any(w in t for w in words):
            return mood
    return default


def pick_cuts(workflow: str, phrases: list[str], seg_durs: list[float], wf: dict,
              seed: int) -> list[dict]:
    """One clip per cut. Returns [{phrase, path, file, mood, color_temp}] in play order.

    Preference order, and it is cost-first because this runs on every video:
    a **couple** or **single** frame (this channel is about two people, not scenery), that is not
    already used in this video nor in the recent window, that matches the line's mood, and that has
    **no burned-in text** — the last of those is a veto, not a preference: a stock caption fighting
    our caption makes both unreadable. A clip with text is only used when nothing else is left.
    """
    v = wf.get("video", {})
    lo, hi = v.get("cut_seconds", [2.6, 4.8])
    target = (lo + hi) / 2

    clips = load_manifest(workflow, "scenes")
    clips_dir = ROOT / wf["media_dir"]
    usage = load_usage(workflow)
    recent = set(usage.get("recent_clips", []))

    rng = random.Random(seed)
    pool = list(clips)
    rng.shuffle(pool)

    people = [c for c in pool if c.get("subject") in ("couple", "single")]
    clean_people = [c for c in people if not c.get("baked_text")]
    pristine = [c for c in pool if not c.get("baked_text")]
    # Tiers tried in order: clean people, then any clean clip, then people (even with text), then all.
    tiers = [t for t in (clean_people, pristine, people, pool) if t]

    def temp_of(c: dict) -> str:
        return c.get("color_temp") or "neutral"

    used: set[str] = set()
    last_temp = None
    plan: list[dict] = []

    def take(src: list[dict], mood: str | None, want: str) -> dict:
        same_temp = [c for c in src if temp_of(c) == want]
        for c in same_temp:                       # fresh + mood + temperature
            if c["file"] not in used and c["file"] not in recent and (not mood or c.get("mood") == mood):
                return c
        for c in same_temp:                       # fresh + temperature
            if c["file"] not in used and c["file"] not in recent:
                return c
        for c in src:                             # fresh anywhere in the tier
            if c["file"] not in used and c["file"] not in recent:
                return c
        for c in src:                             # unused in this video
            if c["file"] not in used:
                return c
        return src[0]

    for i, (phrase, dur) in enumerate(zip(phrases, seg_durs)):
        n_cuts = max(1, min(4, round(dur / target)))
        each = dur / n_cuts
        for k in range(n_cuts):
            mood = mood_for(phrase)
            want = "warm" if mood in ("tender", "joyful") else "cool"
            if last_temp == want:                 # oscillate rather than sit in one temperature
                want = "cool" if want == "warm" else "warm"
            pick = None
            for tier in tiers:
                candidate = take(tier, mood, want)
                if candidate["file"] not in used:
                    pick = candidate
                    break
            pick = pick or tiers[0][0]
            used.add(pick["file"])
            last_temp = temp_of(pick)
            plan.append({
                "phrase": i,
                "cut": k,
                "seconds": round(each, 3),
                "file": pick["file"],
                "path": str(clips_dir / pick["file"]),
                "mood": pick.get("mood"),
                "color_temp": pick.get("color_temp"),
                "subject": pick.get("subject"),
                "baked_text": pick.get("baked_text"),
                "content": pick.get("content"),
            })

    usage["recent_clips"] = ([c for c in usage.get("recent_clips", []) if c not in {p["file"] for p in plan}]
                             + [p["file"] for p in plan])[-30:]
    save_usage(workflow, usage)
    return plan


# --------------------------------------------------------------------------------------
# song choice: same track may repeat, the same *sound* must not
# --------------------------------------------------------------------------------------

def pick_song(workflow: str, seconds: float, wf: dict, seed: int) -> dict:
    """Choose a song and a 50 s window of it that this workflow has not used before.

    The offset walks the remaining span in golden-ratio steps keyed on how many times that track
    has been used, so consecutive videos land in genuinely different parts of the song instead of
    drifting a second or two. That is the difference between "the same song" and "the same sound".
    """
    songs = load_manifest(workflow, "songs")
    usage = load_usage(workflow)
    per_song = usage.setdefault("songs", {})
    recent = usage.get("recent_songs", [])

    candidates = sorted(songs, key=lambda s: (s["file"] in recent, per_song.get(s["file"], {}).get("uses", 0),
                                              random.Random(f"{seed}:{s['file']}").random()))
    song = candidates[0]

    state = per_song.setdefault(song["file"], {"uses": 0, "offsets": []})
    span = max(0.0, float(song["duration"]) - seconds - 1.5)
    offset = 0.0
    for attempt in range(24):
        frac = ((state["uses"] + 1) * GOLDEN + attempt * GOLDEN) % 1.0
        offset = round(frac * span, 2)
        if all(abs(offset - prev) > max(6.0, seconds * 0.15) for prev in state["offsets"][-6:]):
            break
    state["uses"] += 1
    state["offsets"] = (state["offsets"] + [offset])[-12:]

    usage["recent_songs"] = ([s for s in recent if s != song["file"]] + [song["file"]])[-3:]
    save_usage(workflow, usage)

    return {
        "file": song["file"],
        "path": str(ROOT / wf["songs_dir"] / song["file"]),
        "song_duration": song["duration"],
        "offset": offset,
        "seconds": round(seconds, 3),
        "use_number": state["uses"],
    }


# --------------------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------------------

def usage_path(workflow: str) -> Path:
    return ROOT / wf_output_root(workflow) / "usage.json"


def wf_output_root(workflow: str) -> str:
    return load_workflow(workflow).get("output_dir", f"out/{workflow}")


def load_usage(workflow: str) -> dict:
    path = ROOT / wf_output_root(workflow) / "usage.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_usage(workflow: str, usage: dict) -> None:
    path = ROOT / wf_output_root(workflow) / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# captions
# --------------------------------------------------------------------------------------

def word_timings(phrase: str, start: float, dur: float, lead: float = 0.10) -> list[dict]:
    """Proportional word timings inside a phrase.

    ForChi does the same, and it is the right call here: a whispery read has no reliable
    inter-word silence for `silencedetect` to find, so measured boundaries would be sparse and
    jumpy. Proportional by character length tracks the audio closely enough for captions.
    """
    words = phrase.split()
    if not words:
        return []
    total = sum(len(w) for w in words)
    usable = max(dur - lead, 0.2)
    out, t = [], start + lead
    for w in words:
        d = usable * (len(w) / total)
        out.append({"word": w, "start": t, "end": t + d})
        t += d
    return out


def _ass_ts(sec: float) -> str:
    sec = max(sec, 0.0)
    hh = int(sec // 3600)
    mm = int((sec % 3600) // 60)
    return f"{hh}:{mm:02d}:{sec % 60:05.2f}"


def _ass_text(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\\", "/")


FUNCTION_WORDS = {"the", "a", "an", "and", "but", "to", "of", "in", "on", "at", "for", "with",
                  "your", "their", "my", "his", "her", "that", "this", "it", "as", "from", "by"}


def chunk_words(words: list[dict], per: int) -> list[list[dict]]:
    """Split a phrase into caption chunks that do not strand a function word at the end.

    Fixed-size chunks produce lines like *"and gather the"* — measured on the first emmanuel test
    render, and it reads as a mistake on a still frame. When a chunk would end on `the`/`and`/`of`
    and the phrase continues, take one more word so the chunk ends on something that carries
    meaning. The chunk then runs one word long, which is far less noticeable than a dangling article.
    """
    chunks: list[list[dict]] = []
    i = 0
    while i < len(words):
        chunk = words[i:i + per]
        n = len(chunk)
        if (i + n < len(words) and n > 1
                and chunk[-1]["word"].strip(".,!?;:").lower() in FUNCTION_WORDS):
            n += 1
            chunk = words[i:i + n]
        chunks.append(chunk)
        i += n
    return chunks


def build_ass(phrases: list[str], starts: list[float], durs: list[float], cap: dict,
              out_path: Path) -> Path:
    """Word-chunk captions in the workflow's face.

    Jost Bold, white, heavy outline — the same family REZMO ships (`public/fonts/jost.woff2`,
    instantiated at wght 700 into a static TTF by `scripts/make_fonts.py`). The outline is what
    makes a caption legible over moving footage, which is the stated reason for wanting it bold.
    """
    size = cap.get("size", 82)
    per = max(1, int(cap.get("words_per_caption", 3)))
    colour = cap.get("colour", "&H00FFFFFF")
    outline = cap.get("outline", 4)
    shadow = cap.get("shadow", 2)
    margin_v = cap.get("margin_v", 420)
    bold = -1 if cap.get("bold") else 0
    family = cap.get("family", "Jost")

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: X,{family},{size},{colour},&H000000FF,&H00101010,&H80000000,"
        f"{bold},0,0,0,100,100,0.6,0,1,{outline},{shadow},2,70,70,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for phrase, start, dur in zip(phrases, starts, durs):
        words = word_timings(phrase, start, dur)
        for chunk in chunk_words(words, per):
            if not chunk:
                continue
            text = " ".join(w["word"] for w in chunk)
            s = chunk[0]["start"]
            e = chunk[-1]["end"] + 0.04
            events.append(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(e)},X,,0,0,0,,{_ass_text(text)}")

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return out_path


# --------------------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------------------

def render(workflow: str, phrases: list[str], voice_wavs: list[Path], wf: dict,
           out_name: str | None = None, seed: int | None = None, keep_work: bool = False) -> dict:
    v = wf.get("video", {})
    cap = wf.get("caption", {})
    mus = wf.get("music", {})
    WM = wf.get("watermark", {})

    width, height, fps = v.get("width", 1080), v.get("height", 1920), v.get("fps", 30)
    gap = float(v.get("phrase_gap", 0.34))
    tail = float(v.get("tail", 1.5))
    max_sec = float(v.get("max_seconds", 50))

    seed = seed if seed is not None else int(time.time()) % 10**6
    durs = [xf.probe_dur(w) for w in voice_wavs]
    if any(d <= 0 for d in durs):
        raise SystemExit("a narration file has zero duration — refusing to assemble a silent video")

    # Never cut the last line: it is the image the video ends on. Drop from the middle instead, so
    # the hook and the close both survive. (ForChi drops leading phrases; for a 6-line romance the
    # middle is the safer place to lose a beat.)
    def total_of(ds: list[float]) -> float:
        return (sum(ds) + gap * (len(ds) - 1) + tail) if ds else 0.0

    dropped = []
    while total_of(durs) > max_sec and len(durs) > 2:
        mid = len(durs) // 2
        dropped.append(phrases[mid])
        phrases.pop(mid)
        voice_wavs.pop(mid)
        durs.pop(mid)

    total = total_of(durs)
    if total > max_sec and len(durs) == 2:
        # Two very long lines: shorten the video, not the words, by trimming the tail.
        tail = max(0.4, tail - (total - max_sec))
        total = total_of(durs)
    if total > max_sec:
        raise SystemExit(f"{workflow}: {total:.1f}s still over the {max_sec:.0f}s cap after trimming — "
                         f"the narration is too long for this workflow's voice pace")

    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d + gap

    # cut plan
    seg_durs = [d + (tail if i == len(durs) - 1 else gap) for i, d in enumerate(durs)]
    cuts = pick_cuts(workflow, phrases, seg_durs, wf, seed)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = ROOT / wf.get("output_dir", f"out/{workflow}")
    work = out_root / "work" / f"{stamp}_{seed % 1000:03d}"
    work.mkdir(parents=True, exist_ok=True)

    # fonts must be local to the work dir: a Windows "C:\" inside a filter argument is a parse error
    for key in ("caption", "watermark"):
        src = wf.get(key, {}).get("font")
        if src and (ROOT / src).exists():
            shutil.copy(ROOT / src, work / (Path(src).stem + ".ttf"))

    print(f"  [{workflow}] {len(phrases)} phrases, {len(cuts)} cuts, {total:.1f}s")

    # ---- video segments ----
    seg_files: list[Path] = []
    for i, cut in enumerate(cuts):
        seg = work / f"v{i:03d}.mp4"
        # A letterboxed source keeps its black bars through scale+crop, because the bars are part
        # of the picture. Crop to the detected content rectangle first, then fill the frame.
        pre = ""
        rect = cut.get("content")
        if rect:
            cw, ch, cx, cy = (int(v) for v in rect.split(":"))
            pre = f"crop={cw}:{ch}:{cx}:{cy},"
        r = xf.run([
            xf.ffmpeg(), "-y", "-stream_loop", "-1", "-i", cut["path"],
            "-t", f"{cut['seconds']:.3f}",
            "-vf", f"{pre}scale={width}:{height}:force_original_aspect_ratio=increase,"
                   f"crop={width}:{height},fps={fps},format=yuv420p,setsar=1",
            "-an", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", str(seg),
        ])
        if not seg.exists():
            raise SystemExit(f"segment {i} failed: {(r.stderr or '')[-300:]}")
        seg_files.append(seg)

    vlist = work / "vlist.txt"
    vlist.write_text("\n".join(f"file '{p.as_posix()}'" for p in seg_files) + "\n", encoding="utf-8")
    vcat = work / "vcat.mp4"
    xf.run([xf.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(vlist),
            "-c", "copy", str(vcat)])
    if not vcat.exists():
        raise SystemExit("video concat failed")

    # ---- narration with phrase gaps ----
    rate = "24000"
    sil = work / "gap.wav"
    sil_tail = work / "tail.wav"
    xf.run([xf.ffmpeg(), "-y", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
            "-t", f"{gap}", "-c:a", "pcm_s16le", str(sil)])
    xf.run([xf.ffmpeg(), "-y", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
            "-t", f"{tail}", "-c:a", "pcm_s16le", str(sil_tail)])

    alist = work / "alist.txt"
    lines = []
    for i, w in enumerate(voice_wavs):
        lines.append(f"file '{Path(w).as_posix()}'")
        lines.append(f"file '{sil_tail.as_posix() if i == len(voice_wavs) - 1 else sil.as_posix()}'")
    alist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    narr = work / "narr.wav"
    xf.run([xf.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(alist),
            "-c", "copy", str(narr)])
    if not narr.exists():
        raise SystemExit("narration concat failed")

    # ---- captions ----
    subs = build_ass(phrases, starts, durs, cap, work / "subs.ass")

    # ---- song window ----
    song = pick_song(workflow, total, wf, seed)
    bed = work / "bed.wav"
    fade_in = float(mus.get("fade_in", 1.4))
    fade_out = float(mus.get("fade_out", 2.2))
    fade_out_st = max(total - fade_out, 0.0)
    xf.run([xf.ffmpeg(), "-y",
            "-ss", f"{song['offset']:.3f}", "-t", f"{total:.3f}", "-i", song["path"],
            "-af", f"afade=t=in:st=0:d={fade_in},"
                   f"afade=t=out:st={fade_out_st:.2f}:d={fade_out},"
                   f"volume={float(mus.get('gain', 0.2)):.3f}",
            "-ar", rate, "-ac", "2", str(bed)])
    if not bed.exists():
        raise SystemExit(f"song bed failed: {song['file']}")

    # ---- final mux ----
    narr_gain = xf.peak_gain_filter(narr)
    vf = "subtitles=subs.ass:fontsdir=."
    wm_font = work / (Path(WM.get("font", "")).stem + ".ttf")
    if WM.get("text") and wm_font.exists():
        size = int(WM.get("size", 34))
        op = float(WM.get("opacity", 0.55))
        vf += (f",drawtext=fontfile={wm_font.name}:text={WM['text']}:fontsize={size}:"
               f"fontcolor=white@{op}:x=(w-text_w)/2:y=110:"
               f"shadowcolor=black@0.45:shadowx=0:shadowy=2")

    # Caption scrim. Order matters: the gradient goes UNDER the subtitles, so the caption keeps its
    # full brightness while the footage behind it is calmed. Generated once by scripts/make_assets.py
    # and committed, so the renderer needs neither PIL nor a re-derivation.
    scrim = ROOT / "assets" / f"scrim_{width}x{height}.png"

    name = out_name or f"{workflow}_{stamp}"
    out = out_root / f"{name}.mp4"
    if scrim.exists():
        graph = f"[0:v][3:v]overlay=0:0:format=auto[v0];[v0]{vf}[v];"
    else:
        graph = f"[0:v]{vf}[v];"
    cmd = [
        xf.ffmpeg(), "-y", "-i", str(vcat), "-i", str(narr), "-i", str(bed),
    ]
    if scrim.exists():
        cmd += ["-loop", "1", "-i", str(scrim)]
    cmd += [
        "-filter_complex",
        graph +
        f"[1:a]{narr_gain}[narr];"
        f"[2:a]volume=1.0[bed];"
        f"[narr][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.95[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "19", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-t", f"{total:.3f}", "-movflags", "+faststart", str(out),
    ]
    r = xf.run(cmd, cwd=work)
    if not out.exists():
        raise SystemExit("final mux failed:\n" + "\n".join((r.stderr or "").splitlines()[-12:]))

    # verify, because a file that exists is not a file that is right
    final_dur = xf.probe_dur(out)
    lv = xf.levels(out)
    report = {
        "workflow": workflow,
        "file": str(out.relative_to(ROOT)),
        "bytes": out.stat().st_size,
        "duration_s": round(final_dur, 2),
        "cap_s": max_sec,
        "under_cap": final_dur <= max_sec + 0.35,
        "narration_s": round(sum(durs), 2),
        "phrases": phrases,
        "phrase_durations": [round(d, 2) for d in durs],
        "dropped_phrases": dropped,
        "voice": wf.get("voice", {}).get("name"),
        "cuts": [{k: c[k] for k in ("phrase", "cut", "seconds", "file", "mood", "color_temp",
                                    "subject", "baked_text", "content")} for c in cuts],
        "song": song,
        "mean_db": lv["mean_db"],
        "peak_db": lv["peak_db"],
        "seed": seed,
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_root / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)

    print(f"  [{workflow}] wrote {out.relative_to(ROOT)}  {final_dur:.1f}s  "
          f"{out.stat().st_size // 1024} KB  peak {lv['peak_db']} dB")
    print(f"  [{workflow}] song: {song['file'][:40]} @ {song['offset']:.1f}s "
          f"(use #{song['use_number']})")
    return report


def deliver_to_downloads(report: dict, wf: dict) -> Path:
    """Copy a finished video to the owner's Downloads folder under a name that reads on sight.

    The owner asks for the file by looking in Downloads, not by hunting through `out/`. Naming it
    `<channel>_<date>_<seconds>s.mp4` means two files side by side are distinguishable without
    opening either, and the sidecar `.json` travels with it so a report of what went into the video
    is never left behind in the project folder.
    """
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        raise SystemExit(f"Downloads folder not found at {downloads}")
    src = ROOT / report["file"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    channel = wf.get("channel_name", report["workflow"]).lower().replace(" ", "-")
    dst = downloads / f"{channel}_{stamp}_{report['duration_s']:.0f}s.mp4"
    shutil.copy2(src, dst)
    side = src.with_suffix(".json")
    if side.exists():
        shutil.copy2(side, dst.with_suffix(".json"))
    return dst


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Render one vertical short for one X workflow")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--phrases-file", help="JSON from x_script.py; omit to write a fresh script")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out-name")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--deliver", action="store_true",
                    help="also copy the result to the Downloads folder with the report beside it")
    a = ap.parse_args()

    wf = load_workflow(a.workflow)

    if a.phrases_file:
        script = json.loads((ROOT / a.phrases_file).read_text(encoding="utf-8"))
    else:
        import x_script
        script = x_script.write_script(a.workflow)
        x_script.remember_opening(a.workflow, script["opening"])

    phrases = script["lines"]
    print(f"  [{a.workflow}] script: {len(phrases)} lines, {script['words']} words")

    pool = gk.pool_for(a.workflow)
    out_root = ROOT / wf.get("output_dir", f"out/{a.workflow}")
    vo_dir = ROOT / out_root / "voice"
    vo_dir.mkdir(parents=True, exist_ok=True)

    wavs = []
    for i, phrase in enumerate(phrases, 1):
        # Content-addressed cache: the same line in the same voice is the same audio, so a re-render
        # (new clips, new song, new caption size) must not re-spend TTS quota. This matters because
        # TTS quota ran out mid-build on 2026-10-28 after the audition sweeps, and every re-render
        # would otherwise cost 5-6 more calls for identical speech.
        import hashlib
        vcfg = wf.get("voice", {})
        key = hashlib.sha256(
            f"{vcfg.get('model')}|{vcfg.get('name')}|{vcfg.get('cue')}|{phrase}".encode("utf-8")
        ).hexdigest()[:16]
        path = vo_dir / f"{key}.wav"
        if path.exists() and xf.probe_dur(path) > 0.05:
            d = xf.probe_dur(path)
            print(f"    {i}. {d:>5.2f}s  (cached)  {phrase[:52]}")
            wavs.append(path)
            continue
        xv.synthesize_phrase(pool, wf, phrase, path)
        d = xf.probe_dur(path)
        print(f"    {i}. {d:>5.2f}s  {phrase[:60]}")
        if d <= 0.05:
            raise SystemExit(f"phrase {i} rendered silent — refusing to continue")
        wavs.append(path)

    report = render(a.workflow, phrases, wavs, wf, out_name=a.out_name, seed=a.seed,
                    keep_work=a.keep_work)
    print(f"  report: {report['file'].replace('.mp4', '.json')}")
    if a.deliver:
        dst = deliver_to_downloads(report, wf)
        print(f"  delivered: {dst}")
    return 0 if report["under_cap"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
