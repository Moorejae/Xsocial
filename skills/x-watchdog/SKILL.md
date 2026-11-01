---
name: x-watchdog
description: 'The detect-and-escalate layer for the two X channels: why a scheduled job that stops running is invisible, how to tell a dead timer from a quiet day, and the alarm that has actually been observed firing. Use when a video did not appear, when asking whether the schedule is still alive, when adding an alert or heartbeat, when a Windows Task Scheduler job "just does not run", or when something must run unattended without failing silently.'
---

# The alarm, and the silence it exists to break

`scripts/x_watchdog.py` watches the two channels and emails `ALERT_EMAIL` when something has stopped
happening. It exists because of one measured failure: on 2026-11-01 the publishing task **had never run on
schedule** and nothing anywhere said so. `Get-ScheduledTaskInfo` reported `NumberOfMissedRuns: 7` while this
workstation was in continuous use. A failed run writes a log line; a run that never starts writes **nothing**,
and nothing is not something anyone notices.

The whole design follows from that: the watchdog does not trust "no errors". It requires positive evidence that
a pass happened recently, and it sends a **daily heartbeat** whose absence is itself the alarm.

## When to use

- A slot came and went and no video appeared.
- "Is the schedule still running?" — `x_scheduler.py --status` shows what the *ledger* thinks; the watchdog
  shows whether the *timer* is alive. They answer different questions.
- Adding any unattended job: point it at a `.cmd` that redirects into a log, or its failure will be invisible.
- A Windows Task Scheduler job that runs once and never again.

## The ladder, as built here

| Rung | Where | Evidence |
| :--- | :--- | :--- |
| **L0 retry** | inside a slot, 3 attempts, 10 then 25 min | `config/workflows.json` → `max_attempts_per_slot`, `retry_minutes` |
| **L1 supervise** | Windows Task Scheduler, every 15 min, `-StartWhenAvailable` | `Get-ScheduledTaskInfo` |
| **L2 detect** | `x_watchdog.py`: log freshness, overdue slots, credential scope, nothing-posted-today | `python scripts\x_watchdog.py --dry-run` |
| **L3 escalate** | SMTP to `ALERT_EMAIL`, WHAT / WHY / TRIED / ACTION | `--test-mail`, sent and received |
| **L4 dead-man** | daily heartbeat from `x_watchdog.py` (`--heartbeat-hour`, default 19) | `out\.heartbeat` holds the date it last sent |

## What the four checks catch

| Check | Catches |
| :--- | :--- |
| newest `=== wf · date time tz ===` header in `out\scheduler.log` older than `--max-age` (45 min) | the timer stopped firing at all — the original failure |
| a posting hour passed by 45 min with fewer posts than slots | a render that keeps failing, or a credential that gated every attempt |
| `youtube_upload.granted_scopes(wf)` lacks `youtube.upload` | a re-consent that came back without the YouTube permission — it looks healthy and cannot post |
| after `--deadline-hour` (18) nothing posted today | a whole day lost quietly |
| `refresh_token_expires_in` present in the refresh response (once a day) | **the OAuth app is still in Testing**, so both channels stop with `invalid_grant` in 7 days |

### The 7-day bomb, and the tell that needs no console

An OAuth app left in **Testing** issues refresh tokens that die after 7 days. Nothing in the stored token
file records that, and the console is a web page you have to remember to open. The measurable tell is in the
refresh response itself:

```powershell
python scripts\x_watchdog.py --force-token-check --dry-run
#   - jephter: refresh token expires in 7 day(s) — the OAuth app is in Testing, so publishing will stop
```

`refresh_token_expires_in` is present **only** while the app is unpublished. When it disappears, the app is
live and the tokens are permanent. Measured 2026-11-01: both channels returned it, six days before the
schedule would have died — found by accident while checking something else, which is precisely why it is now
a check.

The fix is a console action, not code: APIs & Services → OAuth consent screen → **Publish app**, then
re-consent each channel once (`youtube_auth.py --serve`) so the new token carries the longer life.

## Two rules this project paid for

**1. Never let a scheduled job's output vanish.** `scripts\x_daily.cmd` and `scripts\x_watchdog.cmd` exist only
to redirect:

```bat
cd /d "%~dp0.."
python "scripts\x_scheduler.py" --once --privacy public >> "out\scheduler.log" 2>&1
```

A task registered with `-Execute python` and no redirect discards stdout, so a traceback is written to nowhere.
An empty log after a manual `Start-ScheduledTask` is the tell that the action never started.

**2. Prove the alarm fires.** An alert nobody has seen arrive is a hope:

```powershell
python scripts\x_watchdog.py --test-mail     # a real email, in the real shape
```

Both paths have been observed working: the test alarm and the heartbeat both returned
`sent to … via smtp.gmail.com:587`, and `out\.heartbeat` then held the current date. Do **not** clear that
marker to "check again" — a heartbeat that sends twice a day trains the owner to ignore it.

## The re-authorisation mail (day 4 and day 5)

While the app is in Testing a token dies after 7 days, and the person who has to fix it is the **channel
owner**, not the operator. So the watchdog mails them, twice, with warning to spare:

| Day of the token's life | Who receives it | What it asks for |
| :--- | :--- | :--- |
| day 1 | nobody — granted today | — |
| **day 4** | `channel_email` in `config/workflows.json` | open a link, keep the YouTube box ticked, reply with the address bar |
| **day 5** | same | same, worded as the **final** reminder — "the channel stops publishing in 2 days" |
| day 6-7 | nobody — already asked twice | — |

The day-5 mail is deliberately not a copy of the day-4 mail: a duplicate is ignored exactly when it must not be.
The owner named the 5th as the day the links should land, and consent day + 4 = the 5th for a 1 Nov grant.

Rules that make it safe to leave running:

- **Day 1 is the consent day**, counted from `obtained_at` in the token file — not from a timer the
  watchdog keeps. A re-consent therefore resets the schedule by itself.
- **Once per grant per day.** `out\auth_reminders.json` records `{obtained_at, days[]}`; markers from an
  older grant are ignored rather than deleted, so a fresh consent cannot inherit a stale "already sent".
- **Nothing before 09:00** (`--remind-after-hour`). The task's first pass of the day is around 00:20 and
  nobody wants that email then.
- **Silent once the app is published.** The reminder only builds when the refresh response carries
  `refresh_token_expires_in`. After publishing, tokens stop expiring and there is nothing to remind about.
- **Free on 46 of 48 daily passes.** `reminders_possible()` decides from local files whether today is day
  4 or 5; only then is Google asked. Measured: a normal pass runs in **0.7 s** with no network call.

Recipients live in config, not in the script, because they are the client's addresses:

```json
"jephter":  { "contact_email": "…", "channel_email": "chineduagu598@gmail.com" },
"emmanuel": { "contact_email": "…", "channel_email": "steadfast7878@gmail.com" }
```

### The loopback trap the mail has to explain

`redirect_uri` is `http://localhost:8765/<channel>/callback`. A link emailed to a channel owner opens on
**their** computer, so Google redirects to a localhost that has no listener and the browser shows a
connection error. That is expected, and the **code is sitting in the address bar**. The mail says so in
plain words, step by step, and asks for that whole URL back.

The exchange is then:

```powershell
python scripts\youtube_auth.py --workflow jephter --from-url "http://localhost:8765/jephter/callback?state=...&code=4/0A..."
```

`--from-url` accepts a full URL or a bare code, rejects an `?error=` URL and a URL with no code, and
exchanges with the same `redirect_uri` the request used — which is what Google requires. Nothing but the
short-lived code ever travels.

```powershell
python scripts\x_watchdog.py --test-reminder --dry-run   # the exact mail, sends nothing
python scripts\x_watchdog.py --test-reminder             # send it to ALERT_EMAIL, never to a client
python scripts\x_watchdog.py --remind-to me@example.com --remind-days 4,5   # redirect for a real trial
```

Verify the schedule without waiting four days:

```powershell
python -c "import sys;sys.path.insert(0,'scripts');import x_watchdog as xw;from datetime import datetime;from zoneinfo import ZoneInfo;print([ (d, xw.reminders_possible(datetime(2026,11,d,19,0,tzinfo=ZoneInfo('Africa/Lagos')),(4,5))) for d in (3,4,5,6)])"
# 3 -> [], 4 -> ['jephter','emmanuel'], 5 -> ['jephter','emmanuel'], 6 -> []
```

**Remember to delete `out\auth_reminders.json` after a redirected trial**, or the day it was trialled will
look already-sent.

## Mail without a new secret

Settings are read from this project's `.env`, falling back to ForChi's (same chain as the YouTube credential):
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, and `ALERT_EMAIL` for the recipient. Nothing is
ever printed — `--selftest` reports *which settings are present*, never a value.

```powershell
python scripts\x_watchdog.py --selftest      # parser, message shape, heartbeat marker, log trimming, no network
python scripts\x_watchdog.py --dry-run       # the verdict and the exact body, sends nothing
python scripts\x_watchdog.py                 # what the task runs
python scripts\x_watchdog.py --max-age 90 --deadline-hour 20   # a quiet night, if that is wanted
```

## The failures this had to be written against

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| task runs once, then never | `DisallowStartIfOnBatteries = True` (the default) skips it on a laptop, silently | `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`, then verify by **running** it |
| `--dry-run` says healthy while nothing posts | the log is fresh because a pass ran and failed fast | read `out\scheduler.log`, not just its timestamp; `x_scheduler.py --status` names the failing slot |
| a heartbeat arrives every pass | the marker write failed and the code retried | the marker is written only after `send()` reports success; a missing marker means "not sent", not "sent twice" |
| two alerts for one incident | the watchdog alerts on every pass, by design | that is deliberate: a problem still open 30 minutes later is worth a second mail. Only the *heartbeat* is once a day |

## Verification

```powershell
python scripts\x_watchdog.py --selftest
# ok   parses a pass header and its age (15 min)
# ok   an empty log has no pass, so it cannot look healthy
# ok   a stale pass is measurable and exceeds the limit (420 min)
# ok   the escalation names WHAT / WHY / TRIED / ACTION
# ok   exactly one heartbeat a day, tracked by a dated marker
# ok   log trimming keeps the tail
# ok   token_day counts the consent day as day 1
# ok   a reminder is owed on day 4 and day 5, and on no other day
# ok   each reminder is sent once per grant
# ok   a fresh consent restarts the schedule instead of replaying old sends
# ok   nothing is reminded once the app is published (tokens stop expiring)
# ok   the reminder explains the loopback trap instead of assuming a technical reader

Start-ScheduledTask 'X watchdog'; Get-ScheduledTaskInfo 'X watchdog'
Get-Content out\watchdog.log -Tail 5          # 'healthy' is a logged result, not an assumption
```

The rung above is still missing on purpose: nothing watches `out\watchdog.log` from **outside** this machine.
The heartbeat is the compromise — it is sent by the machine, so a total failure stops it, which is exactly the
case it exists to catch.
