#!/usr/bin/env python3
"""One command that publishes a channel's daily short — safely and repeatably.

    python scripts\\x_publish.py --workflow jephter
    python scripts\\x_publish.py --workflow both --privacy public
    python scripts\\x_publish.py --workflow jephter --dry-run

What it does, in order:

1. **Has today already been published?** The ledger (`out/publish_ledger.json`) is the authority. If a
   video was published for this workflow today, it stops — that is what makes it safe to run from a
   scheduler every hour.
2. **Is there a render for today?** `out/<workflow>/<workflow>_<YYYYMMDD>-*.mp4`. If not, and
   `--render-missing` is given, it renders one (`x_render.py`, which writes into that same folder).
3. **Has that file already been uploaded?** Checked by content hash, so a re-render with the same bytes
   is not re-posted and a *different* file is not mistaken for it.
4. **Upload** via `youtube_upload.py` — resumable, claim-before-side-effect, **public** unless told.

Nothing here runs on a timer by itself; §"Scheduling" in `skills/x-publishing/SKILL.md` shows the two
ways to do that, and the run is idempotent so either works.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"
LEDGER = ROOT / "out" / "publish_ledger.json"
WORKFLOWS = ("jephter", "emmanuel")


def load_config() -> dict:
    return {k: v for k, v in json.loads(CONFIG.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"claims": {}, "published": []}


def published_today(workflow: str) -> dict | None:
    today = date.today().isoformat()
    for entry in reversed(ledger().get("published", [])):
        if entry.get("workflow") == workflow and str(entry.get("at", "")).startswith(today):
            return entry
    return None


def newest_render(workflow: str, wf: dict) -> Path | None:
    out_dir = ROOT / wf.get("output_dir", f"out/{workflow}")
    files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def is_today_render(path: Path) -> bool:
    stamp = date.today().strftime("%Y%m%d")
    if stamp in path.name:
        return True
    return datetime.fromtimestamp(path.stat().st_mtime).date() == date.today()


def render_one(workflow: str, seed: int | None) -> Path:
    cmd = [sys.executable, str(ROOT / "scripts" / "x_render.py"), "--workflow", workflow]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    print(f"  rendering: {' '.join(cmd[1:])}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        print(f"    {line}")
    if r.returncode != 0:
        raise SystemExit(f"{workflow}: render failed (rc={r.returncode})\n{(r.stderr or '')[-800:]}")
    wf = load_config()[workflow]
    out = newest_render(workflow, wf)
    if not out:
        raise SystemExit(f"{workflow}: render reported success but produced no mp4")
    return out


def publish(workflow: str, privacy: str, dry_run: bool, force: bool,
            allow_private: bool = False) -> int:
    # Before anything else: a private upload is refused at the door, not after a render and an
    # "already published" check have already reported something reassuring.
    if privacy == "private" and not allow_private:
        raise SystemExit("refusing to publish as private without --allow-private. A private upload "
                         "reports success and is invisible to the channel.")
    wf = load_config()[workflow]

    done = published_today(workflow)
    if done and not force:
        print(f"  {workflow}: already published today — https://youtu.be/{done.get('video_id')} "
              f"({done.get('at')}). Nothing to do.")
        return 0

    video = newest_render(workflow, wf)
    if video is None or not is_today_render(video):
        print(f"  {workflow}: no render for today — pass --render-missing to build one")
        return 4

    dur = xf.probe_dur(video)
    cap = float(wf.get("video", {}).get("max_seconds", 50))
    if dur > cap + 0.5:
        raise SystemExit(f"{workflow}: {video.name} is {dur:.1f}s, over the {cap:.0f}s cap — not publishing")

    cmd = [sys.executable, str(ROOT / "scripts" / "youtube_upload.py"),
           "--workflow", workflow,
           "--file", str(video),
           "--report", str(video.with_suffix(".json")),
           "--privacy", privacy]
    if privacy == "private":
        cmd.append("--allow-private")
    if dry_run:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    print(f"  {workflow}: {video.name}  {dur:.1f}s  privacy={privacy}")
    r = subprocess.run(cmd, text=True, errors="replace")
    return r.returncode


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Publish a channel's daily short, once")
    ap.add_argument("--workflow", default="both", choices=WORKFLOWS + ("both", "forchi"))
    ap.add_argument("--privacy", default="public", choices=("private", "unlisted", "public"))
    ap.add_argument("--allow-private", action="store_true",
                    help="required with --privacy private; private is never a default")
    ap.add_argument("--render-missing", action="store_true", help="render if there is no video for today")
    ap.add_argument("--dry-run", action="store_true", help="check and claim, upload nothing")
    ap.add_argument("--force", action="store_true", help="ignore today's published record")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--status", action="store_true", help="report what has been published, then exit")
    a = ap.parse_args()

    if a.status:
        data = ledger()
        print(f"  {len(data.get('published', []))} published, {len(data.get('claims', {}))} claim(s)")
        for e in data.get("published", [])[-10:]:
            print(f"    {e.get('at')}  {e.get('workflow'):<9} {e.get('privacy'):<8} "
                  f"https://youtu.be/{e.get('video_id')}  {e.get('title', '')[:48]}")
        return 0

    targets = WORKFLOWS if a.workflow == "both" else (a.workflow,)
    codes = []
    for wf_name in targets:
        print(f"\n=== {wf_name} ===")
        if a.render_missing:
            if not any(is_today_render(p) for p in [newest_render(wf_name, load_config()[wf_name])] if p):
                render_one(wf_name, a.seed)
        codes.append(publish(wf_name, a.privacy, a.dry_run, a.force, a.allow_private))

    bad = [c for c in codes if c not in (0,)]
    if bad:
        print(f"\n  {len(bad)} workflow(s) did not publish cleanly (rc={bad})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
