---
name: x-forchi-bridge
description: 'The two places this project now reaches into ForChi (C:\Users\hp\forchi): the Gemini TTS voice backend added to tools/_video_voice.py so Shorts can use the X channel voice, and the emotion/weather labels merged into media/clips/manifest.json. Load this before touching ForChi from here, when ForChi Shorts fails at the voice stage, when VOICE_BACKEND is questioned, when ForChi clips need re-labelling, or when someone asks why ForChi has a `gemini` voice path at all.'
---

# ForChi, from here

ForChi is a separate repository and a live production system. This project touches it in exactly two
places, and both were owner requests. Nothing else in ForChi should be changed from here without
being asked.

## When to use

- ForChi Shorts fail at the voice stage, or someone asks how its voice is configured.
- ForChi's clip pool needs emotion labels refreshed.
- About to change anything under `C:\Users\hp\forchi`.

## 1. Voice: a `gemini` backend was added

**Why.** The owner preferred the female Gemini voice used on the X channels ("Leda") to ForChi's F5
whisper clone, and asked for Shorts to use it. It also removes the voice stage's dependency on the F5
worker, which is the stage that has wedged for 16+ minutes before and once nearly caused a duplicate
Short.

**What changed, in `tools/_video_voice.py` only:**

| Addition | Purpose |
| :--- | :--- |
| `render_gemini(text, out_dir, mode, seed, max_len)` | same contract as `render_local`: writes `p1.wav`, `p2.wav`, … and returns `[{index, text, wav, mode}]` |
| `GEMINI_TTS_MODELS` | `gemini-3.8-flash-tts` → `3.1-flash-tts-preview` → `2.5-flash-preview-tts`, walked on failure |
| `_gemini_keys()` | `GEMINI_KEYS` (comma or newline separated) plus `GEMINI_PAID_API_KEY` |
| `_pcm_to_wav()` | writes the three formats the models return |
| dispatch in `render_script()` | `if backend == "gemini": return render_gemini(...)` — **before** the F5/Contabo branch, so nothing else is bypassed or removed |

**Settings**, in ForChi's `.env` (**names only** — never print values):

```
VOICE_BACKEND=gemini
VOICE_GEMINI_NAME=Leda
VOICE_GEMINI_CUE=[speaking slowly and softly, almost in a whisper]
```

Revert with `VOICE_BACKEND=` (empty) or `VOICE_BACKEND=contabo` — the F5 path is untouched and is still
the path whenever the variable is not `gemini`.

**Two facts baked into that code, both measured:**

- **A prose style instruction is spoken aloud.** Transcribing the output gave
  *"speak slowly and softly I still remember…"*. A **bracketed** cue is stripped from the speech and
  still changes delivery: ~9 dB quieter, slightly slower. That is the whisper. Do not "improve" the
  cue into a sentence.
- **The three TTS models disagree about the audio format** — `audio/wav`, `audio/l16; rate=24000;
  channels=1`, and `audio/L16;codec=pcm;rate=24000`. The last is bare PCM and needs a 44-byte header
  or every downstream tool reads it as noise. Honour the mime; never assume.

**Verified 2026-11-01:** `render_script("You still remember the way the kitchen light fell across her
face.", …)` through ForChi's own module returned one `p1.wav`, **5.12 s / 251,876 bytes** — 12 words at
2.34 words/s, which is Leda's pace.

## 2. Emotion labels written into ForChi's clip pool

`X/scripts/label_emotions.py --forchi` labels all 211 segments and writes **two** things:

| File | Content |
| :--- | :--- |
| `media/clips/emotion_labels.json` | full labels: `{clips: {file: {emotion, weather}}}` plus counts and taxonomy |
| `media/clips/manifest.json` | each entry **gains** `emotion` and `weather_emo` |

`weather_emo`, not `weather`: ForChi's manifest already has its own vocabulary (`color_temp`, `mood`),
and a bare `weather` would collide with future use. Fields are **added**, never removed — the existing
stitcher keeps working unchanged, and can start filtering on `emotion` when it wants to.

Measured distribution on 2026-11-01:

```text
forchi: calm 84, sad 61, excited 17, fear 15, love 13, happy 13, surprise 5, angry 3
weather: clear 89, indoor 46, none 44, night 23, rain 6, fog 3
```

## Rules

- **Do not commit inside ForChi from an X task.** The changes are left in its working tree and recorded
  in `X/HANDOFF_LOG.md`; its owner reviews and commits.
- **Do not touch ForChi's scheduler, auth or upload paths** — only the two places above.
- **`178.18.245.136` is still forbidden** (AGENTS.md §1). ForChi is a folder on this workstation; that
  host is a different machine.
- Re-run `--forchi` only when the clip pool changes. 211 clips is 27 vision requests.

## Verification

```powershell
cd C:\Users\hp\forchi
python -c "import ast; ast.parse(open(r'tools\_video_voice.py',encoding='utf-8').read()); print('ok')"
python -c "import json;d=json.load(open(r'media\clips\emotion_labels.json',encoding='utf-8'));print(len(d['clips']),'labelled')"
python -c "import json;m=json.load(open(r'media\clips\manifest.json',encoding='utf-8'));print(sum(1 for e in m if e.get('emotion')),'manifest entries carry emotion')"
```

Expect `211 labelled` and `211 manifest entries carry emotion`.
