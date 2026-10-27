#!/usr/bin/env python3
"""Render the OAuth consent-screen pages for every workflow into web/site/.

Google will not verify an OAuth app without a public **app homepage**, **privacy policy** and
**terms of service** URL, and the YouTube API Services Developer Policies add two hard clauses
to the privacy policy: it must say that the app uses YouTube API Services, and it must link
Google's Privacy Policy and the YouTube Terms of Service. Those three pages are rendered here so
both clients get the same, complete text instead of one good page and one hurried one.

Output is static and committed. Re-run after editing config/workflows.json:

    python scripts\\build_site.py

Then publish with:

    python scripts\\deploy_site.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "workflows.json"
OUT = ROOT / "web" / "site"

GOOGLE_PRIVACY = "https://policies.google.com/privacy"
YOUTUBE_TOS = "https://www.youtube.com/t/terms"
YOUTUBE_API_TERMS = "https://developers.google.com/youtube/terms/api-services-terms-of-service"
GOOGLE_PERMISSIONS = "https://myaccount.google.com/permissions"

STYLE = """\
:root { --ink:#16161a; --muted:#5b5b66; --line:#e6e6ea; --accent:#7a3e9d; --bg:#fbfafc; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
main { max-width:44rem; margin:0 auto; padding:3.5rem 1.25rem 5rem; }
h1 { font-size:1.9rem; line-height:1.2; letter-spacing:-.02em; margin:0 0 .4rem; }
h2 { font-size:1.05rem; margin:2.2rem 0 .5rem; }
p, li { color:var(--ink); }
.lede { color:var(--muted); font-size:1.05rem; margin:0 0 2rem; }
nav { display:flex; gap:1.25rem; flex-wrap:wrap; border-bottom:1px solid var(--line);
  padding-bottom:1rem; margin-bottom:2rem; font-size:.92rem; }
nav a { color:var(--accent); text-decoration:none; }
nav a:hover { text-decoration:underline; }
a { color:var(--accent); }
ul { padding-left:1.15rem; }
li { margin:.35rem 0; }
.meta { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line);
  color:var(--muted); font-size:.86rem; }
.card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:1.1rem 1.25rem; }
.card + .card { margin-top:1rem; }
"""


def page(title: str, wf: dict, body: str, *, workflow: str) -> str:
    nav = (
        f'<a href="index.html">{wf["product_name"]}</a>'
        '<a href="privacy.html">Privacy Policy</a>'
        '<a href="terms.html">Terms of Service</a>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {wf["product_name"]}</title>
<meta name="description" content="{wf["description"]}">
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<main>
<nav>{nav}</nav>
{body}
<p class="meta">Last updated {date.today().isoformat()}. {wf["product_name"]} operates one YouTube
channel, <strong>{wf["channel_name"]}</strong>, on behalf of its owner. Contact:
<a href="mailto:{wf["contact_email"]}">{wf["contact_email"]}</a>.</p>
</main>
</body>
</html>
"""


def home(wf: dict) -> str:
    return page("Home", wf, f"""
<h1>{wf["product_name"]}</h1>
<p class="lede">{wf["description"]}</p>
<div class="card">
  <h2 style="margin-top:0">What this application does</h2>
  <ul>
    <li>An AI model drafts a short script for one video.</li>
    <li>A text-to-speech voice — a persona chosen by the channel owner — narrates it.</li>
    <li>The narration is assembled into a vertical short video.</li>
    <li>The finished video is uploaded to the single YouTube channel
        <strong>{wf["channel_name"]}</strong> ({wf["channel_handle"]}) through the YouTube Data API.</li>
  </ul>
</div>
<div class="card">
  <h2 style="margin-top:0">Who can use it</h2>
  <p>Only the owner of {wf["channel_name"]}. There is no sign-up, no public account, and no
     other channel can be connected. The Google account that authorises the app is the account
     that owns the channel.</p>
</div>
<div class="card">
  <h2 style="margin-top:0">Documents</h2>
  <p><a href="privacy.html">Privacy Policy</a> · <a href="terms.html">Terms of Service</a></p>
</div>
""", workflow=str(wf))


def privacy(wf: dict) -> str:
    return page("Privacy Policy", wf, f"""
<h1>Privacy Policy</h1>
<p class="lede">This policy covers {wf["product_name"]}, which publishes videos to one YouTube
channel, {wf["channel_name"]}.</p>

<h2>1. What we access</h2>
<ul>
  <li><strong>Google account authorisation.</strong> When the channel owner authorises this
      application, Google issues an access token and a refresh token for the scopes requested on
      the consent screen. Those scopes are limited to what publishing requires: reading basic
      channel information and uploading videos to the authorising channel.</li>
  <li><strong>The channel's own content metadata</strong> — the title, description and privacy
      status of videos this application has uploaded — so that an upload can be confirmed.</li>
  <li><strong>Nothing else.</strong> We do not read the owner's email, contacts, files, calendar,
      photos, or any other Google service, and we do not request scopes that would allow it.</li>
</ul>

<h2>2. What we store</h2>
<ul>
  <li>The OAuth refresh token, encrypted at rest on a private server controlled by the channel
      owner. It is used only to upload videos to {wf["channel_name"]}.</li>
  <li>The video files, scripts, narration audio and a record of what was uploaded and when.</li>
  <li>We do not store subscriber data, viewer data, or any personal data about third parties.</li>
</ul>

<h2>3. How we use it</h2>
<p>Only to produce videos and publish them to {wf["channel_name"]}. We do not sell, rent, trade or
share this data with anyone, and we do not use it for advertising or profiling. Text sent to a
language model to draft a script contains no personal data: it is a prompt describing the kind of
story to write.</p>

<h2>4. YouTube API Services</h2>
<p>{wf["product_name"]} uses <strong>YouTube API Services</strong>. By using this application, you
are agreeing to be bound by the <a href="{YOUTUBE_TOS}" rel="noopener">YouTube Terms of Service</a>.
Google's handling of data is described in the
<a href="{GOOGLE_PRIVACY}" rel="noopener">Google Privacy Policy</a>, and use of the API is governed
by the <a href="{YOUTUBE_API_TERMS}" rel="noopener">YouTube API Services Terms of Service</a>.</p>

<h2>5. Retention and deletion</h2>
<ul>
  <li>Access can be revoked at any time at
      <a href="{GOOGLE_PERMISSIONS}" rel="noopener">myaccount.google.com/permissions</a>. Revoking
      it invalidates the stored token immediately; the application then stops uploading.</li>
  <li>A request to delete stored data can be sent to
      <a href="mailto:{wf["contact_email"]}">{wf["contact_email"]}</a>. Stored tokens and records
      are deleted within 30 days of the request.</li>
  <li>If a token has been unused, or authorisation has been withdrawn, it is deleted rather than
      kept.</li>
</ul>

<h2>6. Children</h2>
<p>This application is not directed at children and has no end users other than the channel
owner.</p>

<h2>7. Changes</h2>
<p>If this policy changes, the date at the foot of this page changes with it.</p>

<h2>8. Contact</h2>
<p><a href="mailto:{wf["contact_email"]}">{wf["contact_email"]}</a></p>
""", workflow=str(wf))


def terms(wf: dict) -> str:
    return page("Terms of Service", wf, f"""
<h1>Terms of Service</h1>
<p class="lede">These terms cover {wf["product_name"]}, a private publishing tool operated on
behalf of the owner of the YouTube channel {wf["channel_name"]}.</p>

<h2>1. Scope</h2>
<p>{wf["product_name"]} is not a public service. It has no registration and no users other than
the owner of {wf["channel_name"]}, who runs it to write, narrate and upload videos to that channel.</p>

<h2>2. Acceptable use</h2>
<ul>
  <li>The application is used only with content the channel owner has the right to publish.</li>
  <li>It is used in compliance with the
      <a href="{YOUTUBE_TOS}" rel="noopener">YouTube Terms of Service</a> and the
      <a href="{YOUTUBE_API_TERMS}" rel="noopener">YouTube API Services Terms of Service</a>.</li>
  <li>It is not used to upload spam, infringing material, or content that violates YouTube's
      Community Guidelines.</li>
</ul>

<h2>3. Google account</h2>
<p>Authorising the application grants it permission to upload to the authorising channel. That
authorisation can be withdrawn at any time at
<a href="{GOOGLE_PERMISSIONS}" rel="noopener">myaccount.google.com/permissions</a>, which ends the
application's access.</p>

<h2>4. Content ownership</h2>
<p>The channel owner retains all rights in the videos, scripts, narration and music produced with
this tool. Nothing in these terms transfers ownership of that content.</p>

<h2>5. No warranty</h2>
<p>The tool is provided as-is, without warranty of any kind. YouTube's API may rate-limit, reject or
change; the tool is not responsible for the platform's behaviour.</p>

<h2>6. Limitation of liability</h2>
<p>To the extent permitted by law, the operator is not liable for indirect or consequential loss
arising from use of the tool, including lost revenue from a video that failed to publish.</p>

<h2>7. Changes</h2>
<p>These terms may be updated; the date at the foot of this page changes when they are.</p>

<h2>8. Contact</h2>
<p><a href="mailto:{wf["contact_email"]}">{wf["contact_email"]}</a></p>
""", workflow=str(wf))


def index(workflows: dict[str, dict]) -> str:
    cards = "\n".join(
        f'<div class="card"><h2 style="margin-top:0">{wf["product_name"]}</h2>'
        f'<p>{wf["description"]}</p>'
        f'<p><a href="{name}/index.html">Home</a> · '
        f'<a href="{name}/privacy.html">Privacy</a> · '
        f'<a href="{name}/terms.html">Terms</a></p></div>'
        for name, wf in workflows.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy and terms</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<main>
<h1>Privacy and terms</h1>
<p class="lede">One application per YouTube channel. Each has its own privacy policy and terms of
service, because each is verified as its own Google OAuth client.</p>
{cards}
</main>
</body>
</html>
"""


def main() -> int:
    workflows = json.loads(CONFIG.read_text(encoding="utf-8"))
    workflows = {k: v for k, v in workflows.items() if not k.startswith("_")}
    written = []
    (OUT / "assets").mkdir(parents=True, exist_ok=True)
    (OUT / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    written.append("assets/style.css")
    (OUT / "index.html").write_text(index(workflows), encoding="utf-8")
    written.append("index.html")

    for name, wf in workflows.items():
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        for filename, render in (("index.html", home), ("privacy.html", privacy), ("terms.html", terms)):
            (d / filename).write_text(render(wf), encoding="utf-8")
            written.append(f"{name}/{filename}")

    for w in written:
        print(f"  {w}")
    print(f"{len(written)} files in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
