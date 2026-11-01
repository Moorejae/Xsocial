---
name: x-voice-personas
description: 'How the two narrator voices were chosen and how to steer Gemini TTS at all: the bracketed-cue finding (a prose style prompt is read aloud and silently lengthens every video), the audition method and the measured numbers for ten candidate voices, which voice each workflow uses, the TTS model ladder and its format quirks, and the TTS quota that ran out mid-build. Load this when a video sounds wrong, too fast, too loud or not whispery, when picking or changing a voice, when changing the two workflows must not sound alike, when TTS returns an odd mime type or raw PCM, or when TTS starts returning 429.'
---

# The two narrator voices

Two channels, two voices, and they must not sound like the same person. That is a config fact
(`config/workflows.json` → `voice.name`) plus a measured choice, not an opinion.

> **Resilience is part of done.** TTS quota is the tightest limit in the whole pipeline and it *will* run out
> mid-build. `GeminiPool.generate_audio` raises `PoolExhausted` with the last model and HTTP status so it escalates
> instead of producing a silent video. See `build-fallbacks`.

## When to use

- A video sounds too fast, too loud, too flat, or nothing like a whisper.
- The owner likes one channel's voice and not the other's.
- Choosing or changing a voice, or making sure the two channels differ.
- TTS returns `audio/l16…`, `audio/L16;codec=pcm…` instead of a WAV.
- TTS starts answering `429` across every key.

## The finding that matters most: the cue must be bracketed

Gemini TTS speaks **the whole prompt**. Transcribing the output back with an audio-capable model (2026-10-27):

| prompt shape | what came back | level | verdict |
| :--- | :--- | :--- | :--- |
| `"<style sentence>. <line>"` | *"speak slowly and softly I still remember…"* — **the style was spoken** | -19 dB | wrong: adds ~10 s to every video |
| `Say slowly: "<line>"` | the line only | -23.6 dB | works, quoted form |
| `[speaking slowly and softly, almost in a whisper] <line>` | **the line only** | **-28.4 dB** | **use this** |
| `[whispering] <line>` | the line only | — | works, but came back *faster* (3.2 s vs 6.4 s) — the cue word matters |

So: a bracketed direction is stripped from the speech, and still changes delivery. The measured effect of the slow
cue is **~9.4 dB quieter and slightly slower**, which is what a whisper physically is. Prepending style as prose is
read aloud, silently lengthening every video — a bug that renders fine and only shows up as "why is this 62 s when
I wrote 70 words?".

The cue is `config/workflows.json` → `voice.cue`, and it is identical for both workflows; **only the voice name
differs**. A style sentence longer than a cue is not worth the risk.

## The audition, and the numbers it produced

`python scripts\x_voice.py --workflow jephter --audition` renders one 17-word line in ten voices and reports
duration, words/second, mean level, and the fraction of the file that is near-silence (breath).

To **match an existing voice** — the usual request, *"make emmanuel sound like jephter"* — audition the candidates on
*that workflow's own line* with the preferred voice included as the reference, then score by distance from it:

```python
d = abs(wps - ref_wps) * 3 + abs(quiet - ref_quiet) * 12 + abs(mean_db - ref_mean) * 0.08
```

| voice | duration | w/s | mean dB | quiet ratio | note |
| :--- | ---: | ---: | ---: | ---: | :--- |
| **Leda** | 6.76 s | 2.66 | **-29.5** | 0.196 | **both channels** (owner's choice, 2026-11-01) and **ForChi Shorts** |
| Pulcherrima | 5.08 s | 2.95 | -28.9 | 0.231 | was emmanuel; rejected by ear — the fastest read of the field |
| Umbriel | 7.17 s | 2.09 | -26.7 | 0.156 | closest distinct alternative to Leda (gap 0.002); the fallback if the channels ever diverge |
| Aoede | 8.12 s | 2.22 | -26.8 | 0.192 | |
| Erinome | 8.16 s | 2.21 | -27.0 | 0.271 | breathy, louder than Pulcherrima |
| Achernar | 6.96 s | 2.59 | -26.1 | 0.216 | |
| Vindemiatrix | 8.05 s | 2.24 | -24.6 | 0.136 | fewest pauses — reads as flattest |
| Callirrhoe | 6.60 s | 2.73 | -23.9 | 0.205 | audibly more projected |
| Laomedeia | 7.76 s | 2.32 | -23.4 | 0.185 | |
| Sulafat | 8.04 s | 2.24 | -20.7 | 0.170 | **loudest** — not a whisper |
| Despina | 12.08 s | **1.49** | -23.0 | 0.220 | too slow: an 85-word script would be ~57 s and break the 50 s cap |

### The lesson the owner's ear taught (2026-11-01)

The first pairing was chosen by "quietest two, with different breath profiles". The owner liked jephter and
**did not like emmanuel**: *"the voice for emmanuel isn't, I would like it to sound more like that of jephter."*

So the goal is not "two different quiet voices", it is **one voice character, two timbres** — the same steadiness and
closeness, a different person. Re-audition against the owner's preferred voice as the reference and score by
**distance from it**:

| voice | vs Leda: pace gap | quiet-ratio gap | verdict |
| :--- | ---: | ---: | :--- |
| **Umbriel** | 0.14 w/s | **0.002** | **chosen** — same character, different timbre |
| Vindemiatrix | 0.16 | 0.024 | close second |
| Achernar | 0.14 | 0.043 | |
| Pulcherrima (old) | **0.72** | **0.073** | what sounded wrong |

The measurable signal for "sounds wrong" was the **pace**: Pulcherrima read 13 words in 5.08 s — the fastest of every
candidate — and that is what the ear rejected. When a voice is disliked, check its words/second before its level.

The pair must stay distinguishable, so do not simply use Leda for both — except that on 2026-11-01 the
owner asked for exactly that: *"replace emmanuel's voice with the current forchi's voice"*, after asking
that ForChi Shorts use "the voice you are currently using for jephtah's video". So **Leda is now the
voice of jephter, emmanuel and ForChi Shorts**. That is a deliberate reversal of the earlier
"voices must differ" rule and it is the owner's call; it is recorded here rather than quietly dropped.
Umbriel is the closest distinct alternative (pace 2.09 w/s, pause-profile gap 0.002 from Leda) and is a
one-word change in `config/workflows.json`.

Re-audition after any TTS model change: `out/audition/<workflow>/audition.json` holds the numbers, and
`.wav` files sit beside it for a listen.

## TTS backends and their quirks

| backend | model ladder | notes |
| :--- | :--- | :--- |
| `gemini` (default) | `gemini-3.8-flash-tts` → `3.1-flash-tts-preview` → `2.5-flash-preview-tts` | no GPU, no reference clip, works with the existing key pools |
| `f5` (planned) | local / HF Space | a real clone needs a reference clip per workflow. The interface is `synthesize_phrase`, so swapping it does not touch the renderer |

**The mime type must be honoured, not assumed.** Same call, three formats:
`audio/wav` (a container), `audio/l16; rate=24000; channels=1` and `audio/L16;codec=pcm;rate=24000` (bare PCM that
needs a 44-byte header or every downstream tool reads it as noise). `x_voice.pcm_to_wav` handles all three.

## Rendering one line vs a script

Phrases are rendered **one at a time**, like ForChi's `p1.wav`, `p2.wav`:

- exact per-line durations, so captions can be timed and cuts placed on phrase boundaries;
- a line that runs long can be dropped without re-rendering the take;
- a failure costs one line, not the video.

Renders are **content-addressed**: the cache key is `sha256(model|voice|cue|text)[:16]`, so re-rendering with new
clips, a new song or a larger caption costs **zero** TTS calls for identical speech. This was added after quota ran
out mid-build on 2026-10-28 and every retry would otherwise have re-spent 5-6 calls on the same words.

## TTS quota is the binding constraint

Measured 2026-10-28: after ~30 TTS calls across the audition and two builds, **every key in the jephter pool answered
429** on all three TTS models, and the build stopped with `PoolExhausted`. Consequences worth planning around:

- an audition sweep costs 10 calls per workflow — do it once, and keep `audition.json`;
- a 6-line video costs 6 calls when the cache is cold, 0 when warm;
- budget video production against TTS quota, not against text quota. Text generation is comparatively free.
- the pool rotates keys and puts a 429'd key on a cooldown with a growing multiplier, so a genuine exhaustion
  surfaces as a loud failure rather than a degraded render.

## Verification

```powershell
python scripts\x_voice.py --workflow emmanuel --say "I still remember that night." --out out/te.wav
ffprobe -v error -show_entries format=duration -of csv=p=0 out/te.wav     # expect 2-6 s, not 10+
```

A line that comes back much longer than its word count suggests is the style-prompt bug: check `voice.cue` is
bracketed and that nothing is prepended as a sentence.

The two workflows must not share a voice:

```powershell
python -c "import json;d=json.load(open('config/workflows.json',encoding='utf-8'));print({k:v['voice']['name'] for k,v in d.items() if not k.startswith('_')})"
```
