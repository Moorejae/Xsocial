---
name: x-publishing
description: 'Automating the YouTube posting for jephter, emmanuel and ForChi: the one client with three channels (so each channel only has to sign in), the consent step and the redirect URI that must be registered, the resumable uploader, the ledger that stops a double post, the 100-a-day videos.insert bucket, and the two ways to schedule the daily run. Load this when publishing a short, when an upload fails or is refused as a duplicate, when invalid_grant appears after about a week, when a scheduled run does nothing, or when adding a fourth channel.'
---

# Publishing to YouTube

Three channels, **one OAuth client**. On 2026-11-01 both X channels' Google accounts were added to the
audience of the client ForChi already uses, so the console work is done once and each channel only has to
sign in with its own Google account.

> **Resilience is part of done.** The upload path claims the work in a ledger *before* the side effect and
> refuses a second upload of the same bytes. A retry with no ledger claim is how the pipeline this borrows
> from once published the same Short twice. See `build-fallbacks`.

## When to use

- Publishing a short, or wiring the daily run.
- An upload failed, or a "REFUSING: this file is already uploaded" message appeared.
- `invalid_grant` on token refresh.
- A scheduled run does nothing at all.
- Adding a fourth channel.

## The channels

| Workflow | Google account | Register | Notes |
| :--- | :--- | :--- | :--- |
| `jephter` | `chineduagu598@gmail.com` | confession / expressive | own refresh token |
| `emmanuel` | `steadfast7878@gmail.com` | devotion / instructional | own refresh token |
| `forchi` | `aguswigad@gmail.com` | both X formats, alternating | token lives in ForChi's own deployment |

Both new accounts were added to the **same audience** as ForChi's client, so `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` are ForChi's — read from `C:\Users\hp\forchi\.env` in place if this project's `.env`
does not carry them. Nothing is copied.

## The one console step still needed

ForChi's client is a **Web application**, so its authorised redirect URIs are an explicit list. Add both
loopback URIs to that client (APIs & Services → Credentials → the OAuth 2.0 Client ID → Authorized redirect
URIs):

```
http://localhost:8765/jephter/callback
http://localhost:8765/emmanuel/callback
```

Without them the consent screen fails with `redirect_uri_mismatch` — a console problem, not a code problem.
`http://localhost` **is** accepted for a Web client; a bare IP is not.

Then, once per channel, signed in as that channel's account:

```powershell
python scripts\youtube_auth.py --workflow jephter     # opens a browser
python scripts\youtube_auth.py --workflow emmanuel
```

Each run prints the **channel title it authorised**. Read it: consenting as the wrong Google account is the
easiest mistake here, and that title is the only proof consent landed on the right channel.

### The scope that has to be in the consent URL

`SCOPES` in `youtube_auth.py` is `youtube.upload` **+ `youtube.readonly`** + `openid` + `email`.

`youtube.upload` alone authorises `videos.insert` and nothing else — it cannot call `channels.list`. The
authoriser reads the channel back to prove which account consented, so without a read scope that call
returns **`HTTP 403 Forbidden`**, the callback page says "Not authorised", and the refresh token is thrown
away even though the exchange had already succeeded. That is exactly what happened on 2026-11-01 to both
channels.

Two rules now hold in `youtube_auth.py`, and both matter:

1. `youtube.readonly` stays in `SCOPES`. It is a *read* scope, so it does not widen what the pipeline can
   do, only what it can see.
2. **The token is saved before the read-back is attempted, and the read-back may fail.** `whoami_safe()`
   never raises. Evidence is not the deliverable; the token is. If a future scope change breaks
   `channels.list` again, the post still goes out and only the printed channel title is degraded.

When a consent link is regenerated, confirm the scope **in the URL** before opening it
(`scope=...youtube.upload+youtube.readonly...`). A link minted by an older process does not change: the
`--serve` listener must be restarted after editing `SCOPES`, or the owner will authorise an old scope and
hit the same 403.

## Publishing

```powershell
python scripts\youtube_upload.py --workflow jephter --check                     # credential only, uploads nothing
python scripts\youtube_upload.py --workflow jephter --file out\jephter\x.mp4 --dry-run
python scripts\youtube_upload.py --workflow jephter --file out\jephter\x.mp4 --privacy unlisted
python scripts\x_publish.py --workflow both --privacy public --render-missing   # the daily run
python scripts\x_publish.py --status                                            # what has gone out
```

`x_publish.py` is the scheduled entry point and is **idempotent**: if a video was published for that workflow
today it stops; if the newest render is not from today it refuses unless `--render-missing`; if the exact
bytes have already been uploaded it refuses.

## The safety properties, and why each exists

| Property | Why |
| :--- | :--- |
| **Claim before upload** | `out/publish_ledger.json` is written and flushed before the request. A socket timeout *after* YouTube accepted the file would otherwise produce a second public video on retry |
| **Hash, not filename** | the guard keys on `sha256(file)[:16]`. A re-render with identical bytes is the same video; a *different* file with the same name is not |
| **Private by default** | the failure being protected against is an accidental publish, so `--privacy public` must be asked for |
| **`--check` uploads nothing** | proving a credential is `channels.list(mine=true)`: 1 quota unit, zero side effects |
| **Duration re-check before upload** | publishing is irreversible; a 62 s file must be caught by `x_publish` (which knows the 50 s cap) rather than by the owner afterwards |
| **Resumable upload** | a 20 MB file over a flaky link fails half the time otherwise, and a failed plain upload can leave a half-processed video |

## Quota — the number that decides the schedule

`videos.insert` has its **own bucket: 100 calls per project per day** (Google's quota calculator, revision
2026-10-15), separate from the 10,000 units/day that everything else shares.

Three channels in one project therefore share **100 uploads a day**, not 100 each. At one video per channel
per day that is 3 of 100 — ample. It only matters if a retry loop or a backfill is ever written.

## The daily schedule — production apart from posting

Modelled on ForChi's `slots.js` + `scheduler.js`, with two deliberate differences. Configuration lives in
`config/workflows.json` → `schedule`, one source of truth:

```json
"schedule": {
  "timezone": "Africa/Lagos",
  "daily_count": 2,
  "produce_hours": [7, 14],
  "post_hours": [10, 17],
  "jitter_base_minutes": 0,          // 25 for emmanuel
  "jitter_day_span_minutes": 15,
  "max_attempts_per_slot": 3,
  "retry_minutes": [10, 25]
}
```

| ForChi | Here | Why the difference |
| :--- | :--- | :--- |
| renders *at* the posting slot | **produce at 07:00 / 14:00, post at 10:00 / 17:00** | a slow render or a TTS retry cannot eat the posting time |
| fixed slots, no jitter | fixed slots **plus per-channel jitter** | the owner asked for the channels to post at different times: jephter +0-15 min, emmanuel +25-40 min, so they are ≥10 minutes apart by construction and still vary day to day |
| 2/day, one channel | **2/day per channel** | four uploads a day across three channels is 4 of the 100 `videos.insert` calls a day |

Jitter is **deterministic** per `(channel, day, slot)` — `sha256` of that tuple. A random offset would move
the slot every time the scheduler woke and "did we post at 10:12?" would have no answer.

Same-slot retries are kept: 3 attempts with 10 then 25 minute backoff, so one transient failure does not
push the next attempt to the following slot ~7 hours later.

```powershell
python scripts\x_scheduler.py --verify          # today's slots and the separation between them
python scripts\x_scheduler.py --status          # produced / posted / attempts, per slot
python scripts\x_scheduler.py --once            # one pass: whatever is due
python scripts\x_scheduler.py --daemon          # loop, waking every 5 minutes
```

**A missing credential is a gate, not a failure.** `credential_ready()` checks for a refresh token with no
network call; if it is absent the posting slot is marked `gated` and **no attempt is consumed**. ForChi
learned the neighbouring lesson the expensive way — `authGate.youtubeReady()` runs before build *and*
publish, because six builds once succeeded and every publish then failed.

## Scheduling — two ways, pick one

**A. Windows Task Scheduler** (simplest: the clip libraries and `out/` live on this workstation). Trigger
the scheduler rather than `x_publish`, so production and posting both happen on time:

```powershell
$action  = New-ScheduledTaskAction -Execute 'python' `
  -Argument 'C:\Users\hp\Downloads\X\scripts\x_scheduler.py --once --privacy public' `
  -WorkingDirectory 'C:\Users\hp\Downloads\X'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'X scheduler' -Action $action -Trigger $trigger `
  -Description 'Produces at 07:00/14:00 and posts at 10:00/17:00 with per-channel jitter'
```

Every 15 minutes, `--once` does nothing until a slot is due. That is what makes a missed trigger
self-healing rather than a hole in the schedule. `--daemon` is the alternative if a task that never exits is
preferred; a chatty loop is easier to watch, an external trigger is easier to see failing.

**B. systemd timer on the VPS** — only once the media is synced there (`bootstrap_vps.py` prepares the box
but does not copy the libraries). Then a `*.timer` with `OnCalendar=*:0/15` calling the same `--once`.

## Failure catalogue

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `redirect_uri_mismatch` | the loopback URI is not registered on the client | add it in the console (§above) |
| callback page says **Not authorised**, body `HTTPError: HTTP Error 403: Forbidden`, on *both* channels | `whoami()` called `channels.list` with a token that only had `youtube.upload` | keep `youtube.readonly` in `SCOPES`, restart `--serve`, re-open the fresh links. The token may already have been saved by `whoami_safe()` — run `--check` before redoing consent |
| `invalid_grant` on refresh | consent revoked, or the app is in **Testing** and the 7-day refresh token expired | publish the app; re-run `youtube_auth.py` |
| `REFUSING: this file is already uploaded` | the ledger has a claim for those bytes | correct — check `--status`; `--force` only if you know it did not land |
| `no render for today` | nothing was built today | add `--render-missing`, or run `x_render.py` first |
| `over the 50s cap — not publishing` | a manual render with a longer script | re-render; the cap comes from `config/workflows.json` |
| consent succeeded but the wrong channel appears | signed in as the wrong Google account | revoke at `myaccount.google.com/permissions`, redo |
| `forchi: no refresh token in its .env or its jobs database` | ForChi's token lives in its **deployment**, not this workstation | set `YOUTUBE_REFRESH_TOKEN` in ForChi's `.env`, or run `youtube_auth.py --workflow forchi` |

## Verification

```powershell
python scripts\youtube_upload.py --selftest                     # no network, no credential
python scripts\youtube_upload.py --workflow jephter --check     # proves the credential
python scripts\x_publish.py --workflow both --dry-run           # everything except the upload
python scripts\x_publish.py --status                            # what has gone out
```

Run the selftest after any change here: it proves the title/description builder, the hash, and that a claim
blocks a second upload of the same file.
