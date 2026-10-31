---
name: x-test-video
description: 'How to produce a finished short and hand it to the owner: the exact command, the delivery convention (a copy in C:\Users\hp\Downloads named <channel>_<date>_<seconds>s.mp4 with its report beside it), rendering both channels side by side so the two voices can be compared, and the checks to run before saying it is done. Load this whenever the owner asks for a video, a test video, a sample, a clip, "something to look at", or a re-render after a script, voice, caption or clip change — do not make them ask twice for the same thing.'
---

# Delivering a video

The owner should never have to say "put it in my Downloads folder" again. That is the convention, and it is built
into the renderer as `--deliver` so it cannot be forgotten.

## When to use

- "give me a video", "a test video", "a sample", "something I can watch".
- After changing anything that affects output: script, voice, caption size/font, cut length, song choice.
- Before any upload, as the thing a human actually looks at.

## The command

```powershell
cd C:\Users\hp\Downloads\X
python scripts\x_render.py --workflow jephter  --deliver
python scripts\x_render.py --workflow emmanuel --deliver
```

**Render both, always.** The two channels exist to sound different; one video cannot show that. Two files side by
side in Downloads is the whole point.

`--deliver` copies the result to `C:\Users\hp\Downloads\<channel>_<YYYYMMDD-HHMM>_<seconds>s.mp4` and puts the
sidecar `.json` report next to it, so the "what went into this video" answer travels with the file instead of being
left behind in `out/`.

Other useful flags:

| Flag | Use |
| :--- | :--- |
| `--seed 81` | reproduce a specific cut/clip/song arrangement |
| `--phrases-file out/<wf>/scripts/reuse.json` | re-render an **existing** script — with a warm voice cache this costs **0 TTS calls** |
| `--keep-work` | keep the intermediate segment files for debugging |

Re-rendering after a caption or clip change does **not** need new speech. Extract the current script once, then reuse
it:

```powershell
$j = Get-Content (Get-ChildItem out\jephter\*.json | Sort LastWriteTime -Desc | Select -First 1) -Raw | ConvertFrom-Json
[ordered]@{ workflow='jephter'; lines=@($j.phrases) } | ConvertTo-Json -Depth 4 | Set-Content out\jephter\scripts\reuse.json
python scripts\x_render.py --workflow jephter --phrases-file out\jephter\scripts\reuse.json --deliver
```

## What to report back

Four facts and one picture. Anything less is a guess:

| Fact | Where |
| :--- | :--- |
| length vs the 50 s cap | sidecar `duration_s`, `under_cap` |
| voice and song, with the song's start offset | sidecar `voice`, `song.file`, `song.offset` |
| how many cuts, and how many landed on couple footage | sidecar `cuts` (`subject`) |
| no clip with burned-in text, and no letterboxed clip left uncropped | sidecar `cuts` (`baked_text`, `content`) |

Then **look at a frame** — the owner will. A file that exists is not a file that is right:

```powershell
ffmpeg -y -ss 8 -i C:\Users\hp\Downloads\<file>.mp4 -frames:v 1 -q:v 2 out\check\a.jpg
```

## Checks that have caught real defects

Each of these was found by looking, not by rendering successfully:

| Check | What it caught |
| :--- | :--- |
| a frame from a **bright** scene | white captions vanishing over snow — fixed by the scrim |
| a frame from **mid-video**, not the first | a letterboxed source keeping its black bars (6 of 97 clips) — fixed by `cropdetect` + crop |
| the caption text itself | *"and gather the"* — a chunk ending on a function word — fixed by `chunk_words` |
| the **last** caption | a narration ending mid-sentence (*"You just stand together in."*) — fixed by the ending guard |
| audio levels | `peak_db` near -1 dB with `mean_db` around -25 dB is a healthy whisper over a bed |

## Pitfalls

| Situation | Do this |
| :--- | :--- |
| `PoolExhausted` on TTS | quota, not a bug. Reuse an existing phrases file (0 calls), wait for the daily reset, or see `x-voice-personas` |
| The owner wants a different song feel | change `music.gain` and re-render with the same phrases file — no speech cost |
| A clip looks wrong (car, city, cartoon) | it is the owner's library; relabel with `x_media.py --vision <wf>` rather than editing the picker |
| Output over 50 s | the renderer already drops phrases from the middle; if it still refuses, the voice pace is wrong for the workflow, not the cap |
| Owner asks for a **new** script | omit `--phrases-file`; that spends TTS calls (one per line) |

## The deliverable, in one line

`out/<workflow>/<name>.mp4` is the build; `C:\Users\hp\Downloads\<channel>_<stamp>_<s>s.mp4` is the deliverable.
Always produce the second, and always look at it first.
