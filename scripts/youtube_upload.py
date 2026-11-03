"""Publish one workflow's newest render to YouTube, with a ledger so nothing posts twice.

Why this shape
--------------
* **Resumable `videos.insert`.** A 20 MB Short over a slow link fails a plain upload half the time;
  resumable survives that and can be retried.
* **A ledger claim BEFORE the side effect.** `upload:4` in the pipeline this borrows from is the most
  dangerous number in that file: a socket timeout *after* YouTube accepted the upload produced a
  second public video. So the claim is written and flushed first, and a retry reads it and refuses.
* **`--check` never uploads.** It proves the refresh token works, prints the scopes Google actually
  granted, and reads the channel back if a read scope allows it — no insert, nothing published.
* **Public by default, and `private` needs `--allow-private`.** The original reasoning was backwards: it
  assumed the dangerous mistake was an accidental publish. The mistake that actually happened (2026-11-03)
  was an accidental **private** upload — the run reported success, the video was invisible, and the channel
  lost a day. Both failures are now deliberate acts: publishing is the default because that is what these
  two channels exist to do, and hiding a video takes a second explicit flag.

Usage
-----
    python scripts\\youtube_upload.py --workflow jephter --check
    python scripts\\youtube_upload.py --workflow jephter --file out/jephter/x.mp4 --dry-run
    python scripts\\youtube_upload.py --workflow jephter --file out/jephter/x.mp4 --privacy unlisted
    python scripts\\youtube_upload.py --workflow forchi  --check

Tokens: `secrets/youtube/<workflow>.json` (AES-GCM sealed, written by `youtube_auth.py`). For the
`forchi` workflow the refresh token is read from ForChi's own `.env` so nothing is copied.

Quota: `videos.insert` has its own bucket of **100 per project per day** (Google quota calculator,
rev. 2026-10-15), separate from the 10,000 units/day for everything else. Three channels sharing one
Google project therefore share that 100, they do not get 100 each.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import x_ffmpeg as xf  # noqa: E402

CONFIG = ROOT / "config" / "workflows.json"
SECRETS = ROOT / "secrets" / "youtube"
LEDGER = ROOT / "out" / "publish_ledger.json"
FORCHI_ENV = Path(r"C:\Users\hp\forchi\.env")

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CATEGORY_PEOPLE_BLOGS = "22"


ENV_FILE = ROOT / ".env"


def read_env(path: Path, name: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith(name + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def env(name: str) -> str:
    """One setting from this project's .env, by name."""
    return read_env(ENV_FILE, name)


# --------------------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------------------

def credentials(workflow: str) -> dict:
    """Return {client_id, client_secret, refresh_token} for a workflow, without printing any of it.

    One client, three channels: since both X accounts were added to the same OAuth audience, every
    channel authorises the *same* client and each ends up with its own refresh token. The token is
    looked for in this order, and the first two are the normal paths:

    1. `secrets/youtube/<workflow>.json` — written by `youtube_auth.py`, AES-GCM sealed;
    2. `<WORKFLOW>_REFRESH_TOKEN` in this project's `.env`;
    3. for `forchi` only: its own `.env` (`YOUTUBE_REFRESH_TOKEN`), then its jobs database
       (`kv` table, key `youtube_refresh_token`) — read in place, never copied.
    """
    client_id, client_secret = shared_client(workflow)

    if workflow != "forchi":
        import youtube_auth

        try:
            cred = youtube_auth.load(workflow)
        except SystemExit:
            cred = {}
        token = (cred.get("refresh_token") or env(workflow.upper() + "_REFRESH_TOKEN"))
        if token:
            return {"client_id": cred.get("client_id") or client_id,
                    "client_secret": cred.get("client_secret") or client_secret,
                    "refresh_token": token}
        raise SystemExit(f"{workflow}: no refresh token. Run once, signed in as that channel's Google "
                         f"account:\n  python scripts\\youtube_auth.py --workflow {workflow}")

    token = read_env(FORCHI_ENV, "YOUTUBE_REFRESH_TOKEN") or token_from_jobs_db()
    if not token:
        raise SystemExit("forchi: no refresh token in its .env or its jobs database. Set "
                         "YOUTUBE_REFRESH_TOKEN there, or run youtube_auth.py --workflow forchi.")
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": token}


def shared_client(workflow: str) -> tuple[str, str]:
    """The one OAuth client all three channels use, in the same order youtube_auth.py uses."""
    cid = env(workflow.upper() + "_GOOGLE_CLIENT_ID") or env("GOOGLE_CLIENT_ID")
    secret = env(workflow.upper() + "_GOOGLE_CLIENT_SECRET") or env("GOOGLE_CLIENT_SECRET")
    if not (cid and secret):
        cid = cid or read_env(FORCHI_ENV, "YOUTUBE_CLIENT_ID")
        secret = secret or read_env(FORCHI_ENV, "YOUTUBE_CLIENT_SECRET")
    if not (cid and secret):
        raise SystemExit("no OAuth client found: set GOOGLE_CLIENT_ID/_SECRET in .env, or let it read "
                         "ForChi's YOUTUBE_CLIENT_ID/_SECRET")
    return cid, secret


def token_from_jobs_db() -> str:
    """ForChi keeps its refresh token in SQLite when it runs locally. Read it, never log it."""
    import sqlite3

    for rel in ("data/jobs.db",):
        path = Path(r"C:\Users\hp\forchi") / rel
        if not path.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            row = con.execute("select v from kv where k='youtube_refresh_token'").fetchone()
            con.close()
        except Exception:
            continue
        if row and row[0]:
            return str(row[0])
    return ""


def access_token(cred: dict) -> str:
    import urllib.parse

    data = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"token refresh failed (HTTP {e.code}): {detail}\n"
                         f"  invalid_grant here means the consent was revoked, or the OAuth app is "
                         f"still in Testing and the refresh token expired after 7 days")
    token = body.get("access_token")
    if not token:
        raise SystemExit(f"no access_token in the token response (keys: {sorted(body)})")
    return token


def granted_scopes(workflow: str) -> list[str]:
    """The scopes Google actually granted at consent time, short names only.

    This is the check that matters before an upload: `youtube.upload` present means `videos.insert` is
    allowed. A token without it refreshes happily and then fails every insert, so the failure would only
    surface days later in the scheduler. See skills/x-publishing.
    """
    path = SECRETS / f"{workflow}.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("scopes") or []
    except (OSError, json.JSONDecodeError):
        return []
    return [s.rsplit("/", 1)[-1] for s in raw]


def channel_of(token: str) -> dict:
    """Which channel this token belongs to — best effort, never fatal.

    `channels.list` needs a read scope and a token granted only `youtube.upload` does not have one, so an
    unreadable identity returns None instead of raising. Uploading proves the identity anyway: the
    `videos.insert` response carries `snippet.channelId` and `snippet.channelTitle`.
    """
    url = f"{API}/channels?part=snippet,contentDetails&mine=true"
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"id": None, "title": "(unread: no read scope)"}
        raise
    items = body.get("items") or []
    if not items:
        return {"id": None, "title": "(no channel on this account)"}
    it = items[0]
    return {"id": it["id"], "title": it["snippet"]["title"]}


# --------------------------------------------------------------------------------------
# ledger — the claim that prevents a double post
# --------------------------------------------------------------------------------------

def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"claims": {}, "published": []}


def save_ledger(ledger: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def claim(ledger: dict, workflow: str, file_hash: str) -> None:
    """Write the claim and flush it BEFORE the upload starts."""
    ledger.setdefault("claims", {})[f"{workflow}:{file_hash}"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "claimed",
    }
    save_ledger(ledger)


def already_done(ledger: dict, workflow: str, file_hash: str) -> dict | None:
    key = f"{workflow}:{file_hash}"
    if key in ledger.get("published_by_hash", {}):
        return ledger["published_by_hash"][key]
    entry = ledger.get("claims", {}).get(key)
    if entry and entry.get("status") in ("claimed", "uploaded"):
        return entry
    return None


def hash_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------------------
# metadata and upload
# --------------------------------------------------------------------------------------

def metadata_for(workflow: str, script: dict | None, channel_name: str | None = None) -> dict:
    """Title, description and tags. The first line of the script becomes the hook.

    `privacyStatus` here is a placeholder that `main()` overwrites with the requested privacy — it is
    **public**, not private, so a caller that forgets to set it publishes rather than hides. A private
    default is how an emmanuel video went up invisible on 2026-11-03 while the run reported success.

    The footer names the **real** channel when the caller knows it, because the workflow name and the
    YouTube channel's own name are not the same thing: the emmanuel workflow posts to a channel called
    "Brown Cinema", and a description signed "Emmanuel" on that channel is a mismatch a viewer sees.

    The hook is validated, not trusted. On 2026-11-02 a leaked model instruction (`target 85, max 95).`)
    became the public title of a published video, so a line that is not speech is replaced instead of
    being shipped.
    """
    wf = json.loads(CONFIG.read_text(encoding="utf-8")).get(workflow, {})
    wf = {k: v for k, v in wf.items() if not k.startswith("_")}
    channel = channel_name or wf.get("channel_name", workflow.title())
    lines = (script or {}).get("lines") or []
    hook = ""
    try:
        import x_script as _xs
        for cand in lines:
            cand = str(cand).strip()
            if cand and _xs.is_speech(cand):
                hook = cand.rstrip(".")
                break
    except Exception:                                        # noqa: BLE001 - never block a publish
        hook = (lines[0].rstrip(".") if lines else "")
    title = (hook[:92] + "…") if len(hook) > 92 else (hook or f"{channel} — a short about love")
    body = "\n".join(lines) if lines else ""
    description = (
        f"{body}\n\n"
        f"—\n{channel}\n\n"
        "New short every day. Made with love.\n\n"
        "#love #relationship #romance #shorts"
    )
    return {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": ["love", "relationship", "romance", "couple", "shorts"],
            "categoryId": CATEGORY_PEOPLE_BLOGS,
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }


def upload(token: str, path: Path, meta: dict, timeout: float = 1800.0) -> dict:
    """Resumable upload. Returns the videos.insert response."""
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    start = urllib.request.Request(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
            "x-upload-content-type": mime,
            "x-upload-content-length": str(size),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(start, timeout=120) as r:
            session = r.headers.get("location")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"could not open an upload session (HTTP {e.code}): "
                         f"{e.read().decode('utf-8', 'replace')[:300]}")
    if not session:
        raise SystemExit("no resumable session URL returned")

    data = path.read_bytes()
    put = urllib.request.Request(
        session, data=data,
        headers={"content-type": mime, "content-length": str(size)}, method="PUT")
    try:
        with urllib.request.urlopen(put, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"upload failed (HTTP {e.code}): {e.read().decode('utf-8', 'replace')[:300]}")


def confirm(token: str, video_id: str) -> dict:
    url = f"{API}/videos?part=status,snippet&id={video_id}"
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # `youtube.upload` does not authorise videos.list either. The upload itself already
            # succeeded, so report it as unconfirmed instead of failing a run that did post.
            return {"title": "(unconfirmed)", "privacy": "as requested",
                    "upload_status": "uploaded-unconfirmed"}
        raise
    items = body.get("items") or []
    if not items:
        return {"title": "(unconfirmed)", "privacy": "as requested",
                "upload_status": "uploaded-unconfirmed"}
    return {"title": items[0]["snippet"]["title"],
            "privacy": items[0]["status"]["privacyStatus"],
            "upload_status": items[0]["status"].get("uploadStatus")}


def selftest() -> int:
    """Prove the parts that do not need a credential: metadata, hashing, and the double-post guard."""
    import tempfile

    print("  metadata:")
    meta = metadata_for("jephter", {"lines": ["I told myself I was fine.", "I wanted it to stay."]})
    assert meta["snippet"]["title"] == "I told myself I was fine", meta["snippet"]["title"]
    assert "I wanted it to stay." in meta["snippet"]["description"]
    assert meta["snippet"]["categoryId"] == CATEGORY_PEOPLE_BLOGS
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    print(f"    title={meta['snippet']['title']!r}  privacy={meta['status']['privacyStatus']} (public unless told)")

    long_title = {"lines": ["x " * 80]}
    t = metadata_for("emmanuel", long_title)["snippet"]["title"]
    assert len(t) <= 100, len(t)
    print(f"    long line clipped to {len(t)} chars")

    with tempfile.TemporaryDirectory() as d:
        probe = Path(d) / "a.mp4"
        probe.write_bytes(b"x" * 4096)
        h1, h2 = hash_file(probe), hash_file(probe)
        assert h1 == h2 and len(h1) == 16
        print(f"    hash stable: {h1}")

        ledger = {"claims": {}, "published": []}
        assert already_done(ledger, "jephter", h1) is None
        claim(ledger, "jephter", h1)
        assert already_done(ledger, "jephter", h1) is not None
        print("    double-post guard: claim blocks a second upload of the same file")

        probe.write_bytes(b"y" * 4096)
        assert hash_file(probe) != h1
        print("    a different file is a different key")

    print("  selftest passed (nothing uploaded, no credential touched)")
    return 0


def main() -> int:
    xf.utf8_stdout()
    ap = argparse.ArgumentParser(description="Publish one workflow's video to YouTube")
    ap.add_argument("--workflow", choices=("jephter", "emmanuel", "forchi"))
    ap.add_argument("--file", help="the mp4 to upload")
    ap.add_argument("--report", help="the render sidecar .json (for the script and title)")
    ap.add_argument("--check", action="store_true", help="prove the credential; uploads nothing")
    ap.add_argument("--dry-run", action="store_true", help="everything except the upload")
    ap.add_argument("--privacy", default="public", choices=("private", "unlisted", "public"),
                    help="public by default. `private` now also needs --allow-private, because a "
                         "default that quietly uploaded invisible videos cost a day on 2026-11-03")
    ap.add_argument("--allow-private", action="store_true",
                    help="required to publish as private; nothing should reach a channel's audience "
                         "by accident, and nothing should be invisible by accident either")
    ap.add_argument("--force", action="store_true", help="ignore an existing ledger claim")
    ap.add_argument("--selftest", action="store_true", help="no network, no credential")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.workflow:
        ap.error("--workflow is required unless --selftest is given")
    if a.privacy == "private" and not a.allow_private:
        ap.error("--privacy private needs --allow-private. A private upload looks like a success and is "
                 "invisible to the channel; on 2026-11-03 that is exactly how a video vanished.")

    cred = credentials(a.workflow)
    token = access_token(cred)
    scopes = granted_scopes(a.workflow)
    ch = channel_of(token)
    identity = ch["title"] + (f" ({ch['id']})" if ch["id"] else "")
    print(f"  credential ok: {identity}")
    print(f"  granted scopes: {' '.join(scopes) or '(unknown)'}")
    if scopes and "youtube.upload" not in scopes:
        raise SystemExit(
            f"{a.workflow}: this token was consented WITHOUT youtube.upload, so videos.insert will be "
            f"refused.\n  re-authorise it:\n    python scripts\\youtube_auth.py --serve --timeout 900\n"
            f"  then open the {a.workflow} link and tick the YouTube permission")

    if a.check:
        print("  --check only: nothing uploaded")
        return 0

    if not a.file:
        ap.error("--file is required unless --check is given")
    path = Path(a.file)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise SystemExit(f"file not found: {path}")

    script = None
    if a.report:
        rp = Path(a.report) if Path(a.report).is_absolute() else ROOT / a.report
        if rp.exists():
            data = json.loads(rp.read_text(encoding="utf-8"))
            script = {"lines": data.get("phrases") or data.get("lines") or []}

    meta = metadata_for(a.workflow, script, channel_name=ch["title"] if ch.get("id") else None)
    meta["status"]["privacyStatus"] = a.privacy
    digest = hash_file(path)
    ledger = load_ledger()
    done = already_done(ledger, a.workflow, digest)
    if done and not a.force:
        print(f"  REFUSING: this file is already {done.get('status')} "
              f"({done.get('video_id') or 'no id'}, {done.get('at')}). Use --force only if you are sure.")
        return 3

    print(f"  title: {meta['snippet']['title']}")
    print(f"  privacy: {a.privacy}   size: {path.stat().st_size // 1024} KB   sha: {digest}")
    if a.dry_run:
        print("  --dry-run: claim written, nothing uploaded")
        claim(ledger, a.workflow, digest)
        return 0

    claim(ledger, a.workflow, digest)          # BEFORE the side effect
    started = time.time()
    resp = upload(token, path, meta)
    vid = resp.get("id")
    if not vid:
        raise SystemExit(f"upload returned no video id: {json.dumps(resp)[:300]}")
    info = confirm(token, vid)

    # The insert response is the identity proof when there is no read scope to call channels.list with.
    inserted = resp.get("snippet") or {}
    channel_name = inserted.get("channelTitle") or ch["title"]
    channel_id = inserted.get("channelId") or ch["id"]
    if channel_id:
        print(f"  posted to: {channel_name} ({channel_id})")

    ledger["claims"][f"{a.workflow}:{digest}"]["status"] = "uploaded"
    ledger["claims"][f"{a.workflow}:{digest}"]["video_id"] = vid
    ledger.setdefault("published_by_hash", {})[f"{a.workflow}:{digest}"] = {
        "video_id": vid, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "privacy": info["privacy"], "channel": channel_name, "channel_id": channel_id,
        "status": "uploaded",
    }
    ledger.setdefault("published", []).append({
        "workflow": a.workflow, "video_id": vid, "title": info["title"],
        "privacy": info["privacy"], "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "seconds": round(time.time() - started, 1),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    save_ledger(ledger)

    print(f"  uploaded: https://youtu.be/{vid}  ({info['privacy']}, state {info['upload_status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
