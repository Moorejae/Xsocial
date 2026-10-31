---
name: x-video-pipeline
description: 'The jephter/emmanuel short-video pipeline end to end: what each script does, how a clip or song is chosen, the caption and watermark setup, the ffmpeg graph, and the failure modes that have already shipped a broken video. Load this when producing a short for either channel, when a render fails or produces silence, when captions are missing or in the wrong font, when the same song or clip keeps recurring, when a script ends mid-sentence, when output exceeds 50 seconds, or before changing anything in scripts/x_*.py.'
---

# The two short-video workflows

One pipeline, two isolated instances. `jephter` and `emmanuel` share code and share nothing else: own key pool,
own voice, own clip folder, own song folder, own output folder, own usage ledger. A failure in one cannot consume
the other's quota because the pools are disjoint.

> **Resilience is part of done.** Retry/backoff lives in `GeminiPool`; `PoolExhausted` and `GeminiError` are raised
> rather than swallowed, so an exhausted pool becomes an alert, never an empty video. See `build-fallbacks`.

## When to use

- Making a video: `python scripts\x_render.py --workflow jephter`.
- A render produced no audio, no captions, the wrong font, or the wrong length.
- The same song, or the same clips, keep coming back.
- A script ends mid-sentence, or reads as a fragment.
- Changing anything under `scripts/x_*.py` or `config/workflows.json`.

## The stages

| # | Stage | Code | Output |
| :--- | :--- | :--- | :--- |
| 0 | **Label the libraries** | `x_media.py --label <wf>` | `config/scenes/<wf>.json`, `config/songs/<wf>.json` (committed) |
| 0b | **Vision labels** (optional, once) | `x_media.py --vision <wf>` | adds `subject`, `baked_text`, verified `mood` |
| 0c | **Letterbox rects** (once, or after adding clips) | `x_media.py --bars <wf>` | adds `content` / `content_coverage` |
| 1 | **Script** | `x_script.py` / `x_render.py` | 5-6 romance lines, ≤92 words |
| 2 | **Voice** | `x_voice.py` | one WAV per line, content-addressed cache |
| 3 | **Cut plan** | `x_render.pick_cuts` | one clip per cut, ~2.6-4.8 s each |
| 4 | **Song window** | `x_render.pick_song` | a track + a *different* offset each time |
| 5 | **Assemble** | `x_render.render` | 1080×1920, H.264, captions burned, ≤50 s |

One command does 1-5:

```powershell
python scripts\x_render.py --workflow jephter --seed 51
python scripts\x_render.py --workflow jephter --phrases-file out/jephter/script.json   # reuse a script
```

Every render writes a sidecar `<name>.json`: phrases, per-line durations, every clip with its mood/temperature/
subject, the song and its offset, levels, and `under_cap`. **Read the sidecar, not just the mp4.**

## Config that drives it

`config/workflows.json`, per workflow:

| Key | jephter | emmanuel | Meaning |
| :--- | :--- | :--- | :--- |
| `media_dir` | `jephtah` | `emmanuel` | the folder the owner already used (the directory is spelled `jephtah`) |
| `songs_dir` | `jephtah/songs` | `emmanuel/songs` | |
| `voice.name` | `Leda` | `Pulcherrima` | measured, see `x-voice-personas` |
| `video.max_seconds` | 50 | 50 | hard cap; phrases are dropped to obey it |
| `video.cut_seconds` | 2.6-4.8 | 2.6-4.8 | target length of one shot |
| `caption.size / words_per_caption` | 82 / 3 | 82 / 3 | Jost Bold |
| `music.gain` | 0.20 | 0.20 | bed level under the narration |

## How a clip is chosen

Tiered, and the tiers are the point:

1. **people, no burned-in text** (`subject` in `couple`/`single`, `baked_text` false)
2. any clip without burned-in text
3. people, even with text
4. anything

Within a tier: fresh (not used in this video, not in the last 30) **and** mood-matched **and** colour-temperature
matched; then fresh and temperature-matched; then fresh; then merely unused. Temperature **oscillates** —
consecutive cuts alternate warm/cool so the video breathes instead of sitting in one grade.

`baked_text` is a near-veto, not a preference: a stock clip's own caption fighting ours makes both unreadable.
Measured 2026-10-28: 8 of 54 jephter clips and 1 of 43 emmanuel clips carry burned-in text.

## How a song is chosen (and why no two videos sound alike)

The owner's requirement: *"the songs can be cut depending on the script so one in every video won't have the same
sound."* So a song is **labelled** (duration, mean/peak level, energy) and **edited** to fit, but the excerpt is
taken from a different place every time:

- track choice prefers the least-used song, excluding the last 3;
- offset = `frac(use_number × 0.618…) × (song_length − video_length − 1.5)`, retried until it is at least ~15 % of
  the video length away from any of the last 6 offsets for that track;
- the excerpt is faded in/out and gain-staged to sit under the voice;
- the counter lives in `out/<workflow>/usage.json`, so the promise holds **across runs**, not just within one.

Golden-ratio steps rather than a linear cursor because a linear cursor creeps forward and adjacent videos end up
sharing a phrase of the melody — which is exactly what "the same sound" means to a listener.

## Captions and watermark

- `.ass` file, `PlayResX/Y` 1080×1920, `Alignment=2` (bottom centre), `MarginV` from config.
- **Jost Bold** — REZMO's own face, instantiated at wght 700 by `scripts/make_fonts.py` from
  `REZMO-FRONTEND-DESIGN-SPEC/public/fonts/jost.woff2`. A variable font handed straight to libass gives the default
  instance (body weight with a synthetic smear), which is why the static bold is generated and committed.
- White fill, 4 px outline, 2 px shadow. Three words per chunk by default (`words_per_caption`); set it to 1 for
  ForChi's one-word-at-a-time look.
- A **scrim** (`assets/scrim_1080x1920.png`, generated by `make_assets.py`) is composited *under* the captions: a
  quadratic black ramp from 55 % of frame height to the bottom. It fixes two measured problems at once — white
  captions vanishing over bright snow/sky, and stock clips whose own captions sit exactly where ours go.
- Watermark top-centre in Cormorant Garamond SemiBold at 55 % opacity.

Fonts are copied into the work dir and referenced relatively, and ffmpeg runs with `cwd=work`, because a Windows
drive-letter colon inside a filter argument is a parse error.

## The ffmpeg graph

```
[0:v]  vcat (hard cuts, already 1080x1920@30, yuv420p)
[3:v]  scrim png (looped still)
       -> overlay -> subtitles=subs.ass:fontsdir=. -> drawtext (watermark) -> [v]
[1:a]  narration concat (phrase wavs + 0.34 s gaps + 1.5 s tail) -> volume=<gain>dB,alimiter=limit=0.95
[2:a]  song excerpt -> afade in/out, volume=<music.gain>
       amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95 -> [a]
out:   libx264 crf 19, aac 192k 44.1k stereo, -t <total> -movflags +faststart
```

**Never `loudnorm`.** It dropped ~3 s of audio on ffmpeg 7.1 in this pipeline's predecessor; peak gain + `alimiter`
is the replacement and is what runs here.

## Failure modes already seen

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| Black bars top and bottom inside the picture | the **source clip** is letterboxed, so the bars survive `scale`+`crop` | `x_media.py --bars <wf>` records the content rect via `cropdetect`; the renderer crops to it before scaling. 6 of 97 clips were affected |
| A caption reads *"and gather the"* | fixed 3-word chunks strand a function word | `chunk_words` takes one more word when a chunk would end on `the`/`and`/`of` |
| A "letterboxed" clip still shows bars | its `content` rect was never measured (added after labelling) | run `--bars <wf>`; it backfills without re-measuring everything |
| Final line is a fragment: *"The radio plays"* / *"You just stand together in."* | the model truncated, and the first version of the guard **appended a full stop to the truncation** | `complete_ending` + `repair_ending` in `x_script.py`: a short or dangling tail is **dropped**, the previous sentence becomes the close; refuses to publish if no complete ending survives 3 attempts |
| Captions invisible on bright shots | white text over snow/sky | the scrim; keep it composited *under* the subtitles |
| Two captions fighting | a stock clip with burned-in text | `--vision` sets `baked_text`; the picker avoids those clips |
| Every song sounds the same | one offset, or a linear cursor | golden-ratio offsets + the ledger |
| Whole song pool reads `energy: unknown` | `volumedetect` reports at INFO level and `-v error` suppresses it | `xf.levels()` never passes `-v error` |
| `UnicodeEncodeError` mid-sweep | clip filenames are emoji; cp1252 console | `xf.utf8_stdout()` in every entry point |
| Render stops with `PoolExhausted` | TTS/script quota genuinely spent | it is *supposed* to be loud. See `x-voice-personas` |
| Video shorter than the narration | a phrase dropped but the audio kept | the renderer drops the line from **both** lists together (`phrases`, `wavs`, `durs`) |

## Verification

```powershell
$j = Get-Content (Get-ChildItem out\jephter\*.json | Sort LastWriteTime -Desc | Select -First 1) -Raw | ConvertFrom-Json
$j.duration_s, $j.under_cap, $j.cuts.Count, $j.song.offset

ffmpeg -y -ss 9 -i <output>.mp4 -frames:v 1 out\check\a.jpg      # then open the frame
```

Check three things code cannot: the caption is legible over the actual footage, the first and last frames are
sensible, and the audio is audible across the whole file (not just at the start).

## Still open

- Nothing schedules these: no systemd unit, watchdog, email escalation or external heartbeat yet — §8 of
  [AGENTS.md](../../AGENTS.md).
- The uploader is not wired to this pipeline (`youtube_auth.py` gets the credential; nothing calls `videos.insert`).
- Voice is Gemini TTS. A true clone (F5-TTS, as ForChi uses) needs a reference clip per workflow; `x_voice.py` is
  the seam to swap it in without touching the renderer.
