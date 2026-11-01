# X — handoff log

Newest first. Every change gets an entry, with evidence. See [AGENTS.md](AGENTS.md) §6 for the shape.

---

## 2026-11-01 (ForChi Shorts now written in the two X formats; two video proofs delivered)

**Plane(s):** this folder, and `C:\Users\hp\forchi` (its working tree, **nothing committed there**).

**Asked:** "i need to use both writing format used in jephtah and emmanuel to be the writing format for forchi's
short videos... i also need a video proof."

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| ForChi `tools/_video_script.py` | `X_FORMATS` = `confession` + `devotion`, `SHARED_RULES`, `next_x_format(history)`; `generate_script()` selects `X_FORMATS.get(style, PERSONA_PROMPT)`; `generate_script_checked()` **rotates** when no style is given; the expand-to-length round no longer tells an X-format script to use the Victor aphorism formula | Shorts alternate between jephter's confessional shape and emmanuel's instructional one. The Victor persona is still reachable as `style="victor"` |
| ForChi `tools/_video_voice.py` | `render_gemini()` now walks **every key** before dropping to the next model, and treats `HTTP 402` like a 429 (key cooled 300 s) | a render died at phrase 7 of 7 with `HTTP 402 -> next model` three times while 11 keys sat unused |
| `scripts/forchi_proof.py` | **new.** Runs ForChi's chain end to end (script → voice → clips → assemble), verifies, delivers to Downloads, writes `out/forchi_proof.json` | the owner asked for a proof, and this makes it repeatable |
| `skills/x-forchi-bridge` | describes the third touchpoint and the three traps below | |

### Delivered proofs

```text
C:\Users\hp\Downloads\forchi_confession_20261101-1014_38s.mp4   37.5 s  1080x1920  h264/aac  black=0
C:\Users\hp\Downloads\forchi_devotion_20261101-1016_48s.mp4     48.1 s  1080x1920  h264/aac  black=0
```

Written by ForChi's own writer, voiced by its own voice module (Leda via the new `gemini` backend), cut by its own
stitcher from its own 211-clip pool, assembled by its own assembler — with its own Ink Free captions and
"Victor Moore" watermark. Only the writing format changed.

Confession sample: *"I told myself I was completely done letting anyone new ever in. / But some nights, your voice
stays lingering long after the call finally ends. / … Until one day, the emotional walls just stopped working. / And
I fully realised I was already completely yours. / I just want to stay close."*

Devotion sample: *"Put your phone down when they speak. Look at their face. Listen past the words to what they are
actually carrying. / … Stay soft when the argument turns sharp because defense is just fear in disguise. / Choose
them again when the novelty fades and the work begins. / Love is the evidence of courage."*

### Three traps found while producing the proof

1. **Relative wav paths.** `Error opening input file …assemble_build\narr.wav`, rc 4294967294, no output. The concat
   demuxer resolves relative paths against the **list file's** directory. Every narration wav and clip path is now
   `os.path.abspath()`. This is already in the shared `video-pipeline` skill; it caught me anyway.
2. **The voice cache is keyed by filename, not content.** A delivered proof was captioned *"griefs"* — a word in
   neither script — because `render_script` reused `pN.wav` from the previous run while the captions came from the new
   text. Fixed with a content-addressed work dir: `proof_<format>_<sha1(text)[:8]>`.
3. **`HTTP 402` on ForChi's TTS keys** mid-render. Its pool has tighter TTS quota than X's; the fix above is to walk
   the whole pool rather than three keys.

**Verified:** both files `1080x1920`, `h264` + `aac`, `blackdetect` clean, durations under ForChi's own 58 s cap, and
a frame from each was opened — the devotion frame's caption reads *"say"*, which is in the delivered script, so audio
and text now agree.

**Left open:** ForChi's changes are **uncommitted** for its owner to review. Shorts still are not scheduled by me;
ForChi's own scheduler is untouched.

---

## 2026-11-01 (whisper removed from emmanuel by changing the cue; scripts rewritten to the owner's own examples)

**Plane(s):** this folder only. No server change, no OAuth work, keys not rotated (owner's instruction).

**Asked:** "the emmanuel voice is still whispery... remove the whisper. and again the script isnt nice" — with two
reference scripts supplied as the standard: jephter as a first-person confession (*"I thought I was fine without
you... But some nights... Some mornings... until one day I stopped fighting it"*), emmanuel as direct imperative
advice (*"Love them in the quiet ways. Put your phone down when they talk... that's the real measure of how much you
love them"*).

**The insight that mattered:** the voice was never wrong — **the cue was**. Swapping voices to fix delivery throws
away the timbre the owner liked; changing one bracketed phrase keeps it.

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `config/workflows.json` | emmanuel `voice.cue` → `[speaking warmly and softly, in a calm clear voice]`; jephter keeps the whisper cue | see the measurement below |
| `config/workflows.json` | `video.phrases` 6 → **8**, `target_words` 85, `max_words` 95 (both) | the reference scripts are 74-80 words; 6 lines could not carry the shape |
| `scripts/x_script.py` | `CHANNEL_BRIEF`: jephter confesses, emmanuel instructs, with the exact shape of each. Shared rules now say **"do not describe a physical scene"** | the old brief asked for "a kitchen light, a hand on a back" — which is what the owner called not nice |
| `scripts/x_script.py` | `truncated()` with **STRICT** and **LOOSE** word sets; every line is checked, not just the ending | a generated line ended *"…because real devotion requires no."* |
| `scripts/x_render.py` | `fresh_emotion()` deprioritises `motion: high` | a still frame from a fast pan is motion blur |

### Measured: whisper vs clear, same voice

```text
jephter  cue "[speaking slowly and softly, almost in a whisper]"   narration mean -22.8 dB
emmanuel cue "[speaking warmly and softly, in a calm clear voice]"  narration mean -17.6 dB
```

A **5.2 dB** difference in the finished narration, same voice, same script rules. On a 14-word test line the clear
cue read at -15.1 dB / 0.140 quiet ratio against the whisper cue's -16.8 dB / 0.208 — louder and with far less
breath.

### The scripts, after the rewrite

```text
jephter  (89 words, 8 lines)
 1. I was convinced that needing no one was my greatest strength.
 2. But some nights, the quiet kindness you offered made that strength feel like a punishment.
 3. Some mornings, I caught myself waiting for you before I could even begin.
 4. You never rushed my fear or demanded that I prove myself.
 5. Until one day I noticed how easy it had become to stop defending myself.
 6. And I realized that being loved by you was never a trap.
 7. I do not want this safe distance anymore.
 8. I want to need you.

emmanuel (87 words, 8 lines)
 1. Listen to their whole story without interrupting to fix it.
 2. Put your pride down the moment an argument starts.
 3. Forgive them quickly because holding a grudge only builds a wall between your hearts.
 4. Speak up for them when they are not in the room.
 5. Choose them on the quiet ordinary days, not just during the highlights.
 6. Give them your undivided eyes because partial presence is just polite distance.
 7. Stay steady when their world becomes loud and uncertain.
 8. That is the measure of a devotion that never breaks.
```

Both follow the supplied examples' structure: the claim → contradiction → turn → quiet want for jephter; commands →
two "because" clauses → a named standard for emmanuel.

**Verified:**

```text
jephter_20261101-0923_48s.mp4   47.9 s  under_cap=True   14 cuts  emotion 14/14  weather 14/14  black none
emmanuel_20261101-0924_42s.mp4  42.4 s  under_cap=True   11 cuts  emotion 11/11  weather 11/11  black none
motion after the preference: jephter low 5 / mid 6 / high 3;  emmanuel low 8 / mid 2 / high 1
```

Frames were extracted and looked at: emmanuel is a couple holding hands under night flowers — the right picture for
*"Give them your undivided…"*. jephter's 20 s frame is still soft-focus; 3 of 14 cuts remain high-motion because a
54-clip library cannot always supply a calm clip that matches the line's emotion. That is a library limit, not a
picker bug — more calm footage would fix it.

### Dead ends — do not repeat

| Attempted | Result |
| :--- | :--- |
| putting auxiliaries in the dangling-word list | rejected `It never did.` and `That is.` — auxiliaries are main verbs at the end of short lines |
| a dangling list with no ellipsis words | let `…requires no.` through |
| the same list including `of`/`to` strictly | threw away `…before I remembered not to.` and `…they are not part of.`, dropping a script from 89 words to 49 |
| asking for "8-18 words per line" | three failed attempts and a 49-word script; replaced with "each one complete thought" |

**Left open:** the pipeline is not scheduled or uploading. The owner intends to add both channels' emails to the
existing ForChi OAuth scope — worth noting that this puts three channels' uploads into one project's
`videos.insert` bucket, which is **100 per day shared**, not 100 each.

---

## 2026-11-01 (emotion labels for all three libraries; ForChi Shorts on the Leda voice; per-channel script briefs)

**Plane(s):** this folder, and `C:\Users\hp\forchi` (its working tree — **nothing committed there**, its owner
reviews). No server change, no OAuth work, keys not rotated (owner's instruction).

**Asked:** re-label the clips with `weather, happy, sad, excited, angry, calm, love, fear, surprise` and produce a
table of clip name → label; do the same for ForChi's Shorts clips; give ForChi Shorts the voice currently used on
jephter ("I love the female voice"); then replace emmanuel's voice with ForChi's current voice; and set the script
focus — jephter: how being loved and loving someone feels, expressive and mindful; emmanuel: how to love
intentionally, unconditionally and unapologetically.

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `scripts/label_emotions.py` | **new.** Vision pass assigning `emotion` (8 values) and `weather` (8 values) per clip; `--workflow` for X, `--forchi` for ForChi, `--table` prints and writes the table | the measured labels said what a shot *looks* like, not what it *feels* like — and a script poses a feeling |
| `config/scenes/<wf>.json` | every clip gains `emotion` + `weather`; file gains an `emotions` summary block | |
| `config/scenes/<wf>-labels.md` | the table the owner asked for, one row per clip | |
| `scripts/x_render.py` | `emotion_for()` / `weather_for()` map a line to labels; the picker now searches the **whole library** for the line's emotion, then weather, then tier, then temperature | see "two bugs" below |
| `config/workflows.json` | new `script` block per workflow (theme / focus / avoid); emmanuel `voice.name` → **Leda** | the owner's briefs, kept in config so they can be tuned without code |
| `scripts/x_script.py` | `system_for(wf)` appends the channel's brief to the shared rules | one writer, two intents |
| ForChi `tools/_video_voice.py` | **new `render_gemini()`** + a `VOICE_BACKEND=gemini` dispatch before the F5 branch; model ladder, key rotation, the three audio formats handled | owner request; also removes the voice leg's dependency on the F5 worker |
| ForChi `.env` | `VOICE_BACKEND=gemini`, `VOICE_GEMINI_NAME=Leda` (**names only** here) | |
| ForChi `media/clips/emotion_labels.json` + `manifest.json` | 211 clips labelled; each manifest entry gains `emotion` and `weather_emo` (not `weather` — that name is free for ForChi's own use) | fields **added**, nothing removed, so ForChi's stitcher is unaffected |
| `skills/x-forchi-bridge` | **new skill** for the two ForChi touchpoints | |
| `skills/x-video-pipeline`, `skills/x-voice-personas`, `AGENTS.md` | labelling step, picker order, the Leda-for-all reversal, the script briefs | |

### Label distributions

```text
jephter  love 22, calm 10, happy 9, sad 4, excited 4, angry 2, fear 2, surprise 1
         weather: indoor 21, clear 21, night 5, none 4, fog 2, snow 1
emmanuel calm 6, love 17, excited 10, sad 4, happy 3, fear 1, surprise 1, angry 1
         weather: clear 23, night 8, indoor 6, rain 3, none 3
forchi   calm 84, sad 61, excited 17, fear 15, love 13, happy 13, surprise 5, angry 3
         weather: clear 89, indoor 46, none 44, night 23, rain 6, fog 3
```

Tables: `config/scenes/jephter-labels.md`, `config/scenes/emmanuel-labels.md`,
`out/forchi_emotion_table.md` (211 rows).

### Two bugs the emotion labels exposed

1. **`take()` always returns something, so tier 1 won before emotion was consulted.** Asking tier by tier for an
   emotion match meant tier 1 answered with a non-matching clip and tier 2 never ran. Fixed by searching every tier
   for the line's emotion first. Measured: **4/10 and 3/11 → 10/10 and 11/11**.
2. **Weather was not consulted at all.** A line about *"the cool night air from the open window"* was illustrated
   with a couple in bright daytime sunlight. `weather_for()` now prefers a clip whose weather matches the line.
   After both fixes, the final pair scored **11/11 and 10/10 on emotion, and 11/11 and 10/10 on weather**, with no
   black intervals (`blackdetect`) in either file.

A third, older bug resurfaced and was fixed properly: the black-window detection only trimmed head and tail, so a
mid-clip fade still produced one black interval in an emmanuel render. It now uses the **longest gap between black
spans**, which covers lead-in, tail and mid-clip fades. 19 jephter and 3 emmanuel clips are trimmed.

### ForChi voice, verified through its own code

```text
backend: gemini voice: Leda
render_script("You still remember the way the kitchen light fell across her face.", ...)
  temp_media\vt_gemini\p1.wav -> 5.120000 s 251876 bytes      (12 words @ 2.34 w/s — Leda's pace)
```

Revert is one variable: `VOICE_BACKEND=` or `VOICE_BACKEND=contabo`. The F5 path is untouched.

### Delivered

```text
C:\Users\hp\Downloads\jephter_20261101-0756_36s.mp4    36.4 s   voice Leda   11 cuts
C:\Users\hp\Downloads\emmanuel_20261101-0757_34s.mp4   33.6 s   voice Leda   10 cuts
```

Both re-rendered at **zero TTS cost** after the picker fixes (cached voice).

### One decision the owner should confirm

"Replace emmanuel's voice with the current forchi's voice" was read literally: ForChi's voice *became* Leda on this
same day, so **all three now share Leda**. That reverses the earlier "the two channels must not sound the same" rule
(AGENTS.md §12, now amended). If the intent was ForChi's *previous* F5 whisper clone, the answer is to move emmanuel
back to `Umbriel` — one word in `config/workflows.json`, and its numbers are in `skills/x-voice-personas`.

### Dead ends

| Attempted | Result |
| :--- | :--- |
| choosing ForChi's replacement voice by matching the F5 reference clip's pitch | the ref is **whispered**, so its "F0" of 103.9 Hz is unvoiced periodicity, not a voice register. Comparing it to Gemini output is not meaningful |
| `--bars` with head/tail-only trimming | one black interval survived in a mid-clip fade. Use the longest gap |
| a `reuse.json` without a `words` key | `KeyError: 'words'` in the renderer; it now computes the count |

**Left open:** ForChi's changes are **uncommitted** by design; its owner should review and commit. Nothing is
scheduled or uploaded yet. The owner plans to add both channels' emails to the existing ForChi OAuth scope — that is
the next step and it is theirs to do in the console.

---

## 2026-11-01 (emmanuel's voice matched to jephter's; two invisible video defects found and fixed)

**Plane(s):** this folder only. No server change, no OAuth work, keys still not rotated (owner's instruction).

**Asked:** "the voice for jephtah is nice and amazing… the voice for emmanuel isnt, I would like the voice to sound
more like that of jephtah… once you are done remove the old testing video and add the new ones so i can test them.
if both works then we can move on."

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `config/workflows.json` | emmanuel `voice.name`: **Pulcherrima → Umbriel** (cue unchanged) | see the audition below |
| `scripts/x_media.py` | `detect_black_edges()` via `blackdetect`; `--bars` now records `trim_in`/`trim_out` as well as the letterbox `content` rect; both are also written by `--label` | a cut that starts at frame 0 of a fading clip shows black |
| `scripts/x_render.py` | `clean_source()`: builds a **cached trimmed copy** for clips with black lead/tail, and segments loop that copy | looping the raw source re-shows its fade-to-black at every wrap |
| `skills/x-voice-personas` | new pairing table, the "match the liked voice" method, and the lesson below | |
| `skills/x-video-pipeline`, `skills/x-test-video` | black-frame row, `blackdetect` in the verification block, the ear-check row, and the "delete the old pair" pitfall | |

### The voice: what the ear rejected, and what measured it

Re-auditioned 13 voices on an emmanuel line with **Leda as the reference**, scored by distance:

| voice | pace | quiet ratio | gap vs Leda |
| :--- | ---: | ---: | ---: |
| **Umbriel** (chosen) | 2.09 w/s | 0.156 | **0.002** |
| Leda (jephter, the one the owner likes) | 2.23 w/s | 0.158 | — |
| Vindemiatrix | 2.39 w/s | 0.182 | 0.024 |
| Achernar | 2.37 w/s | 0.201 | 0.043 |
| **Pulcherrima** (old emmanuel) | **2.95 w/s** | 0.231 | **0.073** |

Umbriel's pause profile is almost identical to Leda's while the timbre stays different, so the channels remain
distinguishable — which AGENTS.md §12 requires. **The measurable cause of "it doesn't sound nice" was speed**:
Pulcherrima read 13 words in 5.08 s, the fastest of every candidate. Check words/second first when a voice is
rejected.

### Two defects that no passing render would have caught

1. **Black frames at clip boundaries.** A frame at 34.65 s of the delivered jephter video was **entirely black**.
   `blackdetect` on the file found black spans at **21.9 s, 29.4 s and 34.1 s** — each exactly on a cut boundary,
   because stock clips fade up from and down to black. Measured library-wide: **21 of 97 clips** (18 jephter, 3
   emmanuel) have a black lead and/or tail of up to ~0.8 s. Now measured per clip and cut inside the clean window;
   the same frame is now a full-bleed shot of a girl with flowers braided into her hair.
2. **A crop that produced black.** The first diagnosis looked like the letterbox crop failing, so the crop was
   re-tested on the source directly: cropping to `720:800:0:240` returns a *brighter* frame than the full one, and
   `cropdetect` reports the identical rect across the whole clip. The crop was innocent; the black was the source's
   own fade. Worth recording so nobody "fixes" the crop again.

**Verified:**

```text
ffmpeg -vf blackdetect=d=0.08:pix_th=0.10 on both delivered files
  emmanuel_20261101-0644_35s.mp4 : no black intervals
  jephter_20261101-0643_37s.mp4  : no black intervals

delivered (old pair deleted from Downloads first):
  C:\Users\hp\Downloads\jephter_20261101-0643_37s.mp4   20.4 MB  37.4 s  voice Leda
  C:\Users\hp\Downloads\emmanuel_20261101-0644_35s.mp4  17.9 MB  35.3 s  voice Umbriel
  + a .json report beside each

pace in the finished files: jephter 2.28 w/s, emmanuel 2.10 w/s  (previously emmanuel was the fast one)
levels: jephter mean -23.4 dB / peak -2.2 dB;  emmanuel mean -26.3 dB / peak -3.5 dB
cuts: 11 and 10; letterboxed clips handled; 0 clips with burned-in text
```

Both re-renders after the voice change cost **zero TTS calls** except the 5 new emmanuel lines — every other phrase
hit the content-addressed cache.

**Dead ends:** none new. The one wrong hypothesis (a broken crop) is recorded above so it is not chased again.

**Left open:** the same three as before — nobody has listened to the *finished* pair yet (the owner is about to);
the uploader, the schedule and the §8 supervision ladder are not built; `--bars` should be re-run whenever clips are
added to either library.

---

## 2026-10-31 (two test videos delivered to Downloads; letterbox and caption-chunk defects found by looking)

**Plane(s):** this folder only. No server change, no OAuth work, keys still not rotated (owner's instruction).

**Asked:** "give me a video test saved to my download folder, pls add this as a skill in the skill folder so i wont
have to repeat my self again."

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `scripts/x_render.py` | `--deliver`: copies the finished mp4 **and its sidecar report** to `C:\Users\hp\Downloads\<channel>_<stamp>_<s>s.mp4` | the owner looks in Downloads, not in `out/`. Naming carries the length so two files are distinguishable without opening them |
| `scripts/x_render.py` | `pick_cuts` carries `content`; the segment filter crops to it **before** scaling | a letterboxed source keeps its bars through scale+crop |
| `scripts/x_render.py` | `chunk_words()` | fixed 3-word chunks stranded function words — the frame read *"and gather the"* |
| `scripts/x_media.py` | `detect_content_rect()` via `cropdetect`, stored as `content`/`content_coverage`; new `--bars <wf>` backfills a labelled pool | measuring 54 clips again just to add one field is wasted work |
| `skills/x-test-video/SKILL.md` | **new skill** — the delivery convention, the command, the checks, the pitfalls | so this request never has to be repeated |
| `AGENTS.md` | §4 skill table + §12 "one command per channel" now names `--deliver` and the Downloads path | |
| `skills/x-video-pipeline` | stages table gained `--bars`; failure table gained the letterbox and chunking rows | |

### Delivered

```text
C:\Users\hp\Downloads\jephter_20261031-1144_37s.mp4    16.6 MB   37.4 s   11 cuts   voice Leda
C:\Users\hp\Downloads\emmanuel_20261031-1146_36s.mp4   17.3 MB   36.1 s   10 cuts   voice Pulcherrima
  (+ a .json report beside each)
```

Both underneath the 50 s cap, both verified with ffprobe (`h264 / 1080x1920 / aac`), and both **looked at** — a frame
from the middle of each, not the first frame.

### Two defects found by looking, not by rendering

1. **Letterboxed sources.** The first jephter test had black bands top and bottom. Measured with `cropdetect`:
   **6 of 97 clips** are letterboxed inside their own frame — e.g. `Pookie_edits.mp4` is 720×1280 but its content is
   `720:876:0:202`, **68 %** of the frame; `Vikas_Gangwar.mp4` is **62 %**. `scale`+`crop` cannot fix that because
   the bars are part of the picture. Now measured once and cropped per segment; emmanuel's re-render used one of the
   affected clips (`ᗩEᔕTᕼETIᑕ_ᐯIᗷE𓂃…`, content `720:1118:0:0`) and came back full-bleed.
2. **Caption chunks stranded function words.** A frame read *"and gather the"*. `chunk_words` now extends a chunk by
   one word rather than end on `the`/`and`/`of`. Verified on the exact failing line:
   `"They step into / the small space / between your knees / and gather the hem / of your shirt."`

### Zero-cost re-render, used for real

Both videos were re-rendered after those fixes **without spending TTS quota**: the script was extracted from the
previous sidecar into `out/<wf>/scripts/reuse.json` and passed as `--phrases-file`, so every phrase hit the
content-addressed voice cache (`(cached)` on every line). This is the documented way to iterate on a finished video.

### Dead ends

| Attempted | Result |
| :--- | :--- |
| `cropdetect` parsed with a 2-way unpack | `ValueError: too many values` — it returns `w:h:x:y`, four fields |
| printing emmanuel's clip filenames to a cp1252 console | `UnicodeEncodeError` on `✮`; `xf.utf8_stdout()` is required in every ad-hoc probe too, not only in the scripts |

**Still open:** unchanged — TTS quota is the practical limit (it reset on 2026-10-31 and both pools worked); nobody has
listened to the two voices end to end; the uploader, the schedule and the §8 supervision ladder are not built.

---

## 2026-10-28 (video workflows built and proven for both channels: 50 s romance shorts, two voices, own songs)

**Plane(s):** this folder, plus `169.58.212.7` (ffmpeg + the brand fonts installed, `/opt/x` created). No OAuth work
— parked by the owner. Keys **not** rotated, per the owner's instruction.

**Asked:** "build the workflows first for the jephtah and then for the emmanuel… both should be isolated from each
other… use the same format just as forchi… the two workflow will not have the same voice and both will be
relationship focused, romance and couple based scripts and each video should not be longer than 50sec… each
workflow has their own videos and songs… we'll have to label and cut the scenes just like we did with forchi, the
songs wont be cut but will be labelled and edited to match the script, the songs can be cut depending on the script
so one in every video wont have the same sound… caption, use the same font style as used in rezmo and then the
caption should be bold too… the gemini api keys should use the same water fall method as forchi."

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `scripts/x_media.py` | **new.** Measures each clip (brightness, colour temperature, motion -> mood) and each song (duration, mean/peak level, energy) into `config/scenes|songs/<wf>.json`. `--vision` adds `subject`, `baked_text` and a verified mood from one batched frame pass | ForChi's clip manifest, but the labels are measured: 97 clips is where hand-labelling stops |
| `scripts/x_ffmpeg.py` | **new.** One home for system-ffmpeg selection, UTF-8 stdout, `volumedetect` levels, and peak-gain + `alimiter` | three files were about to repeat the same three bugs |
| `scripts/x_script.py` | **new.** Romance script writer (second person, concrete, no moral), plus `complete_ending` / `repair_ending` | see the truncated-ending incident below |
| `scripts/x_voice.py` | **new.** Per-line TTS, WAV/PCM normalisation, `--audition` | narration must be one file per line so cuts and captions can be timed |
| `scripts/x_render.py` | **new.** Cut plan, song window, captions, scrim, ffmpeg assembly, sidecar report | the Shorts assembly, at a 50 s cap |
| `scripts/make_fonts.py` | **new.** Instantiates `Jost-Bold.ttf` (wght 700) and the Cormorant faces from REZMO's own variable fonts | "same font as rezmo" + bold, as a real bold rather than a synthetic one |
| `scripts/make_assets.py` | **new.** Generates `assets/scrim_1080x1920.png` | white captions vanish over snow; committed so the renderer needs no PIL |
| `scripts/bootstrap_vps.py` | **new.** Installs ffmpeg + fonts on `169.58.212.7`, then verifies filters and font count | that box had **no ffmpeg at all** |
| `scripts/gemini_keys.py` | `GeminiPool.generate_audio()` — the same key rotation/cooldown/ladder applied to TTS, plus `sense_text` and `_audio_of` | the waterfall the owner asked for, extended to speech |
| `config/workflows.json` | per-workflow `media_dir`, `songs_dir`, `output_dir`, `video`, `voice`, `caption`, `watermark`, `music` | both channels driven by data, not code |
| `.gitignore` | media extensions and the two media folders, anchored so `web/site/jephter/` is **not** ignored | `jephtah/` held 240 MB of MP4 in a public repo |
| `AGENTS.md` §12 | rewritten: independence table now covers voice and song use; the owner's video brief recorded verbatim in substance; file map and the one command per channel | |
| `skills/x-video-pipeline`, `skills/x-voice-personas` | **new skills** | |

### Renders produced

```text
out\jephter\jephter_20261029-001635.mp4   35.4 s  15332 KB  peak -1.4 dB   voice Leda
  10 cuts — subjects couple=6 single=4, 0 on baked-text clips
  song 'Letters in Lamplight - November Without You' @ 78.4 s (use #1)

out\emmanuel\emmanuel_20261029-001254.mp4  41.0 s  20369 KB  peak -4.0 dB   voice Pulcherrima
  12 cuts — subjects couple=9 single=3, 0 on baked-text clips
  song 'Celestial Hearts - Midnight Silk' @ 32.5 s (use #1)
```

Both `under_cap: true`. Frames were extracted and **looked at**: jephter is a couple in a bookshop with a bold Jost
caption over the scrim and a Cormorant watermark; emmanuel is a couple at a bathroom mirror, same treatment. That
matters because a file that exists is not a file that is right.

Isolation, checked rather than asserted:

```text
voices differ: Leda vs Pulcherrima
song dirs:  jephtah\songs   vs   emmanuel\songs
song start offsets: 78.4 s  vs  32.5 s
gemini pool audit: 8/8 invariants hold (9/9, disjoint, balanced, nothing unowned)
```

### What measurement changed

| Assumption | Measured reality |
| :--- | :--- |
| a style sentence steers Gemini TTS | **it is spoken aloud.** Transcribed back: *"speak slowly and softly I still remember…"*. A bracketed cue is stripped from the speech and still changes delivery: **-9.4 dB quieter**, slightly slower. That is the whisper |
| any quiet voice is fine | ten voices on one line range from **-20.7 dB (Sulafat, not a whisper)** to **-29.5 dB (Leda)**; Despina is 1.49 words/s, which would make an 85-word script ~57 s and break the cap |
| `volumedetect` output is available | it prints at INFO level; `-v error` made **every song read `energy: unknown`** — twice, because failures were then cached as labels |
| the clip library is all couples | 33 couple / 19 single / 2 scenery (jephter), 31 / 7 / 5 (emmanuel) — and **8 jephter clips carry burned-in text** that collides with our captions |
| TTS calls are cheap enough to re-run | after ~30 calls **every key in the jephter pool answered 429 on all three TTS models**. Renders are now content-addressed, so re-rendering costs 0 TTS calls |

### The failure I shipped and then fixed, in one commit

The first emmanuel render ended on **"You just stand together in."** — the ending guard caught the truncated tail,
then *appended a full stop to it* and published it. Fixed: a short or dangling tail is now **dropped** so the previous
sentence becomes the closing image, and the build refuses if no complete ending survives three attempts. Both real
failures are now regression cases:

```text
"real failure 1"  ['You sit in the dark driveway.', 'The radio plays']
   -> dropped ['The radio plays']            final_ok=True
"real failure 2"  ['You stand by the stove.', 'You just stand together in.']
   -> dropped ['You just stand together in.'] final_ok=True
"good"            -> untouched, valid=True
"unpunctuated but real" -> period added (not dropped), final_ok=True
```

### VPS

```text
python scripts\bootstrap_vps.py
  installed: ffmpeg 6.1.1-3ubuntu5
  /usr/bin/ffmpeg, /usr/bin/ffprobe
  filters present: subtitles, ass, drawtext, amix, alimiter
  fonts in /opt/x/fonts: 4      python 3.12.3      /dev/sda1 193G 2.7G 190G 2%
```

The brand fonts live in `/opt/x/fonts` rather than system-wide, so installing them cannot change any other tool that
asks for "Jost".

### Dead ends — do not repeat

| Attempted | Result |
| :--- | :--- |
| ASR via `gemini-3.5-transcribe` | returned empty strings (and one network timeout). `gemini-3.8-flash` with the audio inline transcribed correctly |
| style sentence as prose / as a second prompt part | both are **read aloud**; two-part was worst (9.6 s for an 11-word line) |
| `[whispering]` as the cue | not spoken, but made the read **faster** (3.2 s vs 6.4 s). The cue's wording changes pacing |
| variable TTF + `Bold: -1` in ASS | gives the default instance, i.e. synthetic bold. Instantiate wght 700 instead |
| `loudnorm` | still not used; it dropped ~3 s of audio in this pipeline's predecessor |
| trusting `config/songs/<wf>.json` after a failed probe | an unusable label was cached as if it were a hand-correction; only *successful* measurements are now reused |

**Deprecated, cleaned:** nothing. Every file touched this session is listed in the table above.

**Left open — in blocking order:**

1. **TTS quota.** The jephter pool is currently out of TTS quota; emmanuel's render succeeded. Either wait for the
   daily reset, add keys to the pool (`x-gemini-key-pool`), or point `voice.backend` at a clone. Nothing else blocks
   a render.
2. **A human listen.** The numbers say whispery (-29 dB, 0.33 quiet ratio) and the level is right; nobody has
   actually listened to both voices end to end. That is the one check code cannot do.
3. **The uploader** — `youtube_auth.py` gets the credential, nothing calls `videos.insert`. Then the schedule, then
   the §8 ladder (systemd, watchdog, email, external heartbeat).
4. **Media sync to the VPS** — `jephtah/` (240 MB) and `emmanuel/` (203 MB) are local only; `bootstrap_vps.py` makes
   the box ready but does not copy the libraries.

---

## 2026-10-27 (the pool claims checked, the token actually encrypted, the pages looked at)

**Plane(s):** this folder + `169.58.212.7` (the pages were reopened in a browser to look at them). No new server
change; the site deployed earlier is unchanged.

**Asked:** (continuing the owner's "secure their console, split the keys" stage) — make sure what is written down is
true and verified rather than asserted.

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `scripts/gemini_keys.py` | new `--audit`: eight invariants — count, uniqueness, no truncated key, **pools disjoint**, **pools balanced**, no stale id, nothing unowned, at least one pool populated. Network-free | the 9/9 independence is the whole point of the split, so it is now checked instead of assumed, and it is the command to run **after rotating keys** |
| `scripts/gemini_keys.py` | `write_pools()` now preserves `_`-prefixed annotations | it was dropping `_status`, i.e. the only machine-readable record that these keys are due for rotation |
| `scripts/youtube_auth.py` | refresh token and client secret **sealed with AES-GCM** (`cryptography` 49.0.0, already installed), key from `X_SECRET_KEY` in `.env` else `secrets/master.key` (created 0600), workflow name as associated data | the privacy policy served to Google says the token is encrypted at rest. It was not — it was plaintext JSON. Either the code or the text had to change |
| `scripts/youtube_auth.py` | new `--selftest`: round-trip, rejection under the wrong workflow name, and no plaintext on disk | proves the claim without touching Google |
| `scripts/gemini_keys.py` | `generate()` now has a **wall-clock budget** (`total_timeout=300 s`) and per-request timeouts capped by the remaining budget | a hung request could previously stall a caller for 4 models × 2 attempts × 120 s = **16 min of silence**, which is exactly the failure mode an unattended pipeline cannot have |
| `scripts/gemini_keys.py` | `ladder_for(workflow)`: each workflow **starts on a different rung** of the model ladder | measured: both pipelines starting on `3.8-flash`, which 503s under load, is self-inflicted contention. Slower rung first for one of them is faster overall |
| `.env.example` | `X_SECRET_KEY` added **by name** with the one-liner that generates it | |
| `AGENTS.md` §12 | "Guards" block: `git config core.hooksPath .githooks` (per clone — the guard is **inert** until it runs), `--audit`, `--selftest`, plus the rule that the page text and the encryption move together | |
| `skills/x-google-console-oauth` | the credential section now describes the sealing and the self-test | |

**Verified:**

```text
python scripts\youtube_auth.py --selftest
  ok   seal/unseal round-trip
  ok   ciphertext rejected under the wrong workflow name
  ok   no plaintext in the written file (666: posix mode is not enforced on Windows)
  exit=0

python scripts\gemini_keys.py --audit
  ok   key file has 18 keys          ok   every key is unique
  ok   no truncated key              ok   the two pools are disjoint
  ok   pools are balanced: jephter=9 emmanuel=9
  ok   every assigned id exists in the key file (0 stale)
  ok   every key belongs to a pool or the spare (0 unowned)
  ok   at least one workflow has keys
  8/8 invariants hold

python scripts\gemini_keys.py --split   ->  jephter=9, emmanuel=9, and `_status` is still in the file after the write
git check-ignore -v secrets/master.key  ->  .gitignore:18:secrets/   secrets/master.key
```

Looked at the delivered page in a browser, not just at its status code: `http://169.58.212.7/jephter/privacy.html`
renders as a clean policy page — nav, eight numbered sections, the three YouTube/Google policy links, the contact
address in the footer.

Two timings that motivated the last two changes, same prompt both times:

```text
before the ladder rotation        jephter 128.4 s (3.8-flash 503 x2, then 3.6)     emmanuel 102.9 s
after  (emmanuel starts at 3.6)   emmanuel 5.8 s
ladder_for('jephter')[0] -> gemini-3.8-flash      ladder_for('emmanuel')[0] -> gemini-3.6-flash
```

**Left open:** unchanged from the entry below — key rotation, client id/secret per workflow, the domain for TLS,
then voice.

---

## 2026-10-27 (INCIDENT: all 18 Gemini keys were printed in full to a terminal; hook rewritten to never echo)

**Plane(s):** this folder. No server change. No third party received anything.

**What happened.** While proving that `.githooks/pre-commit` would block a staged key file, the probe staged the real
`Gemini api key` file and the hook reported the offending lines with `cut -c1-60`. The keys are 53 characters, so
**60 characters printed each of them in full** — all 18, in the terminal output of that command, which is part of
the session transcript.

**Scope and remedy.** These keys must be treated as **compromised and rotated**:

1. In each Google Cloud project that owns one of them: **APIs & Services → Credentials → API keys**, delete the
   existing keys and create replacements.
2. Replace the contents of `Gemini api key` (one key per line, same count so the sibling/pool structure holds).
3. `python scripts\gemini_keys.py --split` then `--validate`.
4. The ids in `config/gemini-pools.json` are `sha256(key)[:12]` and will therefore **change** when the values
   change; the pool size per workflow should stay 9 / 9.

Nothing in git history contains a value: the probe commits were made and then removed with `git reset --mixed` (they
were never pushed), and `git log --all -- '*probe*' '*api-key*'` returns nothing. The exposure is the transcript,
not the repository.

**Changed:**

| File | Change |
| :--- | :--- |
| `.githooks/pre-commit` | created **twice**: the first `create` failed on a missing parent directory and was never retried, so the hook **did not exist** and two probe commits went through. It now exists (2559 bytes) and blocks all three probes |
| same | the matching line is now reported as `staged line N` **only**. The rule: a hook must never print the thing it is protecting — a truncation to 60 chars is not redaction when the secret is 53 |
| `.gitignore` | media added: `/media/ /music/ /videos/ /jephtah/ /jephter/ /emmanuel/` and `*.mp4 *.mov *.webm *.mkv *.wav *.mp3 *.m4a *.flac`. Anchored with a leading slash so generated page folders (`web/site/jephter/`) are not caught |

**Verified:**

```text
git check-ignore -v 'Gemini api key'        -> .gitignore:30:Gemini*      Gemini api key
git check-ignore -v 'jephtah/aesthetic.mp4' -> .gitignore:39:/jephtah/    jephtah/aesthetic.mp4
git check-ignore  web/site/jephter/privacy.html  -> (exit 1 = not ignored, correct)

probe 1, a 48-char opaque token line : BLOCKED  "a line that is nothing but a long opaque token"
probe 2, git add -f of a key file    : BLOCKED  "a provider token or private key is in the staged diff"
probe 3, git add -f of an .mp4       : BLOCKED  "refusing credential- or media-shaped path"
re-probe with a synthetic AQ. value  : BLOCKED, and prints only "staged line 1" — no content
git log --oneline -2                 : d20de71 / 42b2d68  (probe commits gone)
```

**Lesson for the skill folder.** Written into `skills/x-gemini-key-pool`: *print the id, never the value — and that
includes a "truncated" match inside a guard script.* The second lesson is duller and cost an hour: a `create` that
fails is not a file that exists; check the path before trusting a guard to have run.

---

## 2026-10-27 (stage 1 of the two YouTube workflows: keys secured and split, consoles runbooked, verification pages live)

**Plane(s):** this folder + `169.58.212.7` (nginx installed, `/srv/x-site` created, port 80 now serving).
Ten outbound HTTPS calls to `generativelanguage.googleapis.com` and two documentation reads from
`developers.google.com`. **Nothing was sent to `178.18.245.136` and nothing will be.**

**Asked:** "start with securing their google console first and sharing the gemini api keys." Two clients, two
YouTube channels, two workflows to be called **jephter** and **emmanuel**, each independent of the other; a Gemini
key file was dropped in the folder to write the scripts. Also: read the skill folder, add skills as you go, and keep
this log so the next agent does not rediscover anything.

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `.gitignore` | new block ignoring loose credential dumps by shape (`secrets/`, `*api*key*`, `*apikey*`, `*secret*.json`, `client_secret*`, `credentials*.json`, `*keys*.txt`, `*key*.txt`, `Gemini*`, `tokens*.json`) | see the finding below — this was the urgent one |
| `.githooks/pre-commit` | **new.** Refuses (a) a credential-shaped path and (b) a staged diff containing `AIza…`, `AQ.…`, `ya29.…`, `ghp_…`, `github_pat_…`, a `BEGIN … PRIVATE KEY` block, or a line that is nothing but a 40+ character opaque token | `.gitignore` only protects a name; this looks at what is actually staged |
| `scripts/gemini_keys.py` | **new.** Loads the key file, derives `sha256(key)[:12]` ids, splits round-robin into the two workflows, rotates keys on 429/403, walks a model ladder on 503/timeouts, bounded backoff with jitter, raises `PoolExhausted` rather than returning empty text | the split and the fallbacks in one place, no key ever printed |
| `config/gemini-pools.json` | **new**, committed. Key **ids** only: `jephter` 9, `emmanuel` 9 | reproducible, safe to publish, survives a reorder of the key file |
| `scripts/youtube_auth.py` | **new.** Loopback consent on `http://localhost:8765/<workflow>/callback`, code→token exchange, stores `secrets/youtube/<workflow>.json` (0600), then **reads back the authorised channel** via `channels.list(mine=true)` | the redirect URI has to match the console exactly; the read-back is the only proof consent landed on the right channel |
| `scripts/build_site.py` | **new.** Renders the app homepage, privacy policy and terms per workflow from `config/workflows.json` into `web/site/` | Google will not verify without them; the YouTube policy clauses are easy to omit on the second page |
| `scripts/deploy_site.py` | **new.** Installs nginx if absent, writes `/etc/nginx/sites-available/x-site`, uploads `web/site/` to `/srv/x-site`, then verifies every URL with curl | the pages have to be *served*, not just present |
| `config/workflows.json` | **new.** Product name, channel handle, contact email, persona, page paths per workflow. Placeholders are marked `TODO` | one source of truth for both clients |
| `web/site/` | **new**, 8 generated files | output of `build_site.py` |
| `.env` / `.env.example` | added by **name** only: `GEMINI_KEYS_FILE`, `GEMINI_MODEL`, `JEPHTER_GOOGLE_CLIENT_ID/_SECRET`, `EMMANUEL_GOOGLE_CLIENT_ID/_SECRET`, `SITE_HOST` | the four client values are **empty** and are filled from each client's own console |
| `AGENTS.md` | §4 gained the project-skill table; new **§12** records the independence contract, the file map, the two console facts, and what is still open | a reader should not have to infer the architecture from the scripts |
| `skills/x-gemini-key-pool/SKILL.md` | **new skill** — key file shape, ids, the 9/9 split, rotation, measured 404/503/empty-candidate/latency facts | |
| `skills/x-google-console-oauth/SKILL.md` | **new skill** — desktop client vs web client, the loopback URIs, the 7-day Testing trap, the `videos.insert` bucket, the verification pages, failure catalogue | |

### The finding that came first

`git status` showed the new key file as `?? "Gemini api key"` — **untracked but not ignored** — in a repository that
is **public**, with AGENTS.md instructing `git add -A`. One commit away from publishing 18 live Gemini keys. Fixed
by the `.gitignore` block and the pre-commit hook before any other work started. The file has never been committed
(`git ls-files` does not list it).

**Verified:**

```text
python scripts\gemini_keys.py --split   ->  wrote config\gemini-pools.json: jephter=9, emmanuel=9
python scripts\gemini_keys.py --validate
  3087b333c979  jephter   HTTP 200  generates on gemini-3.6-flash
  ... (18 rows, alternating jephter/emmanuel) ...
  validated 18 keys, 0 not usable
python scripts\gemini_keys.py --generate jephter  "Reply with the single word: ready"  ->  ready
python scripts\gemini_keys.py --generate emmanuel "Reply with the single word: ready"  ->  ready
```

```text
python scripts\deploy_site.py
  # connected to 169.58.212.7 as root over key x-vps-ed25519
  nginx: nginx version: nginx/1.24.0 (Ubuntu)
  uploaded 8 files to /srv/x-site
  vmi3701960 / root / 2026-10-27T23:06:38+01:00
  files: 8
  200 /            200 /jephter/      200 /jephter/privacy.html   200 /jephter/terms.html
  200 /emmanuel/   200 /emmanuel/privacy.html                     200 /emmanuel/terms.html
  youtube-disclosure hits in jephter/privacy.html: 3
```

Checked again **from this workstation**, i.e. through the public interface rather than from the box itself:

```text
200 /                      bytes=1144
200 /jephter/privacy.html  bytes=4016
200 /emmanuel/privacy.html bytes=4025
200 /jephter/terms.html    bytes=2923
```

Consent URLs (client id is a placeholder; the **redirect URI is the part that matters**):

```text
https://accounts.google.com/o/oauth2/v2/auth?client_id=TESTID...&redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fjephter%2Fcallback
  &scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.upload+openid+email&access_type=offline&prompt=consent
redirect_uri must be registered exactly as: http://localhost:8765/jephter/callback
                                           http://localhost:8765/emmanuel/callback
```

### Facts measured today, worth not rediscovering

| Fact | Evidence |
| :--- | :--- |
| The 18 keys are **53 chars, shape `U.Ul9…`** — not the classic `AIza…` AI Studio shape — and they work against `generativelanguage.googleapis.com/v1beta` | `models.list` HTTP 200 on key `3087b333c979` |
| **18** keys, not 17 | `count=18 unique=18` |
| `gemini-2.5-flash` / `gemini-2.0-flash` are **listed** but answer **HTTP 404** *"no longer available to new users"* | raw 404 body, quoted |
| `gemini-3.8-flash` answers **HTTP 503 UNAVAILABLE** *"high demand"*; 4/4 attempts failed in one pool cycle, then 3.6 worked | `--generate jephter` retry log |
| **Thinking tokens count against `maxOutputTokens`** — a 16-token ceiling returns an *empty* candidate with `thoughtsTokenCount 58` | raw 200 body |
| Generation latency ranged 7.3 s / 21 s / 138 s for the same prompt | timed probes |
| Serial validation of 18 keys did not finish in 30+ min | it was stopped; `--validate` now uses 8 threads and takes ~1 min |
| **`videos.insert` has its own bucket: 100/day per project** (the old "1600 units → 6/day" figure is obsolete) | Google quota calculator, revision 2026-10-15 |
| **An External app in Testing status gets refresh tokens that expire in 7 days**, unless the scopes are only name/email/profile | Google OAuth docs, *Refresh token expiration* |

### Dead ends — do not repeat

| Attempted | Result |
| :--- | :--- |
| `generateContent` on `gemini-2.5-flash` | HTTP 404, model retired for new users. Use the ladder in `gemini_keys.py` |
| `maxOutputTokens=4/16` with thinking on | HTTP 200 with an **empty** candidate. Raise the ceiling or set `thinkingBudget: 0` |
| `--validate` serially | wanted ~2.4 h worst case; abandoned and parallelised |
| `http://169.58.212.7/…` as an OAuth **Web** redirect URI | will be refused by Google (no bare IPs); use the Desktop client + loopback |
| `print(out.strip())` on remote UTF-8 output under a cp1252 console | `UnicodeEncodeError` after a *successful* deploy. `deploy_site.py` now reconfigures both streams to utf-8 |

**Left open — in the order they block the next step:**

1. **Client id + secret for each workflow**, from each client's own console, into `.env` as
   `JEPHTER_GOOGLE_CLIENT_ID` / `_SECRET` and `EMMANUEL_GOOGLE_CLIENT_ID` / `_SECRET`. The console steps are in
   `skills/x-google-console-oauth` §"Console steps". Then: `python scripts\youtube_auth.py --workflow jephter`
   and read back the channel it prints.
2. **A domain → `169.58.212.7` → TLS** for the privacy/terms pages. They answer on the plain IP today; Google
   verification needs a real domain over HTTPS. One domain with `/jephter/` and `/emmanuel/` paths is enough.
   After that: `certbot --nginx` on `vmi3701960`, then set `site_host` in `config/workflows.json` and re-run
   `build_site.py` + `deploy_site.py`.
3. **Voice** — one reference clip per workflow, romantic and whispery, and distinct from each other. Read
   `voice-cloning` in the shared library first; the bleed fix is at the source (a non-overlapping reference), not
   in cfg. The keys can also reach `gemini-3.8-flash-tts` if a Gemini voice is preferred to a clone.
4. **Script → video → upload**, then the schedule, then the §8 ladder (systemd, watchdog, email, external
   heartbeat). Nothing is scheduled yet; there is no timer and no service on the box.


## 2026-10-27 (published as a public repository: Moorejae/Xsocial)

**Plane(s):** GitHub + this folder. No server change.

**Asked:** add the same GitHub token to this project's `.env`, and create a new **public** repository called `Xsocial`.

**Changed:**

| Item | Detail |
| :--- | :--- |
| `.env` | `GITHUB_TOKEN` (copied from `REZMO.env`, fine-grained PAT), `GITHUB_USER=Moorejae`, `GITHUB_REPO=Xsocial` |
| remote | `origin` → `https://github.com/Moorejae/Xsocial.git` |
| branch | renamed `master` → `main` (the first push failed with `src refspec main does not match any` because `git init` had produced `master`) |
| git auth | `gh auth setup-git`, so pushes use the `gh` credential helper and **the token is never written into `.git/config`** |
| `AGENTS.md` | new §10 GitHub: the remote, the credential path, and the public-repo rules; "before you report done" became §11 |

**Verified:**

```text
created: Moorejae/Xsocial private=False
local  HEAD 42b2d68af00402a5600870ff785e839fa28b0fa1
remote main 42b2d68af00402a5600870ff785e839fa28b0fa1     match: True
visibility: public
files at root on the remote:
  .env.example   .gitignore   AGENTS.md   HANDOFF_LOG.md   scripts   skills
```

**The important line is the one that is missing: `.env` is not on the remote.** Confirmed by listing the repository
contents through the public API, not by trusting `.gitignore`.

**Token facts worth keeping:** it authenticates as `Moorejae` and can read and push to the owner's repositories. It is
scoped to those repos, so it **cannot create a new repository** — creation needs `Administration: write` or the `gh`
login, which is why the repo was made with `gh`'s scope rather than the PAT.

**Left open — a decision for the owner, not something to change unasked:** this repository is public, and `AGENTS.md`
names both server addresses (`178.18.245.136`, `169.58.212.7`) and the alert mailbox, because the prohibition is only
enforceable if the host is named. No credential is published — but if the owner would rather the IPs were not public,
the fix is to name the hosts by role (`REZMO production`, `this project's VPS`) and keep the addresses in `.env` only.

---

## 2026-10-27 (access proven over a key; the password reduced to a fallback)

**Plane(s):** `169.58.212.7` only. Two server-side changes: one public key installed, and `authorized_keys` rewritten to
contain only it.

**Asked:** the owner supplied the root password for the new VPS.

**Changed:**

| File / host | Change |
| :--- | :--- |
| `.env` | `CONTABO_LOGIN_PASSWORD` now populated by the owner; `CONTABO_SSH_KEY_PATH` set to `C:\Users\hp\.ssh\x-vps-ed25519` |
| `scripts\vps.py` | connection now **tries the key, then falls back to the password**, and reports which worked. A broken key cannot lock the project out while a valid password exists, and vice versa. |
| server `/root/.ssh/authorized_keys` | replaced with the single project key (`SHA256:53648u+…`, comment `x-vps-20261027`); mode 600, dir 700 |
| `AGENTS.md` §5 | rewritten: current state, the exact working output, and the dead ends worth never repeating |

**Verified — three independent checks:**

```text
python scripts\vps.py --check
# connected to 169.58.212.7 as root over key x-vps-ed25519
vmi3701960 / root / Ubuntu 24.04.5 LTS / cores: 6
Mem: 11960 total, 520 used, 11430 available
/dev/sda1 193G 1.9G 191G 1%
load: 0.16 0.24 0.20

key-only client (no password, no agent, look_for_keys=False):
vmi3701960 / root / key-auth-ok

ssh-keygen -lf /root/.ssh/authorized_keys
256 SHA256:53648u+PI8sgDOcXTQTdIy6N+4h4RmXkgnM0lPSSMJQ x-vps-20261027 (ED25519)
```

**Two mistakes made and fixed in this step, recorded so they are not repeated:**

1. `ssh-keygen -N '""'` in PowerShell sets the passphrase to the two literal quote characters, producing an **encrypted**
   key. It failed with `PasswordRequiredException` on first use. Regenerated through `cmd.exe` with `-N ""`, then proved
   unencrypted with `ssh-keygen -y -f <key> -P ""`.
2. The first `vps.py` treated a set-but-broken key as fatal instead of falling back. That is a lockout waiting to
   happen, so the connection path now walks the available methods and only fails when all of them do.

**Left open:** nothing blocking. The password remains in `.env` deliberately as the fallback; once the owner is happy
with key access it can be deleted, and the recovery path in AGENTS.md §5 then depends on the Contabo panel alone.

**Also noted for the next agent:** this box is the same tier as REZMO's host (6 vCPU / 12 GB / 200 GB) and currently
uses 1.9 GB of disk with almost no load. Nothing has been installed or configured beyond SSH access — there is no
application here yet.

---

## 2026-10-27 (project bootstrapped: env, rules, handoff habit, and an honest account of the login state)

**Plane(s):** this folder only. No connection was made to the server except two read-only reachability probes, and
nothing was sent to `178.18.245.136`.

**Asked:** create an `.env` carrying the Contabo IP `169.58.212.7`; write a handoff file that forbids any agent from
touching the REZMO VPS `178.18.245.136`; tell agents to use the skill folder, to write a handoff entry for every
change, and to add a new skill for everything they learn; and make sure an agent has what it needs to log in.

**Created:**

| File | Purpose |
| :--- | :--- |
| `.env` | the server (`CONTABO_IP_ADDRESS=169.58.212.7`), the login user, the key path, the alert address |
| `.env.example` | the same keys with no values, safe to commit |
| `.gitignore` | env files by shape (not just by exact path), keys, logs, build output |
| `AGENTS.md` | the rules: the REZMO prohibition, the skill library, the two habits, the fallback ladder |
| `HANDOFF_LOG.md` | this file |
| `skills/README.md` | the format for project skills |
| `scripts/vps.py` | the supported way to run anything on the server |

**Verified about the server, 2026-10-27:**

```text
PTR  169.58.212.7 -> vmi3701960.contaboserver.net     (Contabo, instance vmi3701960)
TCP  169.58.212.7:22 -> open
TCP  169.58.212.7:443 -> closed                       (nothing serving HTTPS yet)
```

**What does NOT authenticate — checked so nobody repeats it:**

| Method | Result |
| :--- | :--- |
| `keys\rezmo-contabo-ed25519.key` | denied |
| `keys\rezmo-vm-ed25519.key` | denied |
| `keys\rezmo-vm-rsa.key` | invalid key file |
| root password held in ForChi's `.env` | denied |
| Contabo REST API (`CONTABO_CLIENT_ID` / `_SECRET` from ForChi's `.env`) | `HTTP 401 unauthorized_client` — those API credentials are invalid |

Single attempts, not a sweep: four known credentials, each tried once.

**Left open — one owner action, and it is the only blocker:** `CONTABO_LOGIN_PASSWORD` in `.env` is empty. The root
password for `vmi3701960` is not held anywhere on this machine and was not invented. Fill it from the Contabo panel
(`my.contabo.com` → instance `vmi3701960` → initial password or **reset password**), then run:

```powershell
python scripts\vps.py --check
```

Once that succeeds, install a key (`scripts\vps.py --install-key <pub>`) and blank the password, so the password stops
being the only way in. Recorded in AGENTS.md §5.

**Note on reuse:** the Contabo API credentials found in another project's `.env` are dead. Do not copy them here or
retry them; if API access is wanted, mint a fresh API client in the panel.
