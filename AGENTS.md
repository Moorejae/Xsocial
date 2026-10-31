# AGENTS.md — read this before you do anything

This is the handoff file for the **X** project. Every agent and every human who works here reads it first. It records
the rules, the verified facts about this project's server, and the two habits this workspace depends on: **a handoff
entry for every change**, and **a skill for everything you learn**.

If you are an agent: read this file top to bottom, then load the skills named in §4, *then* start.

---

## 1. THE ONE HARD PROHIBITION

# 🚫 DO NOT TOUCH THE REZMO VPS — `178.18.245.136`

That machine is **REZMO's production host**. It runs the live API, the database that holds customer data, the mail
sender, and a second product. It is not part of this project and it is not yours.

**"Touch" means all of it, including:**

- No SSH, `ssh-copy-id`, paramiko, `scp`, `rsync`, `ping` sweeps, or port scans against `178.18.245.136`.
- No `docker` commands, no `systemctl` commands, no file reads or writes on it, "just to look".
- No restarting, deploying, migrating, upgrading, or "fixing" anything on it.
- No changes to DNS for `rezmo.online`, `www.rezmo.online`, or `origin.rezmo.online`.
- No reuse of a REZMO credential for this project, and no addition of this project's credential to that host.
- No "quick test" that writes to it. A read is still a connection, and a connection is still a change to its logs,
  its load, and its audit trail.

**Work on `169.58.212.7` — the server in `.env`. That is this project's machine. Nothing else.**

If a task appears to require the REZMO host, **stop and ask the owner**. Do not infer permission. Do not find a
workaround. The whole point of this file is that the two projects stay separate.

### Also out of scope

| Host | Status |
| :--- | :--- |
| `178.18.245.136` | **REZMO production. Forbidden. See above.** |
| `217.77.1.187` | Old ForChi box, decommissioned 2026-10-27 and being deleted. Do not deploy to it or rely on it. |
| `169.58.212.7` | **This project.** Work here. |

---

## 2. What this project is

| | |
| :--- | :--- |
| Folder | `C:\Users\hp\Downloads\X` |
| Server | `169.58.212.7` — Contabo, instance `vmi3701960` (`vmi3701960.contaboserver.net`) |
| Access | SSH port 22 as `root` — see §5 |
| Secrets | `.env` in this folder, git-ignored. Template in `.env.example`. |

Verified on 2026-10-27: the instance exists and is reachable (`vmi3701960.contaboserver.net` resolves from the IP, TCP
22 is open, TCP 443 is closed — so nothing is serving HTTPS yet).

---

## 3. How to reach the server

```powershell
# On this workstation `ssh` on PATH is a 0-byte file that exits 0 and prints nothing.
# ALWAYS use the real binary, by full path.
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'

# Key auth (once the key is installed — preferred):
& $ssh -i <key path> -o StrictHostKeyChecking=accept-new root@169.58.212.7 "hostname; id -un; uptime"

# Password auth: use scripts/vps.py, which reads .env and uses paramiko
python scripts\vps.py --check
python scripts\vps.py "systemctl status --no-pager | head -20"
```

`scripts\vps.py` is the supported way in. It reads `.env`, connects, **writes the command to a file on the server and
runs that file**, then prints stdout, stderr and the exit code. That indirection is deliberate: it removes every
quoting layer between PowerShell, SSH and the remote shell, which is where `$`, backticks and `&&` get silently
mangled.

| Command | What it does |
| :--- | :--- |
| `python scripts\vps.py --check` | connects and prints `hostname`, `id -un`, OS, `nproc`, memory, disk |
| `python scripts\vps.py "<command>"` | runs a command or a whole script body |
| `python scripts\vps.py --file <local.sh>` | uploads a local script and runs it |
| `python scripts\vps.py --put <local> <remote>` | copies a file up |
| `python scripts\vps.py --install-key <pubkey>` | appends a public key to `root`'s `authorized_keys` |

### Verification that it actually ran

**Exit code 0 proves nothing on this machine.** Always make the remote command print its own proof:

```bash
hostname; id -un; date -Is
```

If those lines do not come back, the command did not run, whatever the exit status said.

---

## 4. Use the skill folder

A skill library already exists and it encodes hard-won operational knowledge. **Load the relevant skill before you
start, instead of rediscovering it.** Canonical location:

```
C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\.github\skills\
```

| Load this skill | When |
| :--- | :--- |
| **`contabo-vps`** | **Any time you touch this server.** The three meanings of "log in to Contabo", the paramiko pattern, the 0-byte `ssh` trap, which jobs need the panel instead of SSH. |
| **`build-fallbacks`** | **Before shipping anything that runs unattended.** Independence, retry, restart, escalation, dead-man. See §8. |
| `cloudflare-zone` | DNS, zone settings, certificates, or any `403`/`9101`-family error. Which of the four credentials works, and the error codes that tell you why a call failed. |
| `voice-pipeline`, `voice-cloning` | Text-to-speech work, F5-TTS, cloned voices, transcription. |
| `video-pipeline` | Shorts or long-form video production, ffmpeg assembly, uploads. |
| `social-scheduling` | Posting schedules, timezones, per-platform tokens, double-post risk. |
| `rezmo-*` | Only if the owner explicitly points you at REZMO. Never by default. |

**Skills are read at the start of a task, not guessed at.** If a skill contradicts something you believe, the skill
wins until the owner says otherwise — every one of them was written from a measured failure.

### Project-local skills

Skills learned *in this project* go in `X\skills\<name>\SKILL.md`, same format as the library. See
[X\skills\README.md](skills/README.md).

| Load this project skill | When |
| :--- | :--- |
| **`x-vps-access`** | Reaching `169.58.212.7`; the `ssh`-on-PATH trap; key-then-password connection. |
| **`x-gemini-key-pool`** | Anything touching Gemini keys or script writing: a failed generation, a 404/503/429, adding or rebalancing keys, or printing a config value. |
| **`x-google-console-oauth`** | Creating or fixing a client's OAuth client, consent-screen rejection, uploads that die after a week, or moving the privacy/terms pages to a real domain. |
| **`x-video-pipeline`** | Producing a short for either channel; a render with no audio, no captions, the wrong font or the wrong length; a song or clip that keeps recurring. |
| **`x-voice-personas`** | A video that sounds wrong, fast, loud or not whispery; choosing a voice; keeping the two channels' voices distinct; TTS mime or 429 problems. |
| **`x-test-video`** | The owner asks for a video, a test video, a sample or a re-render. It carries the delivery convention — `--deliver` puts the file and its report in `C:\Users\hp\Downloads`. |

---

## 5. Getting in — current state, verified

**Status 2026-10-27: access works over a key. The password is a fallback, not the way in.**

| | |
| :--- | :--- |
| Host | `169.58.212.7` = `vmi3701960` (Contabo), Ubuntu 24.04.5, 6 cores, 12 GB RAM, 193 GB disk |
| Primary credential | **`CONTABO_SSH_KEY_PATH`** → `C:\Users\hp\.ssh\x-vps-ed25519` (ED25519, unencrypted, comment `x-vps-20261027`) |
| Fallback | `CONTABO_LOGIN_PASSWORD` in `.env` — used automatically if the key is missing or refused |
| Server-side | that public key is the **only** line in `/root/.ssh/authorized_keys` (`SHA256:53648u+PI8sgDOcXTQTdIy6N+4h4RmXkgnM0lPSSMJQ`) |

```powershell
cd C:\Users\hp\Downloads\X
python scripts\vps.py --check      # prints hostname, OS, cores, memory, disk, load
```

Verified output, so you know what "working" looks like:

```text
# connected to 169.58.212.7 as root over key x-vps-ed25519
vmi3701960 / root / Ubuntu 24.04.5 LTS / cores: 6
```

`vps.py` tries the key first and falls back to the password, reporting which one it used. **A broken key does not lock
the project out**, and a rotated password does not either, as long as one of the two is valid.

### If you cannot get in

1. `python scripts\vps.py --check` and read the last line: it names the methods it tried and the last error.
2. The password comes from `my.contabo.com` → instance `vmi3701960` → initial password, or **reset password**. Put it
   in `CONTABO_LOGIN_PASSWORD`.
3. If the key was removed from the server, re-install it with
   `python scripts\vps.py --install-key "$env:USERPROFILE\.ssh\x-vps-ed25519.pub"` — which needs the password to work.
4. If SSH itself is refused, the box is not reachable: Contabo panel → **rescue mode** or a restart.

### Dead ends — do not repeat these

Each was tried once on 2026-10-27 and failed. Only the password and the key above work.

| Attempted | Result |
| :--- | :--- |
| `keys\rezmo-contabo-ed25519.key` (the REZMO host's key) | denied — **and it must stay that way; do not copy REZMO's key here** |
| `keys\rezmo-vm-ed25519.key` / `_rsa.key` | denied / invalid key file |
| the root password held in ForChi's `.env` | denied — different host, different password |
| Contabo REST API with the `CONTABO_CLIENT_ID` / `_SECRET` found in ForChi's `.env` | `401 unauthorized_client` — those API credentials are invalid. Mint a fresh API client in the panel if API access is wanted. |

### Two traps that cost time here

- **`ssh` on PATH is a 0-byte file that exits 0 and prints nothing.** Use
  `C:\Windows\System32\OpenSSH\ssh.exe` by full path, always.
- **Generating a key in PowerShell:** `ssh-keygen -N '""'` writes the literal characters `""` as the passphrase, which
  produces an *encrypted* key that then fails unattended with `PasswordRequiredException`. Generate it through
  `cmd.exe` with `-N ""`, and check it immediately with `ssh-keygen -y -f <key> -P ""` — an unencrypted key prints its
  public half instead of prompting.

---

## 6. The handoff habit — a file for every change

**Every change gets a handoff entry. No exceptions, no matter how small.** The failure this prevents is an agent
re-deriving something that was already established, or re-introducing something already fixed.

Append to [HANDOFF_LOG.md](HANDOFF_LOG.md), newest entry at the top, using this shape:

```markdown
## YYYY-MM-DD (one line naming the change)

**Plane(s):** which machine(s) and which part of the system.

**Asked:** what the owner requested, in their words if possible.

**Changed:** what you changed, file by file, with the reason.

**Verified:** the evidence. A command and its output, a URL and its status code, a test count.
"Looks right" is not evidence.

**Left open:** anything you did not finish, and the next step. Say "nothing" if nothing.
```

Rules for the log:

- **Newest first.** The next agent reads the top.
- **Evidence, not assurance.** Paste the check you ran and what it returned.
- **Record the dead ends.** "Tried X, it failed with Y, do not repeat" is the most valuable line in a handoff.
- **Never a credential value.** Name the setting; never paste its contents.

---

## 7. The skill habit — a file for everything you learn

When you work something out that a future agent would otherwise have to rediscover, **write it down as a skill.**
Not a note, not a comment in a chat: a skill file.

- Project-specific → `X\skills\<kebab-name>\SKILL.md`
- Useful to every project on this machine → also copy into the canonical library (ask the owner first, because that
  library is versioned in another repository)

Use the existing format: YAML frontmatter with `name` (must equal the folder name) and a `description` that says what
the skill contains **and when to use it**, then short sections with tables and commands. Open with the "when to use"
block so a reader can decide in five seconds.

Good candidates, from work already done in this workspace: a VPS bootstrap recipe, an unusual API's auth dance, a
failure mode that cost an hour, a deploy sequence with a trap in it.

Do **not** write a skill for something already covered — extend the existing one instead.

---

## 8. Build fallbacks, always

Nothing you ship may depend on another thing running, and nothing may fail silently. The short version:

| Rung | Requirement |
| :--- | :--- |
| **L0 retry** | transient errors retried, bounded, with backoff. Only if the operation is idempotent — a retried upload that has no ledger claim publishes twice. |
| **L1 supervise** | systemd `Restart=always` + `StartLimitBurst` so a crash-loop is visible instead of infinite. |
| **L2 detect** | a watchdog that notices a dead unit or a stuck job. |
| **L3 escalate** | when restarts are exhausted, **email `agumoorewe@gmail.com`** (`.env` → `ALERT_EMAIL`) with WHAT / WHY / TRIED / ACTION. |
| **L4 dead-man** | a heartbeat checked from **outside** the machine, so silence alerts. Errors produce logs; absence produces nothing. |

Full detail: `build-fallbacks` in the skill library. A fallback that has never been observed firing is a hope, not a
fallback — test it.

---

## 9. Credentials and safety

- **Never print a value** from `.env`, not even a prefix, not even "to confirm". Print a derived fact: set/unset,
  length, shape, hash.
- `node C:\Users\hp\Downloads\REZMO-FRONTEND-DESIGN-SPEC\scripts\env-shape.mjs <file>` lists settings by name with
  length and shape, and reports duplicates. It never prints a value.
- **Never commit `.env`.** `.gitignore` covers it and copies of it by shape.
- Do not copy another project's key onto this host, and do not copy this host's credential into another project.
- Destructive commands (`rm -rf`, `dd`, `mkfs`, `apt purge`, a database drop) need the owner's explicit go-ahead in
  the current conversation. "It looked safe" is not one.
- If a value ever does reach a transcript, **say so immediately**, name which value and how much of it, and ask for it
  to be rotated.

---

## 10. GitHub

| | |
| :--- | :--- |
| Remote | `origin` → `https://github.com/Moorejae/Xsocial.git` — **public** |
| Owner | `Moorejae` |
| Token | `GITHUB_TOKEN` in `.env` — fine-grained PAT. Never commit it, never print it. |
| Git auth | the **`gh` CLI credential helper** (`gh auth setup-git`), so the token is never written into `.git/config` |
| Branch | `main` |

```powershell
git add -A
git commit -m "..."
git push origin main
```

### This repository is PUBLIC

Anything committed here is world-readable and effectively permanent — deleting a commit does not un-publish it, because
forks, caches and crawlers keep copies. So:

- **Never commit `.env`, keys, tokens, or anything derived from them.** `.gitignore` covers env files by shape.
- **Never commit customer data, personal data, or logs that contain either.**
- If you need to store a secret for a workflow, use the GitHub Actions secret store, not a file.
- Before the first commit of any new file, ask: *would I be comfortable if a stranger read this?*

### Creating another repository

The owner's fine-grained PAT can push to repositories it is scoped to. Creating a **new** repository needs
`Administration: write` on the token, which a repo-scoped PAT does not have — so use the `gh` CLI, whose login carries
the `repo` scope:

```powershell
gh repo create <name> --public --description "..."
git remote add origin https://github.com/Moorejae/<name>.git
git push -u origin main
```

Via the API instead: `POST https://api.github.com/user/repos` with `{"name": "...", "private": false}`. If that answers
`403`/`404`, it is the token's scope, not the payload — the error message says "Resource not accessible" rather than
anything about permissions.

---

## 11. Before you report done

- [ ] The thing runs, and you have the command output that proves it.
- [ ] You re-ran the check after your last change, not before it.
- [ ] Unrelated behaviour is unchanged.
- [ ] `HANDOFF_LOG.md` has an entry with evidence.
- [ ] Any new knowledge is a skill.
- [ ] No credential appears anywhere in the transcript or the repository.
- [ ] Nothing you did touched `178.18.245.136`.

---

## 12. The product: two independent shorts workflows

Two clients, two YouTube channels, one pipeline each. They are named after the channels: **jephter** and
**emmanuel**. Everything below exists to keep them independent, because the owner's requirement is that one
channel's trouble never becomes the other's.

### The independence contract

| Resource | Rule |
| :--- | :--- |
| Google Cloud project | **one per client.** Quota is per project, so a shared project couples the two workflows |
| OAuth client + refresh token | one per client, obtained in that client's own console |
| Gemini keys | 9 / 9, disjoint (`config/gemini-pools.json`). A key is never in both pools |
| Voice persona | one voice per workflow, chosen by audition — **Leda** vs **Pulcherrima**, both whispery, deliberately different in breath and steadiness |
| Media (videos, music) | separate folders per workflow, and separate labels |
| Song used | separate libraries; the same track never repeats within 3 videos, and the excerpt starts somewhere different every time |
| Schedule | separate, so a failed run on one cannot blank the other's day (nothing is scheduled yet) |

### The video brief, as the owner set it

Romance and couple-focused scripts for both channels; **each video at most 50 seconds**; each workflow uses its own
videos and its own songs; scenes are cut ForChi-style (hard cuts every ~2.6-4.8 s, never a crossfade); songs are
labelled and edited to length rather than chopped up, but **the excerpt starts somewhere different in every video so
no two videos sound the same**; captions in REZMO's own typeface (Jost) and **bold**; the Gemini keys use the same
waterfall as ForChi (a key that is refused or out of quota is demoted, and the next one is tried).

### What exists, and what runs it

| Path | Purpose | Command |
| :--- | :--- | :--- |
| `config/workflows.json` | brand, channel, contact, persona, **video/voice/caption/music settings**. No secrets | — |
| `config/gemini-pools.json` | which **key ids** belong to which workflow. No values | — |
| `config/scenes/<wf>.json` | the measured + vision-labelled clip manifest | `python scripts\x_media.py --label <wf>` |
| `config/songs/<wf>.json` | the measured song manifest | same command |
| `scripts/gemini_keys.py` | key split, rotation, model ladder, TTS, validation | `python scripts\gemini_keys.py --audit` |
| `scripts/x_media.py` | label clips and songs; `--vision` adds subject/baked-text | `python scripts\x_media.py --label jephter` |
| `scripts/x_script.py` | romance script writer + the ending guard | `python scripts\x_script.py --workflow jephter` |
| `scripts/x_voice.py` | TTS narration, one wav per line, audition mode | `python scripts\x_voice.py --workflow jephter --audition` |
| `scripts/x_render.py` | cut plan, song window, captions, assembly | `python scripts\x_render.py --workflow jephter` |
| `scripts/x_ffmpeg.py` | shared ffmpeg plumbing (peak gain, never `loudnorm`) | — |
| `scripts/make_fonts.py` | Jost Bold from REZMO's own variable font | `python scripts\make_fonts.py` |
| `scripts/make_assets.py` | the caption scrim | `python scripts\make_assets.py` |
| `scripts/bootstrap_vps.py` | ffmpeg + fonts on the VPS, then verify | `python scripts\bootstrap_vps.py` |
| `scripts/youtube_auth.py` | loopback consent → refresh token → `secrets/youtube/<workflow>.json` | `python scripts\youtube_auth.py --workflow jephter` |
| `scripts/build_site.py` / `deploy_site.py` | the privacy/terms pages Google requires | `python scripts\build_site.py` |
| `jephtah/`, `emmanuel/` | the owner's clip and song libraries (git-ignored) | — |
| `out/<workflow>/` | renders, reports, voice cache, usage ledger (git-ignored) | — |

Load `x-gemini-key-pool` before touching keys, `x-google-console-oauth` before touching a console,
`x-video-pipeline` before touching a render, and `x-voice-personas` before touching a voice.

### One command, per channel

```powershell
python scripts\x_render.py --workflow jephter  --deliver   # script -> voice -> cuts -> song -> mp4
python scripts\x_render.py --workflow emmanuel --deliver
```

`--deliver` copies the result to `C:\Users\hp\Downloads\<channel>_<stamp>_<seconds>s.mp4` **with its report beside
it** — that is the deliverable, and it is the owner's convention. Each build also writes `out/<workflow>/<name>.json`
(phrases, per-line durations, every clip with its mood/subject/letterbox rect, the song and its offset, levels,
`under_cap`). The sidecar is the evidence; read it, and look at a frame, because a file that exists is not a file
that is right.

### Two facts that decide the console setup

- **`videos.insert` has its own quota bucket: 100 calls per project per day** (Google quota calculator,
  revision 2026-10-15). Two projects therefore allow 200 uploads a day in total, and that is a ceiling worth
  knowing before a schedule is written.
- **An OAuth app left in Testing status issues refresh tokens that expire in 7 days**, unless the only scopes are
  name/email/profile. `youtube.upload` is not exempt. Publish the app, or the pipeline stops working next week.

### Guards, and the one command a fresh clone needs

```powershell
git config core.hooksPath .githooks          # per clone: the pre-commit guard is inert until this runs
python scripts\gemini_keys.py --audit        # 9/9, disjoint, balanced, nothing unowned. No network.
python scripts\youtube_auth.py --selftest    # encryption at rest actually seals. No network.
```

The refresh token and client secret are sealed with AES-GCM under a key from `X_SECRET_KEY` in `.env` (falling
back to `secrets/master.key`), because the privacy policy served to Google states that the token is encrypted at
rest. If that ever stops being true, the page text has to change in the same commit.

### Still open

- A **domain → `169.58.212.7` → TLS** for the privacy/terms pages. Until then the pages answer on
  `http://169.58.212.7/…`, which is enough to review but not enough for Google verification.
- **The uploader.** `youtube_auth.py` obtains the credential; nothing calls `videos.insert` yet.
- **Scheduling and the L1–L4 rungs** from §8: systemd supervision, a watchdog, an email on exhaustion, and an
  external heartbeat. Nothing runs unattended today.
- **Voice cloning.** Both channels are on Gemini TTS today. A real clone per workflow (F5-TTS, as ForChi uses) needs
  one reference clip per workflow; `x_voice.synthesize_phrase` is the seam.
- **TTS quota** is the tightest limit in the pipeline. It ran out mid-build on 2026-10-28; see `x-voice-personas`.

