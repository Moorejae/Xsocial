---
name: x-forchi-bridge
description: 'The three places this project reaches into ForChi (C:\Users\hp\forchi): the Gemini TTS voice backend in tools/_video_voice.py, the two X writing formats (confession and devotion) added to tools/_video_script.py and rotated per Short, and the emotion/weather labels merged into media/clips/manifest.json. Load this before touching ForChi from here, when ForChi Shorts fail at the script or voice stage, when captions do not match the audio, when VOICE_BACKEND is questioned, or when asked for a ForChi video proof.'
---

# ForChi, from here

ForChi is a separate repository and a live production system. This project touches it in exactly three
places, and each was an owner request. Nothing else in ForChi should be changed from here without
being asked.

## When to use

- ForChi Shorts fail at the script or voice stage, or someone asks how either is configured.
- A ForChi video's captions do not match what is spoken.
- A video proof of ForChi is wanted (`X/scripts/forchi_proof.py --format both`).
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

## 2. The two X writing formats are now ForChi's Shorts formats

`tools/_video_script.py` gained `X_FORMATS = {"confession", "devotion"}` — the jephter and emmanuel writing
formats — and `next_x_format(history)` **alternates** them, so consecutive Shorts do not share a shape.

```python
script = vs.generate_script_checked()                    # rotates confession -> devotion -> confession
script = vs.generate_script_checked(style="devotion")    # or force one
script = vs.generate_script_checked(style="victor")      # personality persona still available by name
```

`generate_script()` now selects the prompt with `X_FORMATS.get(style, PERSONA_PROMPT)`, and the "expand this to
length" round no longer tells an X-format script to follow the Victor aphorism→subversion→anchor formula.

| Format | Voice | Shape |
| :--- | :--- | :--- |
| `confession` | first person "I" to an absent "you" | claim → "But some nights…" → turn → quiet want |
| `devotion` | imperative, "Love them…" | 6-12 commands, ≤2 "because" clauses → a named standard |

Both ban scene description — no hands, rooms, weather or objects — because that is what the owner's own reference
scripts do, and what he called "nice".

## 3. Producing a proof, and three traps in that path

`X/scripts/forchi_proof.py --format both` runs ForChi's whole chain (script → voice → clips → assemble) and delivers
to Downloads. Three failures cost real time on 2026-11-01 and are handled in that script:

| Trap | Symptom | Cause |
| :--- | :--- | :--- |
| **Relative wav paths** | `Error opening input file …assemble_build\narr.wav`, rc 4294967294, no output file | the concat demuxer resolves relative paths against the **list file's** directory. Pass `os.path.abspath()` for every narration wav and clip path |
| **The voice cache is keyed by filename** | the delivered video's caption said *"griefs"*, a word from neither script — audio from the previous run, captions from the new one | `render_script` skips any `pN.wav` that exists. Use a **content-addressed work dir** (`proof_<format>_<sha1(text)[:8]>`), or delete the dir when the script changes |
| **`HTTP 402` mid-render** | `gemini-3.8-flash-tts HTTP 402 -> next model` three times, then failure with 6 of 7 phrases done | ForChi's key pool has limited TTS quota and the first version tried only three keys per phrase. It now walks **every key** before dropping to the next model, and a 402 is treated like a 429 (key cooled for 300 s). The cache makes the retry cheap |

Verified proofs, 2026-11-01:

```text
forchi_confession_20261101-1014_38s.mp4   37.5 s  1080x1920  h264/aac  black=0
forchi_devotion_20261101-1016_48s.mp4     48.1 s  1080x1920  h264/aac  black=0
```

## 4. Emotion labels written into ForChi's clip pool

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
