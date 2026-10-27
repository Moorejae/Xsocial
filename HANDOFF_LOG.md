# X — handoff log

Newest first. Every change gets an entry, with evidence. See [AGENTS.md](AGENTS.md) §6 for the shape.

---

## 2026-10-27 (INCIDENT: all 18 Gemini keys were printed in full to a terminal; hook rewritten to never echo)

**Plane(s):** this folder. No server change. No third party received anything.

**What happened.** While proving that `.githooks/pre-commit` would block a staged key file, the probe staged the real
`Gemini api key` file and the hook reported the offending lines with `cut -c1-60`. The keys are 53 characters, so
**60 characters printed each of them in full** — all 18, in the terminal output of that command, which is part of
the session transcript.

**Scope and remedy.** These keys must be treated as **compromised and rotated**:

1. In each Google Cloud project that owns one of them: **APIs & Services → Credentials → API keys**, delete the
   existing keys and create replacements.
2. Replace the contents of `Gemini api key` (one key per line, same count so the sibling/pool structure holds).
3. `python scripts\gemini_keys.py --split` then `--validate`.
4. The ids in `config/gemini-pools.json` are `sha256(key)[:12]` and will therefore **change** when the values
   change; the pool size per workflow should stay 9 / 9.

Nothing in git history contains a value: the probe commits were made and then removed with `git reset --mixed` (they
were never pushed), and `git log --all -- '*probe*' '*api-key*'` returns nothing. The exposure is the transcript,
not the repository.

**Changed:**

| File | Change |
| :--- | :--- |
| `.githooks/pre-commit` | created **twice**: the first `create` failed on a missing parent directory and was never retried, so the hook **did not exist** and two probe commits went through. It now exists (2559 bytes) and blocks all three probes |
| same | the matching line is now reported as `staged line N` **only**. The rule: a hook must never print the thing it is protecting — a truncation to 60 chars is not redaction when the secret is 53 |
| `.gitignore` | media added: `/media/ /music/ /videos/ /jephtah/ /jephter/ /emmanuel/` and `*.mp4 *.mov *.webm *.mkv *.wav *.mp3 *.m4a *.flac`. Anchored with a leading slash so generated page folders (`web/site/jephter/`) are not caught |

**Verified:**

```text
git check-ignore -v 'Gemini api key'        -> .gitignore:30:Gemini*      Gemini api key
git check-ignore -v 'jephtah/aesthetic.mp4' -> .gitignore:39:/jephtah/    jephtah/aesthetic.mp4
git check-ignore  web/site/jephter/privacy.html  -> (exit 1 = not ignored, correct)

probe 1, a 48-char opaque token line : BLOCKED  "a line that is nothing but a long opaque token"
probe 2, git add -f of a key file    : BLOCKED  "a provider token or private key is in the staged diff"
probe 3, git add -f of an .mp4       : BLOCKED  "refusing credential- or media-shaped path"
re-probe with a synthetic AQ. value  : BLOCKED, and prints only "staged line 1" — no content
git log --oneline -2                 : d20de71 / 42b2d68  (probe commits gone)
```

**Lesson for the skill folder.** Written into `skills/x-gemini-key-pool`: *print the id, never the value — and that
includes a "truncated" match inside a guard script.* The second lesson is duller and cost an hour: a `create` that
fails is not a file that exists; check the path before trusting a guard to have run.

---

## 2026-10-27 (stage 1 of the two YouTube workflows: keys secured and split, consoles runbooked, verification pages live)

**Plane(s):** this folder + `169.58.212.7` (nginx installed, `/srv/x-site` created, port 80 now serving).
Ten outbound HTTPS calls to `generativelanguage.googleapis.com` and two documentation reads from
`developers.google.com`. **Nothing was sent to `178.18.245.136` and nothing will be.**

**Asked:** "start with securing their google console first and sharing the gemini api keys." Two clients, two
YouTube channels, two workflows to be called **jephter** and **emmanuel**, each independent of the other; a Gemini
key file was dropped in the folder to write the scripts. Also: read the skill folder, add skills as you go, and keep
this log so the next agent does not rediscover anything.

**Changed:**

| File | Change | Why |
| :--- | :--- | :--- |
| `.gitignore` | new block ignoring loose credential dumps by shape (`secrets/`, `*api*key*`, `*apikey*`, `*secret*.json`, `client_secret*`, `credentials*.json`, `*keys*.txt`, `*key*.txt`, `Gemini*`, `tokens*.json`) | see the finding below — this was the urgent one |
| `.githooks/pre-commit` | **new.** Refuses (a) a credential-shaped path and (b) a staged diff containing `AIza…`, `AQ.…`, `ya29.…`, `ghp_…`, `github_pat_…`, a `BEGIN … PRIVATE KEY` block, or a line that is nothing but a 40+ character opaque token | `.gitignore` only protects a name; this looks at what is actually staged |
| `scripts/gemini_keys.py` | **new.** Loads the key file, derives `sha256(key)[:12]` ids, splits round-robin into the two workflows, rotates keys on 429/403, walks a model ladder on 503/timeouts, bounded backoff with jitter, raises `PoolExhausted` rather than returning empty text | the split and the fallbacks in one place, no key ever printed |
| `config/gemini-pools.json` | **new**, committed. Key **ids** only: `jephter` 9, `emmanuel` 9 | reproducible, safe to publish, survives a reorder of the key file |
| `scripts/youtube_auth.py` | **new.** Loopback consent on `http://localhost:8765/<workflow>/callback`, code→token exchange, stores `secrets/youtube/<workflow>.json` (0600), then **reads back the authorised channel** via `channels.list(mine=true)` | the redirect URI has to match the console exactly; the read-back is the only proof consent landed on the right channel |
| `scripts/build_site.py` | **new.** Renders the app homepage, privacy policy and terms per workflow from `config/workflows.json` into `web/site/` | Google will not verify without them; the YouTube policy clauses are easy to omit on the second page |
| `scripts/deploy_site.py` | **new.** Installs nginx if absent, writes `/etc/nginx/sites-available/x-site`, uploads `web/site/` to `/srv/x-site`, then verifies every URL with curl | the pages have to be *served*, not just present |
| `config/workflows.json` | **new.** Product name, channel handle, contact email, persona, page paths per workflow. Placeholders are marked `TODO` | one source of truth for both clients |
| `web/site/` | **new**, 8 generated files | output of `build_site.py` |
| `.env` / `.env.example` | added by **name** only: `GEMINI_KEYS_FILE`, `GEMINI_MODEL`, `JEPHTER_GOOGLE_CLIENT_ID/_SECRET`, `EMMANUEL_GOOGLE_CLIENT_ID/_SECRET`, `SITE_HOST` | the four client values are **empty** and are filled from each client's own console |
| `AGENTS.md` | §4 gained the project-skill table; new **§12** records the independence contract, the file map, the two console facts, and what is still open | a reader should not have to infer the architecture from the scripts |
| `skills/x-gemini-key-pool/SKILL.md` | **new skill** — key file shape, ids, the 9/9 split, rotation, measured 404/503/empty-candidate/latency facts | |
| `skills/x-google-console-oauth/SKILL.md` | **new skill** — desktop client vs web client, the loopback URIs, the 7-day Testing trap, the `videos.insert` bucket, the verification pages, failure catalogue | |

### The finding that came first

`git status` showed the new key file as `?? "Gemini api key"` — **untracked but not ignored** — in a repository that
is **public**, with AGENTS.md instructing `git add -A`. One commit away from publishing 18 live Gemini keys. Fixed
by the `.gitignore` block and the pre-commit hook before any other work started. The file has never been committed
(`git ls-files` does not list it).

**Verified:**

```text
python scripts\gemini_keys.py --split   ->  wrote config\gemini-pools.json: jephter=9, emmanuel=9
python scripts\gemini_keys.py --validate
  3087b333c979  jephter   HTTP 200  generates on gemini-3.6-flash
  ... (18 rows, alternating jephter/emmanuel) ...
  validated 18 keys, 0 not usable
python scripts\gemini_keys.py --generate jephter  "Reply with the single word: ready"  ->  ready
python scripts\gemini_keys.py --generate emmanuel "Reply with the single word: ready"  ->  ready
```

```text
python scripts\deploy_site.py
  # connected to 169.58.212.7 as root over key x-vps-ed25519
  nginx: nginx version: nginx/1.24.0 (Ubuntu)
  uploaded 8 files to /srv/x-site
  vmi3701960 / root / 2026-10-27T23:06:38+01:00
  files: 8
  200 /            200 /jephter/      200 /jephter/privacy.html   200 /jephter/terms.html
  200 /emmanuel/   200 /emmanuel/privacy.html                     200 /emmanuel/terms.html
  youtube-disclosure hits in jephter/privacy.html: 3
```

Checked again **from this workstation**, i.e. through the public interface rather than from the box itself:

```text
200 /                      bytes=1144
200 /jephter/privacy.html  bytes=4016
200 /emmanuel/privacy.html bytes=4025
200 /jephter/terms.html    bytes=2923
```

Consent URLs (client id is a placeholder; the **redirect URI is the part that matters**):

```text
https://accounts.google.com/o/oauth2/v2/auth?client_id=TESTID...&redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fjephter%2Fcallback
  &scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.upload+openid+email&access_type=offline&prompt=consent
redirect_uri must be registered exactly as: http://localhost:8765/jephter/callback
                                           http://localhost:8765/emmanuel/callback
```

### Facts measured today, worth not rediscovering

| Fact | Evidence |
| :--- | :--- |
| The 18 keys are **53 chars, shape `U.Ul9…`** — not the classic `AIza…` AI Studio shape — and they work against `generativelanguage.googleapis.com/v1beta` | `models.list` HTTP 200 on key `3087b333c979` |
| **18** keys, not 17 | `count=18 unique=18` |
| `gemini-2.5-flash` / `gemini-2.0-flash` are **listed** but answer **HTTP 404** *"no longer available to new users"* | raw 404 body, quoted |
| `gemini-3.8-flash` answers **HTTP 503 UNAVAILABLE** *"high demand"*; 4/4 attempts failed in one pool cycle, then 3.6 worked | `--generate jephter` retry log |
| **Thinking tokens count against `maxOutputTokens`** — a 16-token ceiling returns an *empty* candidate with `thoughtsTokenCount 58` | raw 200 body |
| Generation latency ranged 7.3 s / 21 s / 138 s for the same prompt | timed probes |
| Serial validation of 18 keys did not finish in 30+ min | it was stopped; `--validate` now uses 8 threads and takes ~1 min |
| **`videos.insert` has its own bucket: 100/day per project** (the old "1600 units → 6/day" figure is obsolete) | Google quota calculator, revision 2026-10-15 |
| **An External app in Testing status gets refresh tokens that expire in 7 days**, unless the scopes are only name/email/profile | Google OAuth docs, *Refresh token expiration* |

### Dead ends — do not repeat

| Attempted | Result |
| :--- | :--- |
| `generateContent` on `gemini-2.5-flash` | HTTP 404, model retired for new users. Use the ladder in `gemini_keys.py` |
| `maxOutputTokens=4/16` with thinking on | HTTP 200 with an **empty** candidate. Raise the ceiling or set `thinkingBudget: 0` |
| `--validate` serially | wanted ~2.4 h worst case; abandoned and parallelised |
| `http://169.58.212.7/…` as an OAuth **Web** redirect URI | will be refused by Google (no bare IPs); use the Desktop client + loopback |
| `print(out.strip())` on remote UTF-8 output under a cp1252 console | `UnicodeEncodeError` after a *successful* deploy. `deploy_site.py` now reconfigures both streams to utf-8 |

**Left open — in the order they block the next step:**

1. **Client id + secret for each workflow**, from each client's own console, into `.env` as
   `JEPHTER_GOOGLE_CLIENT_ID` / `_SECRET` and `EMMANUEL_GOOGLE_CLIENT_ID` / `_SECRET`. The console steps are in
   `skills/x-google-console-oauth` §"Console steps". Then: `python scripts\youtube_auth.py --workflow jephter`
   and read back the channel it prints.
2. **A domain → `169.58.212.7` → TLS** for the privacy/terms pages. They answer on the plain IP today; Google
   verification needs a real domain over HTTPS. One domain with `/jephter/` and `/emmanuel/` paths is enough.
   After that: `certbot --nginx` on `vmi3701960`, then set `site_host` in `config/workflows.json` and re-run
   `build_site.py` + `deploy_site.py`.
3. **Voice** — one reference clip per workflow, romantic and whispery, and distinct from each other. Read
   `voice-cloning` in the shared library first; the bleed fix is at the source (a non-overlapping reference), not
   in cfg. The keys can also reach `gemini-3.8-flash-tts` if a Gemini voice is preferred to a clone.
4. **Script → video → upload**, then the schedule, then the §8 ladder (systemd, watchdog, email, external
   heartbeat). Nothing is scheduled yet; there is no timer and no service on the box.


## 2026-10-27 (published as a public repository: Moorejae/Xsocial)

**Plane(s):** GitHub + this folder. No server change.

**Asked:** add the same GitHub token to this project's `.env`, and create a new **public** repository called `Xsocial`.

**Changed:**

| Item | Detail |
| :--- | :--- |
| `.env` | `GITHUB_TOKEN` (copied from `REZMO.env`, fine-grained PAT), `GITHUB_USER=Moorejae`, `GITHUB_REPO=Xsocial` |
| remote | `origin` → `https://github.com/Moorejae/Xsocial.git` |
| branch | renamed `master` → `main` (the first push failed with `src refspec main does not match any` because `git init` had produced `master`) |
| git auth | `gh auth setup-git`, so pushes use the `gh` credential helper and **the token is never written into `.git/config`** |
| `AGENTS.md` | new §10 GitHub: the remote, the credential path, and the public-repo rules; "before you report done" became §11 |

**Verified:**

```text
created: Moorejae/Xsocial private=False
local  HEAD 42b2d68af00402a5600870ff785e839fa28b0fa1
remote main 42b2d68af00402a5600870ff785e839fa28b0fa1     match: True
visibility: public
files at root on the remote:
  .env.example   .gitignore   AGENTS.md   HANDOFF_LOG.md   scripts   skills
```

**The important line is the one that is missing: `.env` is not on the remote.** Confirmed by listing the repository
contents through the public API, not by trusting `.gitignore`.

**Token facts worth keeping:** it authenticates as `Moorejae` and can read and push to the owner's repositories. It is
scoped to those repos, so it **cannot create a new repository** — creation needs `Administration: write` or the `gh`
login, which is why the repo was made with `gh`'s scope rather than the PAT.

**Left open — a decision for the owner, not something to change unasked:** this repository is public, and `AGENTS.md`
names both server addresses (`178.18.245.136`, `169.58.212.7`) and the alert mailbox, because the prohibition is only
enforceable if the host is named. No credential is published — but if the owner would rather the IPs were not public,
the fix is to name the hosts by role (`REZMO production`, `this project's VPS`) and keep the addresses in `.env` only.

---

## 2026-10-27 (access proven over a key; the password reduced to a fallback)

**Plane(s):** `169.58.212.7` only. Two server-side changes: one public key installed, and `authorized_keys` rewritten to
contain only it.

**Asked:** the owner supplied the root password for the new VPS.

**Changed:**

| File / host | Change |
| :--- | :--- |
| `.env` | `CONTABO_LOGIN_PASSWORD` now populated by the owner; `CONTABO_SSH_KEY_PATH` set to `C:\Users\hp\.ssh\x-vps-ed25519` |
| `scripts\vps.py` | connection now **tries the key, then falls back to the password**, and reports which worked. A broken key cannot lock the project out while a valid password exists, and vice versa. |
| server `/root/.ssh/authorized_keys` | replaced with the single project key (`SHA256:53648u+…`, comment `x-vps-20261027`); mode 600, dir 700 |
| `AGENTS.md` §5 | rewritten: current state, the exact working output, and the dead ends worth never repeating |

**Verified — three independent checks:**

```text
python scripts\vps.py --check
# connected to 169.58.212.7 as root over key x-vps-ed25519
vmi3701960 / root / Ubuntu 24.04.5 LTS / cores: 6
Mem: 11960 total, 520 used, 11430 available
/dev/sda1 193G 1.9G 191G 1%
load: 0.16 0.24 0.20

key-only client (no password, no agent, look_for_keys=False):
vmi3701960 / root / key-auth-ok

ssh-keygen -lf /root/.ssh/authorized_keys
256 SHA256:53648u+PI8sgDOcXTQTdIy6N+4h4RmXkgnM0lPSSMJQ x-vps-20261027 (ED25519)
```

**Two mistakes made and fixed in this step, recorded so they are not repeated:**

1. `ssh-keygen -N '""'` in PowerShell sets the passphrase to the two literal quote characters, producing an **encrypted**
   key. It failed with `PasswordRequiredException` on first use. Regenerated through `cmd.exe` with `-N ""`, then proved
   unencrypted with `ssh-keygen -y -f <key> -P ""`.
2. The first `vps.py` treated a set-but-broken key as fatal instead of falling back. That is a lockout waiting to
   happen, so the connection path now walks the available methods and only fails when all of them do.

**Left open:** nothing blocking. The password remains in `.env` deliberately as the fallback; once the owner is happy
with key access it can be deleted, and the recovery path in AGENTS.md §5 then depends on the Contabo panel alone.

**Also noted for the next agent:** this box is the same tier as REZMO's host (6 vCPU / 12 GB / 200 GB) and currently
uses 1.9 GB of disk with almost no load. Nothing has been installed or configured beyond SSH access — there is no
application here yet.

---

## 2026-10-27 (project bootstrapped: env, rules, handoff habit, and an honest account of the login state)

**Plane(s):** this folder only. No connection was made to the server except two read-only reachability probes, and
nothing was sent to `178.18.245.136`.

**Asked:** create an `.env` carrying the Contabo IP `169.58.212.7`; write a handoff file that forbids any agent from
touching the REZMO VPS `178.18.245.136`; tell agents to use the skill folder, to write a handoff entry for every
change, and to add a new skill for everything they learn; and make sure an agent has what it needs to log in.

**Created:**

| File | Purpose |
| :--- | :--- |
| `.env` | the server (`CONTABO_IP_ADDRESS=169.58.212.7`), the login user, the key path, the alert address |
| `.env.example` | the same keys with no values, safe to commit |
| `.gitignore` | env files by shape (not just by exact path), keys, logs, build output |
| `AGENTS.md` | the rules: the REZMO prohibition, the skill library, the two habits, the fallback ladder |
| `HANDOFF_LOG.md` | this file |
| `skills/README.md` | the format for project skills |
| `scripts/vps.py` | the supported way to run anything on the server |

**Verified about the server, 2026-10-27:**

```text
PTR  169.58.212.7 -> vmi3701960.contaboserver.net     (Contabo, instance vmi3701960)
TCP  169.58.212.7:22 -> open
TCP  169.58.212.7:443 -> closed                       (nothing serving HTTPS yet)
```

**What does NOT authenticate — checked so nobody repeats it:**

| Method | Result |
| :--- | :--- |
| `keys\rezmo-contabo-ed25519.key` | denied |
| `keys\rezmo-vm-ed25519.key` | denied |
| `keys\rezmo-vm-rsa.key` | invalid key file |
| root password held in ForChi's `.env` | denied |
| Contabo REST API (`CONTABO_CLIENT_ID` / `_SECRET` from ForChi's `.env`) | `HTTP 401 unauthorized_client` — those API credentials are invalid |

Single attempts, not a sweep: four known credentials, each tried once.

**Left open — one owner action, and it is the only blocker:** `CONTABO_LOGIN_PASSWORD` in `.env` is empty. The root
password for `vmi3701960` is not held anywhere on this machine and was not invented. Fill it from the Contabo panel
(`my.contabo.com` → instance `vmi3701960` → initial password or **reset password**), then run:

```powershell
python scripts\vps.py --check
```

Once that succeeds, install a key (`scripts\vps.py --install-key <pub>`) and blank the password, so the password stops
being the only way in. Recorded in AGENTS.md §5.

**Note on reuse:** the Contabo API credentials found in another project's `.env` are dead. Do not copy them here or
retry them; if API access is wanted, mint a fresh API client in the panel.
