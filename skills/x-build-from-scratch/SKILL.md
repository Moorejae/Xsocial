---
name: x-build-from-scratch
description: 'The whole X pipeline as an ordered build: from an empty clone to two channels that post twice a day by themselves. Use when standing up a new channel or a new host, when asked how this thing is built or what order to do it in, when rebuilding after something was lost, or when you need the acceptance sweep that proves it will still post tomorrow with nobody watching. Every stage names the command that makes the artefact and the check that proves it worked; the per-area detail lives in x-google-console-oauth, x-gemini-key-pool, x-video-pipeline, x-voice-personas, x-publishing, x-watchdog and x-vps-deploy, and this file is the order and the gates.'
---

# The whole thing, in the order it has to happen

Two channels, two Google projects, two key pools, one pipeline, no shared state. The build is eleven
stages and each one has a gate: something you can run that either prints proof or does not. The gates
exist because every failure this project has had was a stage that *looked* done — a task that never ran
for seven passes, a token that was valid for seven days and then was not, a private upload that reported
success, a ledger that forgot a claim.

**The rule for this document:** never move to the next stage on the strength of the previous stage's
output. Run the gate. A stage without a gate does not exist.

---

## Stage 0 — read, then arm the repo

```powershell
cd C:\Users\hp\Downloads\X
git config core.hooksPath .githooks          # per clone: the pre-commit secret guard is inert until this runs
python tests\run_tests.py                    # what "green" means before you change anything
```

Then `AGENTS.md` §0 in full, the top of `HANDOFF_LOG.md` (newest first), and every skill whose
`description` matches the work. §1 is a hard prohibition: never touch `178.18.245.136`.

**Gate:** `git config core.hooksPath` prints `.githooks`, and the suite reports `0 failed`.

---

## Stage 1 — identity, per channel, in config only

`config/workflows.json` holds one block per workflow and **no secrets** — it is committed, so it may only
name key *ids* and settings.

| Field | What it decides | Trap |
| :--- | :--- | :--- |
| `channel_name`, `product_name`, `channel_handle`, `site_host` | the words in the consent screen and the pages Google reads | `@TODO-…` and `TODO.example.com` are still in there; Google verification cannot pass on them |
| `media_dir`, `songs_dir`, `output_dir` | where this channel's footage, music and renders live | **`jephter` → `media_dir: jephtah`** (spelled with an `a`). The workflow name and the folder name differ on purpose; do not "fix" it |
| `gemini_pool` | which half of the key file is usable | a pool name that is not in `config/gemini-pools.json` fails at the first script call |
| `voice` | `name`, `cue`, `model` | the cue must be **bracketed** — see `x-voice-personas`; an unbracketed cue is spoken aloud |
| `schedule` | `produce_hours` [7,14], `post_hours` [10,17], `daily_count` 2, jitter, retry | `jitter_base_minutes` is what keeps the two channels out of the same minute |
| `reminder_email` / `contact_email` / `channel_email` | who is told what | reminders go to `reminder_email` (the operator); the client's own inbox is `channel_email` |

**Gate:** both blocks parse, `channel_email` is real, `media_dir` exists, and `voice.cue` is in square
brackets.

```powershell
python scripts\x_scheduler.py --verify
```

---

## Stage 2 — keys: split, validate, never print

```powershell
python scripts\gemini_keys.py --audit     # 9/9, disjoint, balanced, nothing unowned. No network.
python scripts\gemini_keys.py --validate  # one cheap call per key
python scripts\gemini_keys.py --models    # which models answer for each key
```

The pool file (`config/gemini-pools.json`) stores **ids only**; values live in the ignored key file. Two
channels must never share a key, because quota is per Google project and the whole independence contract
rests on it.

**Gate:** `--audit` reports `9/9` twice and no shared id. A key that is refused or out of quota is demoted
and the next one tried; that is `x-gemini-key-pool`, which also explains why a `429` is not a dead key
(cooldowns are `60 s × attempt` in-process, so a storm costs minutes).

---

## Stage 3 — one Google project, one OAuth client, per channel

Console work, not code. Full detail and the error codes in `x-google-console-oauth`.

| Step | Value |
| :--- | :--- |
| Project | **one per channel** — quota and the consent screen are per project |
| Enable | YouTube Data API v3 |
| Consent screen | External, **Testing**, with each channel's Google account added as a test user |
| Scopes | `youtube.upload`, `youtube.readonly`, `openid`, `email` |
| Redirect URI | `http://localhost:8765/jephter/callback` and `http://localhost:8765/emmanuel/callback` — port and path are both part of the match |
| Credentials | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in **this project's** `.env`, never ForChi's |

```powershell
python scripts\youtube_auth.py --workflow jephter  --serve      # browser, consent, token sealed at rest
python scripts\youtube_auth.py --workflow emmanuel --serve
python scripts\youtube_auth.py --selftest                       # encryption at rest actually seals
python scripts\youtube_upload.py --workflow jephter --check      # 1 quota unit, proves the channel back
```

**The fact that decides everything here:** while the app is in Testing, the refresh token **expires in 7
days**. `youtube.upload` is not exempt. Staying in Testing is a deliberate choice, and living with it means
the day-4/day-5 reminder has to work — Stage 10.

**Gate:** `--check` prints the channel's real name and id, and the token file records `obtained_at`. If
`credentials()` silently falls back to `C:\Users\hp\forchi\.env`, the client is in the wrong file.

---

## Stage 4 — the pages Google reads

```powershell
python scripts\build_site.py
python scripts\deploy_site.py
```

Privacy and terms, served over HTTP on the VPS until a domain exists. What they say is not decoration: if
the page claims the token is encrypted at rest, and that stops being true, the page changes in the same
commit that breaks it.

**Gate:** both URLs answer `200`, and the privacy page states the encryption the code actually performs.

---

## Stage 5 — footage and music in, then labelled

Media is git-ignored, one folder per channel, and every clip gets measured and labelled. The labels are what
let a script pick a clip that matches the line.

```powershell
python scripts\x_media.py --workflow jephter --label           # duration, motion, loudness per clip
python scripts\x_media.py --workflow jephter --label --vision  # subject + any baked-in text
python scripts\label_emotions.py --workflow jephter             # emotion + weather labels
python scripts\label_emotions.py --workflow jephter --table     # the human-readable table
```

Writes `config/scenes/<wf>.json`, `config/scenes/<wf>-labels.md`, `config/songs/<wf>.json`.

**Gate:** the manifest lists every file in the folder, no clip is `0.0s`, and the labels file has a
clip → emotion → weather row for each. A clip with baked-in lettering needs its rect, or the caption lands
on top of it.

---

## Stage 6 — the look

```powershell
python scripts\make_fonts.py     # Jost Bold from REZMO's own variable font
python scripts\make_assets.py    # the caption scrim
```

**Gate:** the font is bold, and a rendered frame shows the caption legibly over the darkest and the
lightest clip in the pool. Look at a frame — a file that exists is not a file that is right.

---

## Stage 7 — build one video by hand, before any schedule exists

```powershell
python scripts\x_script.py --workflow jephter             # the script and its ending guard
python scripts\x_voice.py  --workflow jephter --audition  # hear the voice before committing to it
python scripts\x_render.py --workflow jephter --deliver
```

Order matters: script, then voice, then cuts. `x_render.py` is the whole chain, and `--deliver` copies the
mp4 **with its report beside it** to `C:\Users\hp\Downloads` — that pairing is the owner's convention.

**Gate:** read `out/<wf>/<name>.json`. Do not trust the exit code.

| Field | Must be |
| :--- | :--- |
| `duration_s` / `under_cap` | ≤ 50, and `true` |
| `phrases` | every line passes `is_speech()` — no markdown, no trailing colon, no `(15)` word index |
| `dropped_phrases` | empty, or you know why |
| `song.offset` | different from the last three videos on that channel |
| `peak_db` | about −1 to −2.5, and the chain never used `loudnorm` |

---

## Stage 8 — the publish path, dry first

```powershell
python scripts\youtube_upload.py --workflow jephter --file out\jephter\<name>.mp4 --dry-run
python scripts\youtube_upload.py --workflow jephter --file out\jephter\<name>.mp4 --privacy public
```

The **claim is written before the upload**, keyed on `sha256(file)[:16]`, so a socket timeout after YouTube
accepted the file cannot become a second public video. Every write to `out/publish_ledger.json` is a locked
read-modify-write; a `--dry-run` leaves a `claimed` entry that blocks the real upload (`rc=3`) until it is
removed — the one-liner is in `x-publishing`.

**Public is the default.** A private upload reports success and the channel gets nothing; `private` needs
`--allow-private` on the same command line, in all three entry points.

**Gate:** the ledger's `published[]` entry shows the privacy **YouTube reported back**, and oEmbed resolves
the id — `https://www.youtube.com/oembed?url=https://youtu.be/<id>`. oEmbed only answers for a video the
public can see, which makes it the cheapest public check there is.

---

## Stage 9 — the schedule

```powershell
python scripts\x_scheduler.py --verify          # the config is sane and the slots are what you think
python scripts\x_scheduler.py --status          # what each slot has done today
python scripts\x_scheduler.py --once --dry-run  # walk a pass without uploading
```

Produce at 07:00 and 14:00, post at 10:xx and 17:xx with per-channel jitter, 2 a day each. Production is
separate from posting on purpose: a slow render or a TTS retry must not eat the post.

| Slot outcome | Meaning |
| :--- | :--- |
| `attempts` up, `posted: true` | done |
| `transient_at` set | the provider was busy; retried in `transient_retry_minutes` **without spending an attempt** |
| `3 attempts`, then nothing | that slot is dead for the day — the watchdog must be the thing that says so |

**Gate:** a `--dry-run` pass for both workflows prints `privacy=public` and a slot plan, with no error.

---

## Stage 10 — the fallbacks, and proof each rung fires

Nothing unattended may fail silently. Detail in `build-fallbacks` (shared library) and `x-watchdog`.

| Rung | Thing | The command that proves it |
| :--- | :--- | :--- |
| L0 retry | 5xx/429 walk the ladder, bounded, and a 5xx never cools a key | `python scripts\gemini_keys.py --audit`, and a render during a bad provider hour |
| L1 supervise | systemd `Type=oneshot`, timer every 15 min, `Persistent=true` | `systemctl show x-daily.timer -p NextElapseUSecRealtime` |
| L2 detect | log freshness, overdue slots, granted scope, nothing-posted-today | `python scripts\x_watchdog.py --dry-run` |
| L3 escalate | SMTP to `ALERT_EMAIL`, WHAT / WHY / TRIED / ACTION | `python scripts\x_watchdog.py --test-mail` |
| L4 dead-man | daily heartbeat, absence is the alarm | `python scripts\x_watchdog.py --heartbeat-hour 19` |
| reminder | day-4 and day-5 re-auth mail, per channel | `python scripts\x_watchdog.py --test-reminder` |

**The failure inside the failure:** the VPS could not send mail at all — no `SMTP_*` in its `.env`; the
laptop had been borrowing ForChi's. Every alert and both reminders would have been *"not sent"* on the one
machine with no other way to say so. Check mail **first** on a new host.

**Gate:** `--test-mail` prints `mail: sent to … via …:587`, `--test-reminder` prints the mail it would send,
and `--dry-run` says `healthy` **with a `mail:` line** in the summary.

---

## Stage 11 — put it on the host that will do the work

A laptop sleeps. `-WakeToRun` wakes a machine, not a closed lid, and a task with `DisallowStartIfOnBatteries`
is skipped with no error, no log and no counter anyone reads — that is how seven consecutive passes were
lost.

```powershell
python scripts\bootstrap_vps.py --check     # ffmpeg, fonts, python, then verify
python scripts\vps.py --check
python scripts\sync_media.py --verify       # laptop and host agree, CRLF-normalised
```

Then on the host: the `.venv`, `scripts/x_daily.sh` and `scripts/x_watchdog.sh`, the two units, `enable --now`,
and **disable the old host's tasks**. Two ledgers will double-post, and only one machine may hold the claim.
`x-vps-deploy` has the ordering rule and the traps (CRLF, `git pull` over uploaded files, what git cannot carry).

**Gate:**

```text
systemctl is-enabled x-daily.timer x-watchdog.timer   -> enabled enabled
OnCalendar=*:0/15   Persistent=true   Type=oneshot
python tests/run_tests.py                             -> 0 failed, on the host
```

---

## Stage 12 — the acceptance sweep

Run this before calling it built. Anything that fails is the next task.

```powershell
python tests\run_tests.py                          # the offline suite the workflow itself gates on
python scripts\gemini_keys.py --audit              # 9/9, disjoint
python scripts\youtube_upload.py --workflow jephter  --check
python scripts\youtube_upload.py --workflow emmanuel --check
python scripts\x_scheduler.py --verify
python scripts\x_watchdog.py --selftest            # day-4/day-5 rules, recipient order, escalation wording
python scripts\x_watchdog.py --test-mail
python scripts\sync_media.py --verify
```

On the host, additionally: `sh scripts/x_watchdog.sh` (exit 0, `healthy`), `systemctl list-timers`, and
`x_scheduler.py --status` showing today's four slots per channel.

**The one sentence this whole skill exists to earn:** *tomorrow at 07:00 the host produces video one, at 10
it posts it publicly, at 14:00 and 17:00 it does it again, and if any of that stops, the operator gets an
email that names what stopped.*

---

## The traps, consolidated

Each of these cost real time. None is a guess.

| Symptom | Cause | Do not |
| :--- | :--- | :--- |
| `ssh` exits 0 and prints nothing | `ssh` on PATH is a 0-byte file | use `C:\Windows\System32\OpenSSH\ssh.exe`, or `scripts\vps.py` |
| `Syntax error: Bad fd number` | CRLF in a `.sh` copied to Linux | `*.sh` is pinned `eol=lf` now; still `file` the script after a hand copy |
| *"no OAuth client found"* | the client is in ForChi's `.env`, not this project's | keep both ids in `X\.env`; a fallback that reads another project is a trap |
| A video is invisible but the run said success | `--privacy private` was the default | `public` is the default; `--allow-private` is required to hide one |
| A public title is a prompt fragment | `"target 85, max 95)."`, `"**Final Polish:**"`, `"(15) moments (16)…"` | the title is only taken from a line that passes `is_speech()`; never strip, reject |
| The same video uploaded twice | a stale ledger copy overwrote another run's claim | every write is locked and reloads from disk; edit the ledger through `mutate_ledger` |
| A task "just does not run" | battery/ACL conditions, or a sleeping laptop | run it on the VPS; check `Persistent=true` and `NextElapse` |
| Reminders never arrive | no `SMTP_*` on the host | `--test-mail` first, before anything else |
| TTS dies mid-build | quota is the tightest limit in the pipeline | let the pool demote and retry; a 429 is minutes, not a dead key |
| `x_render.py` exits 75 | the provider was busy — the transient exit | do not count it as an attempt; it is retried in 4 minutes |
| Two hosts both post | every machine keeps its own ledger | disable the old host's tasks **before** enabling the new one |

## What this skill does not cover

Anything about **ForChi**. It is a separate project with its own voice backend and writing formats; read
`x-forchi-bridge` before reaching into it, and never change it while shipping X.
