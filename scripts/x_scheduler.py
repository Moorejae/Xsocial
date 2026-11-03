#!/usr/bin/env python3
"""The daily scheduler: produce earlier, post in a jittered slot, twice a day per channel.

Modelled on ForChi's `src/workflows/video/slots.js` + `scheduler.js`, because that design has already
survived a production channel:

* **one source of truth for the slots** — `config/workflows.json` → `schedule`, never hard-coded twice;
* **fixed daily slots**, not a rolling gap, so "did today post?" has a yes/no answer;
* **same-slot retries** with a short backoff (3 attempts, 10 then 25 minutes) so one transient failure
  does not push the next attempt to the following slot, ~7 hours later;
* **a state file** so a missed slot is visible rather than silent;
* **a self-rescheduling loop**, plus a one-pass mode for an external timer.

Two things are different here, both owner requests (2026-11-01):

1. **Production is separate from posting.** Rendering happens at `produce_hours`; the posting slots only
   upload a video that already exists. ForChi renders *at* the slot, so a slow render or a TTS retry eats
   the posting time; this cannot.
2. **The two channels never post in the same minute.** Each has a jitter base (jephter 0, emmanuel 25
   minutes) plus a per-day offset, so the slots are separated by construction and still vary day to day.

    python scripts\\x_scheduler.py --verify            # show today's slots for both channels
    python scripts\\x_scheduler.py --status            # what has been produced and posted today
    python scripts\\x_scheduler.py --once              # one pass: produce and/or post what is due
    python scripts\\x_scheduler.py --daemon            # loop, sleeping until the next action
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"
STATE = ROOT / "out" / "schedule_state.json"
WORKFLOWS = ("jephter", "emmanuel")


# --------------------------------------------------------------------------------------
# config and state
# --------------------------------------------------------------------------------------

def load_config() -> dict:
    return {k: v for k, v in json.loads(CONFIG.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def schedule_of(wf: dict) -> dict:
    return wf.get("schedule") or {
        "timezone": "Africa/Lagos", "daily_count": 2,
        "produce_hours": [7, 14], "post_hours": [10, 17],
        "jitter_base_minutes": 0, "jitter_day_span_minutes": 15,
        "max_attempts_per_slot": 3, "retry_minutes": [10, 25],
    }


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# slots
# --------------------------------------------------------------------------------------

def jitter_minutes(workflow: str, day: str, slot_index: int, sched: dict) -> int:
    """Deterministic per (channel, day, slot).

    Deterministic so a re-run an hour later computes the SAME posting minute — a random jitter would
    move the slot every time the scheduler woke up, and "did we post at 10:12?" would have no answer.
    """
    span = int(sched.get("jitter_day_span_minutes", 15))
    base = int(sched.get("jitter_base_minutes", 0))
    if span <= 0:
        return base
    seed = f"{workflow}:{day}:{slot_index}".encode()
    return base + int(hashlib.sha256(seed).hexdigest(), 16) % (span + 1)


def slots_for(workflow: str, wf: dict, day: datetime) -> list[dict]:
    """Every produce and post slot for `day`, as aware datetimes in the configured zone."""
    sched = schedule_of(wf)
    tz = ZoneInfo(sched.get("timezone", "Africa/Lagos"))
    stamp = day.strftime("%Y-%m-%d")
    out: list[dict] = []

    for i, hour in enumerate(sched.get("produce_hours", [])[: int(sched.get("daily_count", 2))]):
        at = datetime(day.year, day.month, day.day, int(hour), 0, tzinfo=tz)
        out.append({"kind": "produce", "index": i, "at": at, "key": f"{stamp}:produce:{i}"})

    for i, hour in enumerate(sched.get("post_hours", [])[: int(sched.get("daily_count", 2))]):
        at = datetime(day.year, day.month, day.day, int(hour), 0, tzinfo=tz)
        at = at + timedelta(minutes=jitter_minutes(workflow, stamp, i, sched))
        out.append({"kind": "post", "index": i, "at": at, "key": f"{stamp}:post:{i}"})

    return out


def fmt(at: datetime) -> str:
    return at.strftime("%H:%M")


# --------------------------------------------------------------------------------------
# work
# --------------------------------------------------------------------------------------

def rendered_for_slot(workflow: str, wf: dict, slot: dict) -> Path | None:
    """The video a produce slot made, if it is still there."""
    entry = load_state().get(workflow, {}).get(slot["key"], {})
    path = entry.get("file")
    if path and (ROOT / path).exists():
        return ROOT / path
    return None


def newest_unposted(workflow: str, wf: dict) -> Path | None:
    """A render from today that the ledger has not seen. Falls back to the newest file."""
    out_dir = ROOT / wf.get("output_dir", f"out/{workflow}")
    if not out_dir.exists():
        return None
    sys.path.insert(0, str(ROOT / "scripts"))
    import youtube_upload as yt

    ledger = yt.load_ledger()
    published = ledger.get("published_by_hash", {})
    today = datetime.now().strftime("%Y%m%d")
    candidates = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        key = f"{workflow}:{yt.hash_file(path)}"
        if key in published:
            continue
        if today in path.name or datetime.fromtimestamp(path.stat().st_mtime).date() == datetime.now().date():
            return path
    return None


def produce(workflow: str, wf: dict, slot: dict, seed: int | None) -> Path:
    cmd = [sys.executable, str(ROOT / "scripts" / "x_render.py"), "--workflow", workflow]
    if seed is not None:
        cmd += ["--seed", str(seed + slot["index"])]
    print(f"    produce: {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(cmd[1:]))}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    for line in (r.stdout or "").strip().splitlines()[-2:]:
        print(f"      {line}")
    if r.returncode == 75:
        # EX_TEMPFAIL: the provider was busy. Distinguished from a real failure so the caller can
        # retry soon without spending one of the slot's three attempts.
        raise TransientRender(meaningful_error(r.stderr) or "provider busy")
    if r.returncode != 0:
        raise RuntimeError(f"render failed rc={r.returncode}: {meaningful_error(r.stderr)}")
    out_dir = ROOT / wf.get("output_dir", f"out/{workflow}")
    files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError("render reported success but produced no mp4")
    return files[0]


class TransientRender(RuntimeError):
    """A render that failed for a reason that will probably be gone in a few minutes."""


def meaningful_error(stderr: str, limit: int = 200) -> str:
    """The line worth reading, not the tail of a traceback.

    The old version took `stderr[-400:]`, which for a Python traceback is the *last* frames — file
    names and `line 321` — with the actual message already cut off. That is why the 2026-11-03 log
    said `render failed rc=1: ... max_output_tokens=2048) File "...gemini_keys.py", line 321)` and
    named nothing. Prefer an explicit TRANSIENT:/FAILED: marker, then the last exception line.
    """
    text = (stderr or "").strip()
    if not text:
        return "(no stderr)"
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s.startswith(("TRANSIENT:", "FAILED:")):
            return s[:limit]
    # `XxxError: message` / `SystemExit: message` — the last such line is the one that explains it.
    for line in reversed(text.splitlines()):
        s = line.strip()
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Exhausted|Failure)\b", s) or s.startswith("SystemExit"):
            return s[:limit]
    return " | ".join(l.strip() for l in text.splitlines()[-3:] if l.strip())[:limit]


def post(workflow: str, video: Path, privacy: str, dry_run: bool,
         allow_private: bool = False) -> int:
    cmd = [sys.executable, str(ROOT / "scripts" / "youtube_upload.py"),
           "--workflow", workflow, "--file", str(video),
           "--report", str(video.with_suffix(".json")), "--privacy", privacy]
    if privacy == "private":
        # Forwarded only when the operator asked for it on this command line; youtube_upload.py
        # refuses a private upload without it, which is what stops an invisible video.
        if not allow_private:
            raise RuntimeError("refusing to publish as private without --allow-private")
        cmd.append("--allow-private")
    if dry_run:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, text=True, errors="replace")
    return r.returncode


# --------------------------------------------------------------------------------------
# one pass
# --------------------------------------------------------------------------------------

def credential_ready(workflow: str) -> tuple[bool, str]:
    """Can this workflow actually post? No network, no side effect.

    ForChi learned this the expensive way — `authGate.youtubeReady()` runs before build *and*
    publish, because six builds succeeded and every publish then failed. Here the gate matters for a
    second reason: a missing credential must not consume one of the three attempts for a slot, or the
    day is lost to a problem the schedule cannot fix.

    A stored refresh token is not enough. On 2026-11-01 emmanuel's consent came back without
    `youtube.upload`, so every insert would have been refused while the token looked fine — three
    attempts burned per slot, two slots a day, silently. The granted scope is the real gate.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import youtube_upload as yt

    try:
        cred = yt.credentials(workflow)
    except SystemExit as exc:
        return False, str(exc).splitlines()[0][:160]
    if not cred.get("refresh_token"):
        return False, f"{workflow}: no refresh token — run youtube_auth.py --serve"
    scopes = yt.granted_scopes(workflow)
    if scopes and "youtube.upload" not in scopes:
        return False, (f"{workflow}: consented without youtube.upload "
                       f"(granted {' '.join(scopes)}) — re-authorise the channel")
    return True, ""


def run_pass(workflows: tuple[str, ...], privacy: str, dry_run: bool, seed: int | None,
             force: bool = False, no_produce: bool = False, allow_private: bool = False) -> int:
    # Refused at the door. An invisible upload reports success, so it must not be reachable by a
    # default, a typo or a copied command line.
    if privacy == "private" and not allow_private:
        raise SystemExit("refusing to publish as private without --allow-private. A private upload "
                         "reports success and is invisible to the channel (2026-11-03).")
    cfg = load_config()
    state = load_state()
    now = datetime.now(ZoneInfo("Africa/Lagos"))
    problems = 0

    for name in workflows:
        wf = cfg[name]
        sched = schedule_of(wf)
        ws = state.setdefault(name, {})
        todays = slots_for(name, wf, now)
        print(f"\n=== {name} · {now.strftime('%Y-%m-%d %H:%M')} {sched.get('timezone')} "
              f"· privacy={privacy} ===")

        ready, why = credential_ready(name)
        if not ready:
            print(f"  credential: NOT ready — posting is gated, no attempt will be consumed")
            print(f"    {why}")

        for slot in todays:
            entry = ws.setdefault(slot["key"], {"attempts": 0})
            if entry.get("done") or entry.get("posted"):
                continue
            if now < slot["at"]:
                continue

            attempts = int(entry.get("attempts", 0))
            if attempts >= int(sched.get("max_attempts_per_slot", 3)) and not force:
                print(f"  {slot['kind']} {fmt(slot['at'])}: gave up after {attempts} attempts "
                      f"({entry.get('error', 'no error recorded')})")
                problems += 1
                continue

            retry = sched.get("retry_minutes", [10, 25])
            if attempts and "last_at" in entry:
                # A transient failure gets a short, fixed re-try window instead of the 10/25-minute
                # backoff: the provider is busy, not broken, and the slot is still salvageable.
                transient_last = entry.get("transient_at")
                if transient_last:
                    wait = float(sched.get("transient_retry_minutes", 4))
                    due = datetime.fromisoformat(transient_last) + timedelta(minutes=wait)
                else:
                    wait = retry[min(attempts - 1, len(retry) - 1)]
                    due = datetime.fromisoformat(entry["last_at"]) + timedelta(minutes=wait)
                if now < due and not force:
                    print(f"  {slot['kind']} {fmt(slot['at'])}: {attempts} attempt(s) done, "
                          f"retry at {due.strftime('%H:%M')}")
                    continue

            entry["attempts"] = attempts + 1
            entry["last_at"] = now.isoformat(timespec="seconds")
            save_state(state)

            if slot["kind"] == "post" and not ready:
                entry["attempts"] = attempts          # gated, not failed
                entry["gated"] = why
                save_state(state)
                print(f"  post {fmt(slot['at'])}: gated (no credential)")
                continue

            try:
                if slot["kind"] == "produce":
                    if no_produce:
                        entry["attempts"] = attempts          # not an attempt; leave it pending
                        save_state(state)
                        print(f"  produce {fmt(slot['at'])}: skipped (--no-produce)")
                        continue
                    video = produce(name, wf, slot, seed)
                    entry["file"] = str(video.relative_to(ROOT))
                    entry["done"] = True
                    print(f"  produce {fmt(slot['at'])}: {video.name}")
                else:
                    video = rendered_for_slot(name, wf, {"key": f"{now.strftime('%Y-%m-%d')}:produce:{slot['index']}"})
                    if video is None:
                        video = newest_unposted(name, wf)
                    if video is None:
                        raise RuntimeError("no produced video available to post")
                    dur = xf.probe_dur(video)
                    cap = float(wf.get("video", {}).get("max_seconds", 50))
                    if dur > cap + 0.5:
                        raise RuntimeError(f"{video.name} is {dur:.1f}s, over the {cap:.0f}s cap")
                    rc = post(name, video, privacy, dry_run, allow_private)
                    if rc == 3:      # ledger says these exact bytes are already published
                        entry["posted"] = True
                        entry["note"] = "already published (ledger)"
                        save_state(state)
                        print(f"  post {fmt(slot['at'])}: already published, skipped")
                        continue
                    if rc != 0:
                        raise RuntimeError(f"upload rc={rc}")
                    entry["posted"] = True
                    entry["posted_file"] = str(video.relative_to(ROOT))
                    print(f"  post {fmt(slot['at'])}: {video.name} -> {'claimed (dry run)' if dry_run else 'published'}")
                entry.pop("error", None)
                entry.pop("transient_at", None)
                entry.pop("transient_count", 0)
            except TransientRender as exc:
                # The provider was busy. Retry soon and, crucially, do NOT spend one of the three
                # attempts — otherwise a ten-minute 503 burst silently costs the whole day, which is
                # exactly what happened on 2026-11-03. Bounded, so a permanently busy provider still
                # surfaces instead of looping for ever.
                budget = int(sched.get("max_transient_retries", 12))
                n = int(entry.get("transient_count", 0)) + 1
                entry["attempts"] = max(0, attempts)          # un-spend the attempt
                entry["transient_count"] = n
                entry["transient_at"] = now.isoformat(timespec="seconds")
                entry["error"] = f"transient: {str(exc)[:180]}"
                save_state(state)
                if n > budget:
                    problems += 1
                    print(f"  {slot['kind']} {fmt(slot['at'])}: provider busy {n} times "
                          f"(budget {budget}) — giving up: {str(exc)[:120]}")
                else:
                    print(f"  {slot['kind']} {fmt(slot['at'])}: provider busy ({n}/{budget}) — "
                          f"retrying shortly, attempt not consumed")
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                problems += 1
                print(f"  {slot['kind']} {fmt(slot['at'])}: FAILED — {entry['error']}")
                if "traceback" in str(exc):
                    traceback.print_exc()
            save_state(state)

        pending = [s for s in todays
                   if not (ws.get(s["key"], {}).get("done") or ws.get(s["key"], {}).get("posted"))]
        ahead = [s for s in pending if s["at"] > now]
        if ahead:
            nxt = min(ahead, key=lambda s: s["at"])
            print(f"  next action: {nxt['kind']} at {fmt(nxt['at'])}")
        elif pending:
            past = ", ".join(f"{s['kind']} {fmt(s['at'])}" for s in sorted(pending, key=lambda s: s["at"]))
            print(f"  outstanding from earlier today: {past}")
        else:
            print("  today complete")

    return 0 if problems == 0 else 1


# --------------------------------------------------------------------------------------
# verify / status
# --------------------------------------------------------------------------------------

def show(day_offset: int = 0) -> int:
    cfg = load_config()
    now = datetime.now(ZoneInfo("Africa/Lagos"))
    day = now + timedelta(days=day_offset)
    print(f"{day.strftime('%Y-%m-%d')}  ({'today' if day_offset == 0 else f'+{day_offset}d'})")
    posts: list[tuple[str, str]] = []
    for name in WORKFLOWS:
        wf = cfg[name]
        for slot in slots_for(name, wf, day):
            mark = "post" if slot["kind"] == "post" else "prod"
            print(f"  {name:<9} {mark} #{slot['index']}  {fmt(slot['at'])}")
            if slot["kind"] == "post":
                posts.append((fmt(slot["at"]), name))
    posts.sort()
    print("\n  posting order:")
    for t, n in posts:
        print(f"    {t}  {n}")
    gaps = []
    for (t1, n1), (t2, n2) in zip(posts, posts[1:]):
        m1 = int(t1[:2]) * 60 + int(t1[3:])
        m2 = int(t2[:2]) * 60 + int(t2[3:])
        gaps.append((f"{t1} {n1} -> {t2} {n2}", m2 - m1))
    if gaps:
        print("  separation:")
        for label, gap in gaps:
            print(f"    {label}: {gap} min")
        if min(g for _, g in gaps) < 5:
            print("    WARNING: two posts are less than 5 minutes apart")
            return 1
    return 0


def status() -> int:
    state = load_state()
    cfg = load_config()
    now = datetime.now(ZoneInfo("Africa/Lagos"))
    print(f"now {now.strftime('%Y-%m-%d %H:%M')} Africa/Lagos")
    for name in WORKFLOWS:
        print(f"\n{name}")
        ws = state.get(name, {})
        for slot in slots_for(name, cfg[name], now):
            e = ws.get(slot["key"], {})
            mark = "posted" if e.get("posted") else ("produced" if e.get("done") else "—")
            extra = e.get("file") or e.get("posted_file") or ""
            print(f"  {slot['kind']:<7} {fmt(slot['at'])}  {mark:<9} attempts={e.get('attempts', 0)}  {extra}")
            if e.get("error"):
                print(f"          last error: {e['error'][:120]}")
    return 0


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Produce early, post in a jittered slot, twice a day")
    ap.add_argument("--verify", action="store_true", help="show the slots and their separation")
    ap.add_argument("--tomorrow", action="store_true", help="with --verify, show tomorrow")
    ap.add_argument("--status", action="store_true", help="today's state")
    ap.add_argument("--once", action="store_true", help="one pass")
    ap.add_argument("--daemon", action="store_true", help="loop until stopped")
    ap.add_argument("--workflow", default="both", choices=WORKFLOWS + ("both",))
    # Default matches what the scheduled task runs (`x_daily.cmd` passes --privacy public). It used
    # to be `private`, so a hand-run to catch up looked successful while uploading a video nobody
    # could see — measured 2026-11-03. Divergence between the manual and the scheduled command is a
    # trap, so there is now one default and it is printed on every pass.
    ap.add_argument("--privacy", default="public", choices=("private", "unlisted", "public"))
    ap.add_argument("--allow-private", action="store_true",
                    help="required with --privacy private: an invisible upload is a failure that "
                         "looks like a success")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="retry a slot that gave up")
    ap.add_argument("--no-produce", action="store_true",
                    help="post only: do not render, use a video that already exists")
    ap.add_argument("--interval", type=int, default=300, help="daemon wake interval, seconds")
    a = ap.parse_args()

    if a.verify:
        return show(1 if a.tomorrow else 0)
    if a.status:
        return status()

    targets = WORKFLOWS if a.workflow == "both" else (a.workflow,)
    if a.once:
        return run_pass(targets, a.privacy, a.dry_run, a.seed, a.force, a.no_produce,
                        a.allow_private)

    if a.daemon:
        print(f"scheduler awake; waking every {a.interval}s. Ctrl-C to stop.")
        while True:
            try:
                run_pass(targets, a.privacy, a.dry_run, a.seed, a.force, a.no_produce, a.allow_private)
            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()      # never die silently in a loop
            time.sleep(a.interval)

    ap.error("choose --verify, --status, --once or --daemon")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
