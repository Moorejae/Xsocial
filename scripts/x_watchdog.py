"""Watch the two channels' schedule from outside it, and escalate when something stops happening.

Why this exists
---------------
On 2026-11-01 the scheduled task had never run on time and nothing said so: Task Scheduler's
defaults skip a task on battery, and a pass that does not happen writes no log line. Errors
produce logs; **absence produces nothing**, and nothing is not something you notice. Seven
consecutive passes were lost that way. AGENTS.md §8 calls this rung L2 (detect) and L3 (escalate).

What it checks, all local, no API calls
--------------------------------------
1. `out/scheduler.log` has a pass newer than `--max-age` minutes (proves the timer still fires).
2. Every posting slot whose time has passed is either posted, gated by a known cause, or inside its
   retry window — anything else is overdue.
3. Each workflow's credential still carries `youtube.upload` (a re-consent can quietly lose it).
4. At `--deadline` hour (18:00 Lagos by default) something has actually been published today.

One email, one reason list. A watchdog that mails on every run is a watchdog people filter.

Usage
-----
    python scripts\\x_watchdog.py                # check; mail only if something is wrong
    python scripts\\x_watchdog.py --dry-run      # print the verdict, send nothing
    python scripts\\x_watchdog.py --test-mail    # prove the alarm can actually fire
    python scripts\\x_watchdog.py --selftest     # no log, no mail: parser and message builder only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import x_ffmpeg as xf  # noqa: E402

LOG = ROOT / "out" / "scheduler.log"
STATE = ROOT / "out" / "schedule_state.json"
WATCH_LOG = ROOT / "out" / "watchdog.log"
HEARTBEAT_MARK = ROOT / "out" / ".heartbeat"
PASS_RE = re.compile(r"^=== (?P<wf>\w+) · (?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (?P<tz>\S+) ===$")


def env_of(path: Path) -> dict[str, str]:
    """Parse a .env into a dict without ever echoing it."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def mail_settings() -> tuple[dict[str, str], str]:
    """SMTP settings for the alert mail: this project's .env, then ForChi's, as the credential chain does."""
    mine = env_of(ROOT / ".env")
    theirs = env_of(Path(os.path.expanduser("~")) / "forchi" / ".env")
    merged = {**{k: v for k, v in theirs.items() if k.startswith(("SMTP_", "EMAIL_"))}, **(mine)}
    merged.update({k: v for k, v in mine.items() if k.startswith(("SMTP_", "EMAIL_"))})
    to = mine.get("ALERT_EMAIL") or merged.get("EMAIL_TO") or ""
    return merged, to


def last_passes(text: str) -> dict[str, datetime]:
    """Newest `=== wf · stamp tz ===` header per workflow."""
    out: dict[str, datetime] = {}
    for line in text.splitlines():
        m = PASS_RE.match(line.strip())
        if not m:
            continue
        try:
            stamp = datetime.strptime(m.group("stamp"), "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        wf = m.group("wf")
        if wf not in out or stamp > out[wf]:
            out[wf] = stamp
    return out


def parse_log_age_minutes(text: str, now: datetime) -> float | None:
    """Minutes since the newest pass in the log. None when the log has no pass at all."""
    passes = last_passes(text)
    if not passes:
        return None
    newest = max(passes.values()).replace(tzinfo=now.tzinfo)
    return (now - newest).total_seconds() / 60.0


def check(now: datetime, max_age: float, deadline_hour: int) -> list[str]:
    """Every problem found, as one line each. An empty list means the run is healthy."""
    problems: list[str] = []

    if not LOG.exists():
        problems.append(f"no scheduler log at {LOG.relative_to(ROOT)} — the timer may never have run")
        age = None
    else:
        age = parse_log_age_minutes(LOG.read_text(encoding="utf-8", errors="replace"), now)
        if age is None:
            problems.append("the scheduler log has no completed pass in it")
        elif age > max_age:
            problems.append(f"the last scheduler pass was {age:.0f} min ago (limit {max_age:.0f}) "
                            f"— wake-ups are being skipped")

    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    try:
        cfg = json.loads((ROOT / "config" / "workflows.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}

    day = now.strftime("%Y-%m-%d")
    for wf in ("jephter", "emmanuel"):
        ws = state.get(wf) or {}
        posts_today = [k for k, v in ws.items()
                       if k.startswith(f"{day}:post:") and v.get("posted")]
        sched = (cfg.get(wf) or {}).get("schedule") or {}
        hours = sched.get("post_hours") or [10, 17]
        # A posting hour that has passed by more than the retry budget with nothing posted is overdue.
        overdue = []
        for idx, hour in enumerate(hours):
            slot_past = now.hour > hour or (now.hour == hour and now.minute > 45)
            if slot_past and len(posts_today) <= idx:
                overdue.append(f"{hour:02d}:xx")
        if overdue and problems == [] or (overdue and age is not None and age <= max_age):
            problems.append(f"{wf}: nothing posted for slot(s) {', '.join(overdue)} "
                            f"({len(posts_today)} posted today)")

        try:
            import youtube_upload as yt
            scopes = yt.granted_scopes(wf)
            if not scopes:
                problems.append(f"{wf}: no credential file — run youtube_auth.py --serve")
            elif "youtube.upload" not in scopes:
                problems.append(f"{wf}: consented without youtube.upload (granted {' '.join(scopes)})")
        except Exception as exc:                                  # noqa: BLE001 - never crash the watch
            problems.append(f"{wf}: credential check failed ({type(exc).__name__}: {exc})")

        if now.hour >= deadline_hour and not posts_today:
            problems.append(f"{wf}: nothing published at all today, and it is past {deadline_hour:02d}:00")

    return problems


def token_status(workflow: str) -> dict:
    """Is the refresh token about to expire? One network call, no upload, no log.

    An OAuth app left in **Testing** issues refresh tokens that die after 7 days, and nothing in the
    token file says so. The tell is `refresh_token_expires_in` in the refresh response: it is present
    only while the app is unpublished. Measured on 2026-11-01, both channels returned it, with 6 days
    left — which would have stopped the schedule with `invalid_grant` a week later, silently.
    """
    try:
        import youtube_auth as ya
        cred = ya.load(workflow)
        body = ya.post_form(ya.TOKEN_URI, {
            "client_id": cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type": "refresh_token",
        })
    except Exception as exc:                                  # noqa: BLE001 - report, never crash
        return {"kind": "error", "days": None, "detail": f"{type(exc).__name__}: {exc}"[:160]}
    if "refresh_token_expires_in" not in body:
        return {"kind": "ok", "days": None, "detail": "no refresh_token_expires_in (app not in Testing)"}
    try:
        days = int(body["refresh_token_expires_in"]) / 86400.0
    except (TypeError, ValueError):
        days = 0.0
    return {"kind": "testing", "days": days,
            "detail": "app is in Testing: publish it, or re-consent every 7 days"}


def summarise(now: datetime, tokens: dict | None = None) -> str:
    """Plain facts for the heartbeat: what posted today, and how fresh the timer is."""
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    day = now.strftime("%Y-%m-%d")
    lines = [f"X channels — {now:%Y-%m-%d %H:%M} Africa/Lagos", ""]
    for wf in ("jephter", "emmanuel"):
        ws = state.get(wf) or {}
        posted = [v for k, v in ws.items() if k.startswith(f"{day}:post:") and v.get("posted")]
        produced = [v for k, v in ws.items() if k.startswith(f"{day}:produce:") and v.get("done")]
        ids = [v.get("note", "").split("/")[-1] for v in posted if v.get("note")]
        lines.append(f"  {wf:9} produced {len(produced)}  posted {len(posted)}"
                     + (f"  ({', '.join(ids)})" if ids else ""))
        t = (tokens or {}).get(wf)
        if t:
            if t["kind"] == "testing":
                lines.append(f"  {wf:9} refresh token expires in {t['days']:.0f} day(s) "
                             f"— the OAuth app is still in Testing")
            elif t["kind"] == "error":
                lines.append(f"  {wf:9} token check failed: {t['detail']}")
            else:
                lines.append(f"  {wf:9} refresh token does not expire")
    try:
        text = LOG.read_text(encoding="utf-8", errors="replace")
        age = parse_log_age_minutes(text, now)
        lines.append("")
        lines.append(f"  scheduler: last pass {age:.0f} min ago" if age is not None
                     else "  scheduler: no pass in the log")
    except OSError:
        lines.append("  scheduler: no log")
    lines += ["", "This is the daily heartbeat. It is sent whether or not anything went wrong — its "
                  "absence means this workstation stopped running, which is the only failure a log "
                  "on the same machine cannot report."]
    return "\n".join(lines) + "\n"


def heartbeat_due(now: datetime, hour: int, mark: Path = HEARTBEAT_MARK) -> bool:
    """One heartbeat a day, after `hour`, tracked by a dated marker file."""
    if now.hour < hour:
        return False
    try:
        return mark.read_text(encoding="utf-8").strip() != now.strftime("%Y-%m-%d")
    except OSError:
        return True


def trim_log(path: Path, keep: int = 4000) -> None:
    """Keep the tail. Every pass appends, so an unbounded log is a slow leak."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) > keep:
        path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")


def build_message(problems: list[str], now: datetime) -> tuple[str, str]:
    """WHAT / WHY / TRIED / ACTION — the shape AGENTS.md §8 asks for."""
    subject = f"[X] {'OK' if not problems else 'schedule problem'} — {now.strftime('%Y-%m-%d %H:%M')} Lagos"
    if not problems:
        body = (f"Both channels are healthy as of {now:%Y-%m-%d %H:%M} (Africa/Lagos).\n\n"
                f"Scheduler: {LOG.name} has a recent pass.\n")
        return subject, body
    lines = [f"WHAT: {len(problems)} problem(s) on the X channels at {now:%Y-%m-%d %H:%M} Africa/Lagos.",
             ""]
    for p in problems:
        lines.append(f"  - {p}")
    lines += [
        "",
        "WHY: the schedule is the only thing that publishes these two channels. When it stops, "
        "no video appears and nothing else reports it — errors leave logs, silence leaves nothing.",
        "",
        "TRIED: nothing automatic beyond this check. Retries inside a slot are the scheduler's own "
        "(3 attempts, 10 then 25 minutes).",
        "",
        "ACTION:",
        "  1. Get-Content out\\scheduler.log -Tail 20",
        "  2. python scripts\\x_scheduler.py --status",
        "  3. python scripts\\x_scheduler.py --once --no-produce     # catch up by hand",
        "  4. If the log has no recent pass, check the task: "
        "Get-ScheduledTaskInfo 'X daily publish' (a laptop on battery used to skip it silently).",
        "  5. If a credential is the cause: python scripts\\youtube_auth.py --serve",
    ]
    return subject, "\n".join(lines) + "\n"


def send(subject: str, body: str, settings: dict[str, str], to: str) -> str:
    host = settings.get("SMTP_HOST")
    port = int(settings.get("SMTP_PORT") or 587)
    user = settings.get("SMTP_USER")
    password = settings.get("SMTP_PASS")
    sender = settings.get("EMAIL_FROM") or user
    if not (host and user and password and to):
        return "not sent: SMTP settings are incomplete (SMTP_HOST/USER/PASS + ALERT_EMAIL)"
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=45) as s:
        s.ehlo()
        if port != 25:
            s.starttls()
            s.ehlo()
        s.login(user, password)
        s.send_message(msg)
    return f"sent to {to} via {host}:{port}"


def selftest() -> int:
    """Prove the parser and the message builder without touching a log file or a mailbox."""
    now = datetime(2026, 11, 1, 16, 0, tzinfo=ZoneInfo("Africa/Lagos"))
    sample = ("=== jephter · 2026-11-01 15:45 Africa/Lagos ===\n  today complete\n"
              "=== emmanuel · 2026-11-01 15:45 Africa/Lagos ===\n  today complete\n")
    age = parse_log_age_minutes(sample, now)
    assert age is not None and 14.9 < age < 15.1, age
    print(f"ok   parses a pass header and its age ({age:.0f} min)")

    assert parse_log_age_minutes("", now) is None
    print("ok   an empty log has no pass, so it cannot look healthy")

    stale = "=== jephter · 2026-11-01 09:00 Africa/Lagos ===\n"
    age = parse_log_age_minutes(stale, now)
    assert age is not None and age > 45, age
    print(f"ok   a stale pass is measurable and exceeds the limit ({age:.0f} min)")

    subject, body = build_message(["jephter: nothing posted for slot(s) 10:xx"], now)
    for needle in ("WHAT:", "WHY:", "TRIED:", "ACTION:"):
        assert needle in body, needle
    assert "problem" in subject, subject
    print("ok   the escalation names WHAT / WHY / TRIED / ACTION")

    settings, to = mail_settings()
    have = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if settings.get(k)]
    print(f"ok   mail settings present: {', '.join(have) or 'none'} "
          f"| recipient configured: {'yes' if to else 'no'}")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mark = Path(td) / ".heartbeat"
        early = datetime(2026, 11, 1, 9, 0, tzinfo=now.tzinfo)
        late = datetime(2026, 11, 1, 19, 30, tzinfo=now.tzinfo)
        assert heartbeat_due(late, 19, mark) is True, "a fresh day has no marker, so one is due"
        mark.write_text("2026-11-01", encoding="utf-8")
        assert heartbeat_due(late, 19, mark) is False, "the marker stops a second heartbeat"
        assert heartbeat_due(early, 19, mark) is False, "nothing before the hour"
        print("ok   exactly one heartbeat a day, tracked by a dated marker")

        fat = Path(td) / "fat.log"
        fat.write_text("\n".join(f"line {i}" for i in range(50)) + "\n", encoding="utf-8")
        trim_log(fat, keep=10)
        kept = fat.read_text(encoding="utf-8").splitlines()
        assert len(kept) == 10 and kept[-1] == "line 49", kept[:2]
        print("ok   log trimming keeps the tail")
    return 0


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Watch the X schedule and escalate when it stops")
    ap.add_argument("--max-age", type=float, default=45.0,
                    help="minutes a pass may be old before the timer is suspect (default 45)")
    ap.add_argument("--deadline-hour", type=int, default=18,
                    help="after this hour, a channel with nothing posted today is a problem")
    ap.add_argument("--dry-run", action="store_true", help="print the verdict, send nothing")
    ap.add_argument("--test-mail", action="store_true",
                    help="send the alert once, on purpose, to prove the alarm fires")
    ap.add_argument("--heartbeat-hour", type=int, default=19,
                    help="send the daily heartbeat after this hour (default 19)")
    ap.add_argument("--no-heartbeat", action="store_true", help="skip the daily heartbeat")
    ap.add_argument("--force-token-check", action="store_true",
                    help="ask Google when the refresh tokens expire, even if the daily marker is set")
    ap.add_argument("--selftest", action="store_true", help="no log, no mail")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    now = datetime.now(ZoneInfo("Africa/Lagos"))
    trim_log(LOG)
    trim_log(WATCH_LOG)

    # One network call a day per channel, bounded to the heartbeat, so the watchdog stays a local
    # check on 47 of 48 passes.
    hb_due = (not a.no_heartbeat and heartbeat_due(now, a.heartbeat_hour)) or a.force_token_check
    tokens = {wf: token_status(wf) for wf in ("jephter", "emmanuel")} if hb_due else {}

    problems = [] if a.test_mail else check(now, a.max_age, a.deadline_hour)
    for wf, t in tokens.items():
        if t["kind"] == "error":
            problems.append(f"{wf}: token check failed ({t['detail']})")
        elif t["kind"] == "testing":
            # Not a threshold: while the app is unpublished, publishing WILL stop, and the fix is a
            # console action only the owner can take. Say it on every heartbeat until it is done.
            problems.append(f"{wf}: refresh token expires in {t['days']:.0f} day(s) — the OAuth app is in "
                            f"Testing, so publishing will stop with invalid_grant. Publish the app: "
                            f"Google Cloud console -> APIs & Services -> OAuth consent screen -> "
                            f"Publish app, then re-consent once.")
    subject, body = build_message(problems, now)
    if a.test_mail:
        subject = "[X] TEST — watchdog alarm (deliberate)"
        body = ("This is the deliberate test of the X watchdog. If you are reading it, the alarm "
                "path works: a real problem would arrive in this shape, naming WHAT is wrong, why "
                "it matters, what was already tried, and the commands to run.\n\n" + body)

    print(f"  {len(problems)} problem(s)" if problems else "  healthy")
    for p in problems:
        print(f"    - {p}")

    settings, to = mail_settings()
    if a.dry_run:
        print("\n--- would send ---")
        print(body)
        if not a.no_heartbeat and heartbeat_due(now, a.heartbeat_hour):
            print(f"--- plus the daily heartbeat (after {a.heartbeat_hour:02d}:00) ---")
            print(summarise(now, tokens))
        return 0

    rc = 0
    if problems or a.test_mail:
        result = send(subject, body, settings, to)
        print(f"  mail: {result}")
        rc = 0 if result.startswith("sent") else 1

    if not a.no_heartbeat and heartbeat_due(now, a.heartbeat_hour):
        hb = summarise(now, tokens)
        result = send(f"[X] heartbeat — {now:%Y-%m-%d}", hb, settings, to)
        print(f"  heartbeat: {result}")
        if result.startswith("sent"):
            HEARTBEAT_MARK.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
        else:
            # Not marking it means the next pass tries again; a heartbeat that silently fails to
            # send is worse than none, because the silence looks like a dead machine.
            rc = rc or 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
