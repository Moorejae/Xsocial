---
name: x-google-console-oauth
description: 'Securing each client''s Google Cloud console for the two X YouTube workflows: which client type and redirect URI to use, why a bare IP is refused, the 7-day refresh-token trap for apps left in Testing, the videos.insert quota bucket, the scope to request, and the privacy/terms pages Google will not verify without. Load this when creating or fixing an OAuth client for jephter or emmanuel, when the consent screen is rejected, when an upload stops working after about a week, when a redirect_uri_mismatch or "invalid redirect" appears, or when the privacy/terms URLs have to be moved to a real domain.'
---

# Securing a client's Google console

Two clients, two channels, two **independent** Google Cloud projects. Independence is not tidiness: quota is
granted per project, and a refresh token belongs to one project's client, so sharing a project would couple the two
workflows exactly where the owner asked for them to be separate.

> **Resilience is part of done.** The credential produced here is the thing an unattended uploader depends on. A
> revoked or expired refresh token must raise an alert, not a silent gap in the schedule. See `build-fallbacks` in
> the shared skill library.

## When to use

- Creating the OAuth client for `jephter` or `emmanuel`.
- `redirect_uri_mismatch`, or the console refusing an `http://<ip>/…` URL.
- Uploads worked, then stopped after about a week.
- Google asks for an app homepage, privacy policy or terms of service during verification.
- Deciding whether to keep the client in Testing or publish it.

## Two clients, never one

| | jephter | emmanuel |
| :--- | :--- | :--- |
| Google Cloud project | one, owned by that client | one, owned by that client |
| OAuth client type | **Desktop app** | **Desktop app** |
| Redirect URI | `http://localhost:8765/jephter/callback` | `http://localhost:8765/emmanuel/callback` |
| Scope | `https://www.googleapis.com/auth/youtube.upload` | same |
| Gemini pool | `jephter` (9 keys) | `emmanuel` (9 keys) |
| Pages | `/jephter/` on the site | `/emmanuel/` on the site |

The operator is added to the client's project (or the client performs the clicks and hands over client id +
secret). Nothing about the two projects should be shared — not the project, not the OAuth client, not the token.

## Why a Desktop client and a loopback URI

The uploader runs unattended on `169.58.212.7` and never needs to receive a browser request. So consent happens
once, on a workstation, and produces a long-lived **refresh token**:

| Option | Verdict |
| :--- | :--- |
| Desktop app + `http://localhost:8765/<workflow>/callback` | **Use this.** No domain, no TLS, no inbound server. Google accepts any loopback port for an installed-app client |
| Web app + `https://<domain>/oauth/<workflow>/callback` | Correct once a domain points at the VPS over TLS. Needed only if the callback must be public |
| Web app + `http://169.58.212.7/…` | **Refused.** Google requires a public top-level domain with HTTPS; it will not register a bare IP |
| OOB / copy-paste flow | Gone. Removed by Google; do not design around it |

`scripts/youtube_auth.py` generates the consent URL and runs the loopback listener. The secret never appears in the
URL; the client id does, by design (Google puts it in every consent URL).

```powershell
python scripts\youtube_auth.py --workflow jephter --print-url   # check the URI by eye, no secret needed
python scripts\youtube_auth.py --workflow jephter               # real consent, opens a browser
python scripts\youtube_auth.py --workflow jephter --access-token # refresh + report the channel
```

The credential lands in `secrets/youtube/<workflow>.json` (git-ignored), **sealed with AES-GCM**: the client secret
and refresh token are ciphertext under a key from `X_SECRET_KEY` in `.env` (falling back to `secrets/master.key`),
bound to the workflow name as associated data so one channel's file cannot be used as the other's. Metadata stays
readable so the file can be inspected without the key. The privacy policy states the token is encrypted at rest —
keep those two facts in step.

```powershell
python scripts\youtube_auth.py --selftest     # round-trip, wrong-workflow rejection, no plaintext on disk
```

**The proof that consent went to the right channel is `channels.list(mine=true)` printed after the exchange** — the
account that consented is not necessarily the channel that was meant, and with two clients the wrong one is easy to
pick.

## The two traps that cost a week each

| Trap | Detail | Fix |
| :--- | :--- | :--- |
| **Testing status expires refresh tokens in 7 days** | Google's OAuth docs, *Refresh token expiration*: a project whose consent screen is External with publishing status **Testing** gets a refresh token that expires in **7 days**, unless the only scopes requested are name/email/profile — and `youtube.upload` is not one of those | Publish the app (**In production**). Symptom of getting this wrong: the bot works for a week, then every refresh returns `invalid_grant` |
| **`videos.insert` has its own bucket** | Google's quota calculator (revision 2026-10-15): a project gets **100 `videos.insert` calls per day**, 1 unit each, separate from the 10,000 units/day for other endpoints and 100 `search.list`/day. The old "1600 units per upload → 6/day" figure is obsolete | Budget **100 uploads/channel/day**, and remember two projects means two independent buckets. Exceeding it is `HTTP 403 quotaExceeded`, not a token problem |

## Console steps, per client

1. Console → **APIs & Services → Library** → enable **YouTube Data API v3**.
2. **OAuth consent screen**: User type **External**; fill app name, support email, developer contact; then
   **Publish app**. Do not leave it in Testing for anything that runs unattended.
3. **Scopes**: add `https://www.googleapis.com/auth/youtube.upload` and nothing else. Adding `youtube` (full) or
   `youtube.force-ssl` widens the consent, re-triggers verification, and buys nothing this pipeline needs.
4. **Credentials → Create credentials → OAuth client ID → Desktop app.** Name it after the channel.
5. Put the id and secret in `.env` as `<WORKFLOW>_GOOGLE_CLIENT_ID` / `<WORKFLOW>_GOOGLE_CLIENT_SECRET`
   (`JEPHTER_…`, `EMMANUEL_…`). Use the shape checker to confirm the names are present; never print a value.
6. Run the consent flow once, and **read back the channel title** it prints.
7. Verify with `--access-token`, which refreshes and re-reports the channel.

## The verification pages

Google will not verify an external OAuth app without a public **app homepage**, **privacy policy** and **terms of
service**, and the YouTube API Services Developer Policies add two clauses the privacy policy must carry: a
statement that the app **uses YouTube API Services**, and links to the **Google Privacy Policy** and the **YouTube
Terms of Service**.

Those pages are generated, not hand-written, so both clients get the same complete text:

```powershell
python scripts\build_site.py      # render config/workflows.json -> web/site/
python scripts\deploy_site.py     # nginx on 169.58.212.7, root /srv/x-site, serve :80
```

`web/site/index.html` plus `<workflow>/{index,privacy,terms}.html`. To reword a page, edit the template in
`scripts/build_site.py` and re-run — do not edit `web/site/` by hand, it is output.

**Still open as of 2026-10-27:** the pages are served on the bare IP (`http://169.58.212.7/jephter/privacy.html`
returns 200 from outside). That is enough to draft and review them, but **not** enough for verification: Google
needs a real domain over HTTPS. The remaining step is a domain → A record to `169.58.212.7` → `certbot --nginx` on
`vmi3701960`, then replace `site_host` in `config/workflows.json`. One domain with `/jephter/` and `/emmanuel/`
paths is enough for both.

## Failure catalogue

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `redirect_uri_mismatch` | the URI in the console differs from `redirect_uri()` in `youtube_auth.py` | compare character for character, including scheme, port and path |
| "Invalid Redirect: must end with a public top-level domain" | a Web client given an IP or a non-public host | use the Desktop client, or a real domain |
| Every refresh returns `invalid_grant` after ~7 days | app still in **Testing** | publish the app; re-consent once |
| You want to know **now** whether the app is in Testing, without opening the console | — | exchange the refresh token and look for `refresh_token_expires_in` in the response. Present = Testing, absent = published. `python scripts\x_watchdog.py --force-token-check --dry-run` does it and names the days left. Measured 2026-11-01: both channels had 6 days left |
| `invalid_grant` immediately after re-consent | the previous grant was revoked, or more than the per-client token limit exists | revoke at `myaccount.google.com/permissions`, consent again |
| `HTTP 403 quotaExceeded` on insert | the 100/day `videos.insert` bucket is spent | wait for midnight PT, or request more quota |
| Consent succeeded but the wrong channel appears | two clients, two browser sessions, one Google login | revoke and redo with the right account; always read back the channel title |
| `access_denied` during consent | the client's Google account is not a test user while the app is in Testing | publish, or add the account as a test user |
| Callback page says **Not authorised** and shows `HTTPError: HTTP Error 403: Forbidden`, on every channel at once | the scope requested does not allow the call the authoriser makes after the exchange: `youtube.upload` alone cannot call `channels.list` | request `youtube.readonly` too, **restart the `--serve` listener** (a running process keeps the old `SCOPES`), and open the newly printed link. Token saving must not depend on that read-back — `whoami_safe()` in `youtube_auth.py` exists for exactly this |
| The consent link was emailed to the channel owner and their browser shows "site can't be reached" after approving | the redirect is a **loopback** URI: `http://localhost:8765/<wf>/callback` exists only on the machine running the tool, not on theirs | expected. The code is in *their* address bar — have them send the whole URL back, then `python scripts\youtube_auth.py --workflow <wf> --from-url "<that url>"`. `x_watchdog.py` mails exactly these instructions on day 4 and day 5 of the token's life |
| "The OAuth link isn't working" and there is no error text to go on | nothing is wrong with the link. Both consent URLs return **HTTP 200 on `accounts.google.com`** when fetched, with no `redirect_uri_mismatch` and no `invalid_client` — so the client and the redirect are registered. The failure is always the *last hop*: approve, then `localhost:8765` has nobody listening |

### "The link isn't working" — diagnose it in one request before believing it

```python
import sys; sys.path.insert(0, "scripts")
import youtube_auth as ya, urllib.request
cid, _ = ya.credentials("jephter")
with urllib.request.urlopen(ya.consent_url("jephter", cid), timeout=25) as r:
    print(r.status, r.url)          # 200 https://accounts.google.com/... = the link is fine
```

A `400` whose body contains `redirect_uri_mismatch` is a console problem. `200` on the sign-in page means the link is
good and the remaining fault is one of exactly two things — and they need different words:

| What the person saw | What actually happened | Fix |
| :--- | :--- | :--- |
| the sign-in page, then **"site can't be reached" / "localhost refused to connect"** | approved, and no listener on `localhost:8765` | either run `--serve` **first** (`python scripts\youtube_auth.py --serve` prints both links and waits 15 min), or paste the failing address back with `--from-url` |
| **"Access blocked / Error 403: access_denied"** | the Google account is not a test user on that project while the app is in Testing | add the account as a test user in the console — a link from an email cannot fix this |

### Re-authorising on the laptop leaves the host holding the old token

The pipeline runs on `169.58.212.7`, the browser is on the laptop. `--serve` saves a token **on the laptop**, and the
host keeps refreshing its own older one, so the channel goes quiet a week later with nothing pointing at the cause.
`--push` closes that half-step — it copies the sealed file and then makes the **host's own uploader** name the channel:

```powershell
python scripts\youtube_auth.py --serve                 # prints both links, waits
python scripts\youtube_auth.py --workflow jephter  --push
python scripts\youtube_auth.py --workflow emmanuel --push
```

`--push` chmods `600` on arrival and runs `youtube_upload.py --check` there, so a wrong-account token fails on the
command that just ran instead of at 17:00. Path chosen for the host-side alternative instead? The paste-back route
needs no listener at all:

```powershell
python scripts\youtube_auth.py --workflow jephter --print-url     # open anywhere
python scripts\youtube_auth.py --workflow jephter --from-url "http://localhost:8765/jephter/callback?code=..."
```

Whichever way: **copy the token to the host before the old one expires** — 7 days after `obtained_at`, which the
reminder mail names as a date.

## Verification

```powershell
python scripts\youtube_auth.py --workflow emmanuel --access-token
# emmanuel: access token ok (...) for <channel title> (<channel id>)
```

Then, from outside the machine:

```powershell
(Invoke-WebRequest http://169.58.212.7/jephter/privacy.html -UseBasicParsing).StatusCode   # 200
```

A 200 here is evidence the pages are served. It is **not** evidence Google will accept them — that needs the domain
step above.
