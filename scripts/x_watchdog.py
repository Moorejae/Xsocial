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


REMINDERS = ROOT / "out" / "auth_reminders.json"


def token_day(obtained_at: str, now: datetime) -> int | None:
    """Which day of its 7-day life the refresh token is on — day 1 is the day it was granted."""
    try:
        got = datetime.fromisoformat(str(obtained_at))
    except (TypeError, ValueError):
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=now.tzinfo)
    delta = (now.date() - got.date()).days
    return delta + 1 if delta >= 0 else None


def load_reminders(path: Path = REMINDERS) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def reminder_due(workflow: str, obtained_at: str, now: datetime, days: tuple[int, ...],
                 sent: dict | None = None) -> int | None:
    """The day number whose reminder is owed, or None.

    Keyed on the token's own `obtained_at`, so a re-consent resets the schedule by itself — the sent
    markers belong to the old grant and are simply ignored.
    """
    day = token_day(obtained_at, now)
    if day is None or day not in days:
        return None
    already = (sent or {}).get(workflow) or {}
    if already.get("obtained_at") == obtained_at and day in (already.get("days") or []):
        return None
    return day


def mark_reminded(workflow: str, obtained_at: str, day: int, path: Path = REMINDERS) -> None:
    data = load_reminders(path)
    entry = data.get(workflow) or {}
    if entry.get("obtained_at") != obtained_at:
        entry = {"obtained_at": obtained_at, "days": []}
    entry.setdefault("days", [])
    if day not in entry["days"]:
        entry["days"].append(day)
    data[workflow] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def reminder_message(workflow: str, day: int, link: str, expires_on: str,
                     final: bool = False) -> tuple[str, str]:
    """The re-authorisation mail.

    Addressed to the operator (the owner's own inbox, same as ForChi's), who then runs the pasted-URL
    exchange himself. It still explains the loopback step in full, because the link has to be opened
    signed in as the right channel and the browser WILL end on an error page — that is the part that
    looks broken and is not.

    `final` marks the last reminder of the two, so the mail that matters most does not arrive looking
    identical to the one before it.
    """
    name = {"jephter": "Jephter", "emmanuel": "Emmanuel"}.get(workflow, workflow.title())
    channel = {"jephter": "Chinedu Jephtah", "emmanuel": "Brown Cinema"}.get(workflow, name)
    if final:
        subject = f"Reminder: the {name} YouTube channel stops publishing in 2 days"
        opening = ("This is the second and last reminder. Without one sign-in the {n} channel stops\n"
                   "publishing short videos on {d}.\n\n").format(n=name, d=expires_on)
    else:
        subject = f"Action needed: reconnect the {name} YouTube channel"
        opening = ("The Google permission that lets the {n} channel publish short videos expires on\n"
                   "{d}. It has to be renewed by signing in once — it takes about a minute.\n\n"
                   ).format(n=name, d=expires_on)
    body = (
        f"Hello,\n\n"
        f"{opening}"
        f"WHAT TO DO\n"
        f"  1. Open the link below, signed in as the Google account that owns the {name}\n"
        f"     channel ({channel}):\n\n"
        f"{link}\n\n"
        f"  2. Choose that account. On the consent screen, KEEP the YouTube box ticked — without it\n"
        f"     the tool can read the channel but cannot upload, which looks fine and then fails.\n\n"
        f"  3. Press Continue. The browser will then show an error page (\"site can't be reached\",\n"
        f"     \"localhost refused to connect\" — the wording varies). THAT IS EXPECTED. The page is\n"
        f"     not meant to load.\n\n"
        f"  4. Copy the FULL address from the browser's address bar and run:\n\n"
        f"       python scripts\\youtube_auth.py --workflow {workflow} --from-url \"<that url>\"\n\n"
        f"     It looks like:\n\n"
        f"       http://localhost:8765/{workflow}/callback?state=...&code=4/0A...\n\n"
        f"     That whole line is what is needed. Do not shorten it.\n\n"
        f"WHY IT WORKS THIS WAY\n"
        f"  The tool receives the approval on the machine it runs on, so the browser has to hand the\n"
        f"  approval back there. Pasting the address is how that hand-off is done: the code is\n"
        f"  short-lived and grants only this one channel.\n\n"
        f"Nothing else about the channel changes: the videos already published stay published.\n\n"
        f"— {name} publishing tool\n"
    )
    return subject, body


def reminder_html(text: str, link: str) -> str:
    """The HTML half of the reminder: a real button, plus the bare link as a fallback.

    ForChi learned this the expensive way (recorded 2026-10-22): a long consent URL pasted into a
    plain-text mail is not reliably auto-linkified by mail clients, the recipient never clicked it,
    and posting stalled for days. So every auth mail here carries a real `<a>` button **and** the
    raw URL, because a button that fails silently is worse than no button at all.
    """
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    href = esc(link)
    para = "".join(
        f'<p style="margin:0 0 12px">{esc(line)}</p>' if line.strip() else ""
        for line in text.split("\n")
    )
    # The plain part already shows the URL; in the HTML it becomes the button and the copy line.
    para = para.replace(f'<p style="margin:0 0 12px">{esc(link)}</p>', "")
    return (
        '<!doctype html><html><body style="margin:0;padding:24px;color:#202124;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.55">'
        f"{para}"
        f'<p style="margin:26px 0 18px"><a href="{href}" style="background:#1a73e8;'
        "border-radius:8px;color:#ffffff;display:inline-block;font-weight:700;"
        'padding:14px 24px;text-decoration:none">Authorize YouTube →</a></p>'
        '<p style="margin:0;color:#5f6368;font-size:12px">Button not working? Copy this link:<br>'
        f'<a href="{href}" style="color:#1a73e8;word-break:break-all">{esc(link)}</a></p>'
        "</body></html>"
    )


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


def send(subject: str, body: str, settings: dict[str, str], to: str,
         link: str = "") -> str:
    """Send one mail. When `link` is given, the message is multipart: plain text plus a button.

    The two halves are not optional politeness — see reminder_html(). A consent URL in a plain-text
    body is a URL the recipient may never click.
    """
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
    if link:
        msg.add_alternative(reminder_html(body, link), subtype="html")
    with smtplib.SMTP(host, port, timeout=45) as s:
        s.ehlo()
        if port != 25:
            s.starttls()
            s.ehlo()
        s.login(user, password)
        s.send_message(msg)
    return f"sent to {to} via {host}:{port}"


def reminder_recipient(workflow: str, cfg: dict, override: str = "") -> str:
    """Who receives a re-authorisation mail.

    The operator's own inbox by default — `reminder_email`, falling back to `contact_email`.
    `channel_email` is the client's address and is deliberately last: the owner asked (2026-11-01)
    to receive these himself, which is also what ForChi does — its `EMAIL_TO` is the same address.
    """
    wf = cfg.get(workflow) or {}
    return override or wf.get("reminder_email") or wf.get("contact_email") or wf.get("channel_email") or ""


def reminders_possible(now: datetime, days: tuple[int, ...]) -> list[str]:
    """Workflows whose reminder day is today — decided from local files only, no network.

    This exists so the daily-API-call budget is honest: the watchdog runs every 30 minutes, and
    asking Google about token expiry on all 48 passes would be pointless. Reading `obtained_at` and
    comparing a date costs nothing, so the network is touched only on the two days that matter.
    """
    out: list[str] = []
    sent = load_reminders()
    for wf in ("jephter", "emmanuel"):
        try:
            import youtube_auth as ya
            obtained = ya.load(wf).get("obtained_at", "")
        except Exception:                                     # noqa: BLE001 - no credential, no reminder
            continue
        if reminder_due(wf, obtained, now, days, sent) is not None:
            out.append(wf)
    return out


def run_reminders(now: datetime, days: tuple[int, ...], settings: dict[str, str],
                  tokens: dict, dry_run: bool, override_to: str = "") -> list[str]:
    """Day-4 and day-5 re-authorisation mails, one per channel per day, to the operator's own inbox.

    Only while the token is actually expiring. Once the OAuth app is published the reminder has no
    subject matter, so silence is correct — not a missed notification.

    A failed send is reported back so the caller can escalate it: the reminder is the mechanism that
    keeps a Testing app alive, so a reminder that did not go out is a real problem, unlike the expiry
    itself, which is expected.
    """
    notes: list[str] = []
    try:
        cfg = json.loads((ROOT / "config" / "workflows.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["config/workflows.json unreadable"]
    sent = load_reminders()
    for wf in ("jephter", "emmanuel"):
        if (tokens.get(wf) or {}).get("kind") != "testing":
            continue
        try:
            import youtube_auth as ya
            obtained = ya.load(wf).get("obtained_at", "")
            cid, _ = ya.credentials(wf)
        except Exception as exc:                              # noqa: BLE001
            notes.append(f"{wf}: cannot build a consent link ({type(exc).__name__})")
            continue
        day = reminder_due(wf, obtained, now, days, sent)
        if day is None:
            continue
        link = ya.consent_url(wf, cid)
        expires = (datetime.fromisoformat(obtained) + timedelta(days=7)).strftime("%d %B %Y")
        # The last day in --remind-days is the one the owner waits for; it is worded as final.
        subject, body = reminder_message(wf, day, link, expires, final=(day == max(days)))
        to = reminder_recipient(wf, cfg, override_to)
        if not to:
            notes.append(f"{wf}: no reminder_email / contact_email in config/workflows.json")
            continue
        if dry_run:
            notes.append(f"{wf}: day {day} reminder WOULD go to {to} — \"{subject}\"")
            notes.append("  " + link)
            continue
        result = send(subject, body, settings, to, link=link)
        notes.append(f"{wf}: day {day} reminder — {result}")
        if result.startswith("sent"):
            mark_reminded(wf, obtained, day)
    return notes


def reminder_failures(notes: list[str]) -> list[str]:
    """The reminder notes that represent a real failure, for the alert.

    A reminder that could not be sent is the one genuine emergency in a Testing-only setup: it is the
    only thing that renews the token. The expiry itself is routine; the mail not leaving is not.
    """
    return [n for n in notes
            if "not sent" in n or "cannot build" in n or "no reminder_email" in n]


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

        # --- the re-authorisation reminders: day 4 and day 5 only, and only once each -----------
        obtained = "2026-11-01T15:08:26+0100"

        def at(day: int) -> datetime:
            return datetime(2026, 11, day, 19, 0, tzinfo=now.tzinfo)

        assert token_day(obtained, at(1)) == 1, token_day(obtained, at(1))
        assert token_day(obtained, at(5)) == 5
        print("ok   token_day counts the consent day as day 1")

        days = (4, 5)
        assert reminder_due("jephter", obtained, at(3), days, {}) is None
        assert reminder_due("jephter", obtained, at(4), days, {}) == 4
        assert reminder_due("jephter", obtained, at(5), days, {}) == 5
        assert reminder_due("jephter", obtained, at(6), days, {}) is None
        print("ok   a reminder is owed on day 4 and day 5, and on no other day")

        mark = Path(td) / "auth_reminders.json"
        mark_reminded("jephter", obtained, 4, mark)
        sent = load_reminders(mark)
        assert reminder_due("jephter", obtained, at(4), days, sent) is None, "day 4 must not repeat"
        assert reminder_due("jephter", obtained, at(5), days, sent) == 5, "day 5 is still owed"
        print("ok   each reminder is sent once per grant")

        # A re-consent produces a new obtained_at, so the old markers stop applying by themselves.
        newer = "2026-11-03T09:00:00+0100"
        assert reminder_due("jephter", newer, at(4), days, sent) is None, "day 4 of the NEW grant is day 2"
        print("ok   a fresh consent restarts the schedule instead of replaying old sends")

        # Reminders stop being owed once the app is published: day 5 with a non-dry run must skip.
        skipped = run_reminders(at(5), days, {}, {"jephter": {"kind": "ok", "days": None},
                                                  "emmanuel": {"kind": "ok", "days": None}},
                                dry_run=True)
        assert skipped == [], skipped
        print("ok   nothing is reminded once the app is published (tokens stop expiring)")

        subject, body = reminder_message("jephter", 4, "https://example.invalid/consent", "08 November 2026")
        for needle in ("localhost:8765/jephter/callback", "address bar", "YouTube box"):
            assert needle in body, needle
        print("ok   the reminder explains the loopback trap instead of assuming a technical reader")

        first_subject, first_body = reminder_message("jephter", 4, "L", "08 November 2026")
        last_subject, last_body = reminder_message("jephter", 5, "L", "08 November 2026", final=True)
        assert "Action needed" in first_subject, first_subject
        assert "stops publishing" in last_subject, last_subject
        assert "second and last reminder" in last_body, last_body[:80]
        assert "second and last reminder" in last_body, last_body[:80]
        assert "day" not in first_subject.lower(), "a client should not see internal day counting"
        print("ok   the day-5 mail reads as the final one, not a copy of the day-4 mail")

        # The HTML half: a real button, plus the raw URL as a fallback. ForChi lost days on this.
        link = "https://accounts.google.com/o/oauth2/v2/auth?client_id=a&code_note=ok&state=x"
        html = reminder_html(body, link)
        escaped = link.replace("&", "&amp;")       # href must be entity-escaped to stay valid HTML
        assert f'<a href="{escaped}"' in html, "the consent URL must be a real anchor"
        assert "Authorize YouTube" in html, "the button label is the thing that gets clicked"
        assert "Button not working?" in html and escaped in html, "the bare URL must survive as fallback"
        assert "&amp;amp;" not in html, "escaping must not happen twice"
        print("ok   the mail carries a real button and the raw URL beside it")

        # Recipient order: the operator's own inbox first, the client's address last.
        cfg_probe = {"jephter": {"reminder_email": "owner@example.com",
                                 "contact_email": "contact@example.com",
                                 "channel_email": "client@example.com"}}
        assert reminder_recipient("jephter", cfg_probe) == "owner@example.com"
        cfg_probe["jephter"].pop("reminder_email")
        assert reminder_recipient("jephter", cfg_probe) == "contact@example.com"
        cfg_probe["jephter"].pop("contact_email")
        assert reminder_recipient("jephter", cfg_probe) == "client@example.com"
        assert reminder_recipient("jephter", cfg_probe, "override@example.com") == "override@example.com"
        print("ok   reminders go to reminder_email, then contact_email, and only then the client")

        # A reminder that could not be sent is the one real emergency in a Testing-only setup, while
        # the expiry itself is routine — so only the failure may escalate.
        assert reminder_failures(["jephter: day 4 reminder — sent to a@b via smtp:587"]) == []
        assert reminder_failures(["jephter: day 4 reminder — not sent: SMTP settings are incomplete"])
        assert reminder_failures(["emmanuel: cannot build a consent link (SystemExit)"])
        print("ok   a failed reminder escalates; a routine expiry does not")
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
    ap.add_argument("--remind-days", "--reminder-days", dest="reminder_days", default="4,5",
                    help="days of the token's 7-day life on which to mail the owner (default 4,5)")
    ap.add_argument("--remind-after-hour", type=int, default=9,
                    help="do not mail before this hour (default 9 — a reminder at 00:20 is the first "
                         "pass of the day, and nobody wants that email then)")
    ap.add_argument("--no-reminders", action="store_true", help="skip the re-authorisation reminders")
    ap.add_argument("--remind-to", default="",
                    help="send reminders to this address instead of reminder_email (for testing)")
    ap.add_argument("--test-reminder", action="store_true",
                    help="build and send one reminder now, ignoring the day rule")
    ap.add_argument("--test-reminder-workflow", default="jephter",
                    choices=("jephter", "emmanuel"),
                    help="which channel's link to put in the test mail")
    ap.add_argument("--selftest", action="store_true", help="no log, no mail")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    now = datetime.now(ZoneInfo("Africa/Lagos"))
    trim_log(LOG)
    trim_log(WATCH_LOG)

    try:
        reminder_days = tuple(int(d) for d in str(a.reminder_days).replace(" ", "").split(",") if d)
    except ValueError:
        ap.error("--reminder-days wants day numbers, e.g. 4,5")

    # One network call a day per channel — plus the two reminder days — so the watchdog stays a
    # local check on almost every pass.
    hb_due = (not a.no_heartbeat and heartbeat_due(now, a.heartbeat_hour)) or a.force_token_check
    due = [] if (a.no_reminders or now.hour < a.remind_after_hour) else reminders_possible(now, reminder_days)
    need = set(due) | ({"jephter", "emmanuel"} if hb_due or a.test_reminder else set())
    tokens = {wf: token_status(wf) for wf in sorted(need)}

    problems = [] if a.test_mail else check(now, a.max_age, a.deadline_hour)
    for wf, t in tokens.items():
        if t["kind"] == "error":
            problems.append(f"{wf}: token check failed ({t['detail']})")
        # `kind == "testing"` is deliberately NOT a problem. The owner keeps the app in Testing by
        # decision (2026-11-01), so the 7-day cycle is expected and the reminders are the mechanism.
        # Reporting it here would email every 30 minutes, for ever, about something that is working
        # as designed — and an alarm that always fires is an alarm nobody reads.
        # The expiry is stated in the daily heartbeat instead, and a *failed reminder* is a problem.
    subject, body = build_message(problems, now)
    if a.test_mail:
        subject = "[X] TEST — watchdog alarm (deliberate)"
        body = ("This is the deliberate test of the X watchdog. If you are reading it, the alarm "
                "path works: a real problem would arrive in this shape, naming WHAT is wrong, why "
                "it matters, what was already tried, and the commands to run.\n\n" + body)

    print(f"  {len(problems)} problem(s)" if problems else "  healthy")
    for p in problems:
        print(f"    - {p}")
    for wf, t in tokens.items():
        if t["kind"] == "testing":
            print(f"    note {wf}: refresh token has {t['days']:.0f} day(s) left (Testing — reminders on "
                  f"day {a.reminder_days})")
        elif t["kind"] == "ok":
            print(f"    note {wf}: refresh token does not expire")

    settings, to = mail_settings()

    if a.test_reminder:
        import youtube_auth as ya
        wf = a.test_reminder_workflow
        obtained = ya.load(wf).get("obtained_at", "")
        cid, _ = ya.credentials(wf)
        link = ya.consent_url(wf, cid)
        expires = ((datetime.fromisoformat(obtained) + timedelta(days=7)).strftime("%d %B %Y")
                   if obtained else "7 days from consent")
        subject, body = reminder_message(wf, 0, link, expires)
        subject = "[TEST] " + subject
        try:
            cfg = json.loads((ROOT / "config" / "workflows.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        target = reminder_recipient(wf, cfg, a.remind_to) or to
        if a.dry_run:
            print(f"--- test reminder, would go to {target} ---")
            print(body)
            return 0
        result = send(subject, body, settings, target, link=link)
        print(f"  test reminder: {result}")
        return 0 if result.startswith("sent") else 1

    if a.dry_run:
        print("\n--- would send ---")
        print(body)
        if not a.no_heartbeat and heartbeat_due(now, a.heartbeat_hour):
            print(f"--- plus the daily heartbeat (after {a.heartbeat_hour:02d}:00) ---")
            print(summarise(now, tokens))
        if not a.no_reminders:
            notes = run_reminders(now, reminder_days, settings, tokens, dry_run=True,
                                  override_to=a.remind_to)
            print(f"--- re-authorisation reminders (days {','.join(map(str, reminder_days))}, "
                  f"after {a.remind_after_hour:02d}:00) ---")
            print("\n".join(f"  {n}" for n in notes) if notes else "  none owed today")
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

    if not a.no_reminders and now.hour >= a.remind_after_hour:
        notes = run_reminders(now, reminder_days, settings, tokens, dry_run=False,
                              override_to=a.remind_to)
        for note in notes:
            print(f"  reminder: {note}")
        failures = reminder_failures(notes)
        if failures:
            # The reminder IS the renewal mechanism for a Testing app, so a reminder that did not
            # leave is worth its own alert — separate from the routine expiry notice.
            rc = rc or 1
            alert = ("WHAT: a re-authorisation reminder could not be sent.\n\n"
                     + "\n".join(f"  - {f}" for f in failures)
                     + "\n\nWHY: the OAuth app is in Testing by design, so the weekly reminder is the only "
                       "thing that renews the token. If the mail never arrives, publishing stops on its own "
                       "with invalid_grant and nothing else reports it.\n\n"
                       "TRIED: one SMTP attempt via the configured host.\n\n"
                       "ACTION:\n"
                       "  1. python scripts\\x_watchdog.py --test-reminder --test-reminder-workflow jephter\n"
                       "  2. Check SMTP_HOST / SMTP_USER / SMTP_PASS and reminder_email in config.\n"
                       "  3. Re-run: python scripts\\x_watchdog.py --remind-days 4,5\n")
            sent = send(f"[X] re-authorisation mail FAILED — {now:%Y-%m-%d %H:%M}", alert, settings, to)
            print(f"  alert: {sent}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
