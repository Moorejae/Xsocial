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

Start-ScheduledTask 'X watchdog'; Get-ScheduledTaskInfo 'X watchdog'
Get-Content out\watchdog.log -Tail 5          # 'healthy' is a logged result, not an assumption
```

The rung above is still missing on purpose: nothing watches `out\watchdog.log` from **outside** this machine.
The heartbeat is the compromise — it is sent by the machine, so a total failure stops it, which is exactly the
case it exists to catch.
