---
name: x-vps-deploy
description: 'How the two X channels are deployed and run on 169.58.212.7: the file layout, the systemd units and timers, what has to be uploaded that git cannot carry, and the ordering that stops two machines double-posting. Use when the schedule stops, when redeploying after a code change, when a pass must be run by hand, or when deciding where a render or a log lives now that the laptop is no longer the host.'
---

# The pipeline lives on the VPS

Moved from `C:\Users\hp\Downloads\X` (a laptop) to `169.58.212.7:/root/X` on **2026-11-03**, because the laptop
slept from 01:50 to 11:34 and the morning's videos simply did not exist. A pipeline that depends on someone's
laptop staying open is not a pipeline.

| | Where |
| :--- | :--- |
| Code, config, docs | the repo — `C:\Users\hp\Downloads\X` here, `/root/X` there (a clone of `Moorejae/Xsocial`) |
| Media (`jephtah/`, `emmanuel/`) | **both machines, one copy each, on purpose.** The laptop is where clips are added and labelled; the VPS renders from its own copy. `scripts/sync_media.py` keeps them in step |
| Renders, logs, ledger, state | **VPS only**, under `/root/X/out/` |
| Python | `/root/X/.venv` (cryptography, fonttools, numpy, pillow). ffmpeg is the distro's, 6.1.1 |
| Schedule | `x-daily.timer` (15 min) → `scripts/x_daily.sh`; `x-watchdog.timer` (30 min) → `scripts/x_watchdog.sh` |

## When to use

- A video did not appear and you need to know whether the timer, the tests, or the pass failed.
- You changed code here and the VPS needs it.
- You need to run a pass by hand on the VPS.
- You are tempted to re-enable the Windows tasks — read the ordering rule below first.

## The ordering rule that prevents a double post

The publish ledger is **per machine**. Two hosts with two ledgers will publish the same video twice. So:

```
1. disable the other side first        (Disable-ScheduledTask / systemctl disable --now)
2. copy out/publish_ledger.json        before the new host posts anything
3. only then enable the new host
```

Done on 2026-11-03 in exactly that order: the Windows tasks were disabled *before* the first VPS pass ran.

## What git cannot carry

`git clone` gets the code, `config/`, `fonts/` and `assets/` — all committed. It does **not** get:

| Item | Why | How |
| :--- | :--- | :--- |
| `jephtah/`, `emmanuel/` | git-ignored media, 443 MB, 119 files — **both machines keep a copy**: the laptop is where clips are added, the VPS is what renders | `python scripts\sync_media.py` (sends only what differs; `--verify` for a comparison) |
| `Gemini api key` | git-ignored, by shape | `python scripts\vps.py --put "Gemini api key" "/root/X/Gemini api key"` |
| `.env` | git-ignored | build it and `--put`; never echo it |
| `secrets/master.key`, `secrets/youtube/*.json` | sealed tokens | `--put` each |
| `out/publish_ledger.json`, `out/schedule_state.json`, `out/<wf>/usage.json`, `out/script_usage.json` | state, so the new host does not re-post | `--put` |

**A clip that is not synced is a clip that never appears**, and the symptom looks like a broken picker rather
than a missing file. So the order is: add clips → `label_emotions.py` → `sync_media.py` → the next timer pass
picks them up (nothing needs restarting; the renderer reads from disk).

```powershell
python scripts\sync_media.py --dry-run       # the plan
python scripts\sync_media.py                 # clips + manifests
python scripts\sync_media.py --verify        # 0 = both sides identical, 1 = drift
```

Measured 2026-11-03 after copying back: `jephtah` 251,284,238 bytes / 65 files on both, `emmanuel` 213,002,168
bytes / 54 files on both, `--verify -> identical`. Every labelled clip resolves on both sides (jephter 54/54,
emmanuel 43/43).

**Watch the line endings.** A manifest is CRLF here and LF there, so the *sizes* differ for identical content
(26,710 vs 25,654 bytes for `jephter.json`). `sync_media.py` hashes the text with `\r\n` normalised for exactly
this reason; a size comparison reported four unchanged files as drift on every run.

**`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` must be in the VPS `.env`.** On the laptop the credential chain
falls back to `C:\Users\hp\forchi\.env`, which does not exist on the VPS, so `--check` and any consent link fail
with *"no OAuth client found"*. Copy the two values from that file into `/root/X/.env` once. Posting still works
without them (the sealed token carries its own client), but re-authorisation does not.

## Media transfer: `scp -r` for the first copy, `sync_media.py` for every one after

The first copy is a bulk transfer; after that, use the script so only differences move.

| Attempt | Result |
| :--- | :--- |
| `tar -czf - dir \| ssh "tar -xzf -"` | **died at 17 MB** with `gzip: unexpected end of file` — the pipe closed early |
| `tar -czf media.tgz` locally first | **access violation**, exit `-1073741819`, 17 MB written |
| `scp -i <key> -q -r jephtah emmanuel root@169.58.212.7:/root/X/` | worked, ~800 KB/s, 443 MB in about 14 minutes total |

`scp -r` is slower to start and has no progress bar with `-q`, but it finishes. Re-running it for a partially
copied folder re-sends that folder, which is fine at this size.

**Always compare before deleting the source.** Byte totals and file counts, both ways:

```powershell
foreach ($d in @('jephtah','emmanuel')) {
  $f = Get-ChildItem $d -Recurse -File
  "{0}: {1} bytes, {2} files" -f $d, (($f | Measure-Object Length -Sum).Sum), $f.Count
}
```

```bash
for d in jephtah emmanuel; do printf "%s: %s files\n" "$d" "$(find "$d" -type f | wc -l)"; done
du -sb jephtah emmanuel
```

Measured 2026-11-03, both sides identical: `jephtah` 251,284,238 bytes / 65 files, `emmanuel` 213,002,168 bytes /
54 files, extensions 97 mp4, 20 mp3, 1 apk, 1 ogg. Only then were the local copies removed (1.36 GB freed).

## The two traps in the shell scripts

1. **CRLF.** `vps.py --put` copies bytes verbatim, so a script written on Windows arrives with `\r\n` and dash
   fails with **`Syntax error: Bad fd number`** on the `2>&1` line. Always `sed -i 's/\r$//'` after uploading, then
   `chmod +x`.
2. **`vps.py` has a timeout.** A pass that renders can exceed it and the client raises `PipeTimeout` while the
   remote command keeps running. That is not a failure — check with `pgrep -af x_render` before assuming anything.
   Use the systemd units for anything long.

## Running a pass by hand

```powershell
# the supported way: let systemd run the real thing and read the journal
python scripts\vps.py "systemctl start x-daily.service; journalctl -u x-daily -n 40 --no-pager --output=cat"

# state and logs
python scripts\vps.py "tail -25 /root/X/out/scheduler.log"
python scripts\vps.py "tail -10 /root/X/out/watchdog.log"
python scripts\vps.py "tail -5 /root/X/out/tests.log"
python scripts\vps.py "systemctl list-timers 'x-*' --no-pager"
```

`vps.py --file` writing a heredoc that itself contains a heredoc is fragile — prefer uploading a `.sh` and running
it, which is what `--file` is for.

## Redeploying after a code change

```powershell
cd C:\Users\hp\Downloads\X
git add -A; git commit -m "..."; git push origin main

python scripts\vps.py "cd /root/X && git pull --ff-only && systemctl restart x-daily.timer x-watchdog.timer"
python scripts\vps.py "cd /root/X && /root/X/.venv/bin/python tests/run_tests.py | tail -3"
```

`git pull` never touches the media, the `.env`, `secrets/` or `out/` — all ignored — so a pull is safe.

**One trap:** if a file was uploaded with `--put` *before* it was committed, `git pull` refuses with
`Aborting` / *"untracked working tree files would be overwritten by merge"*. That happened to `scripts/x_daily.sh`
and `scripts/x_watchdog.sh` on 2026-11-03. Remove the uploaded copies first, then pull, then fix the modes:

```bash
cd /root/X
rm -f scripts/x_daily.sh scripts/x_watchdog.sh
git pull --ff-only
sed -i 's/\r$//' scripts/x_daily.sh scripts/x_watchdog.sh
chmod +x scripts/x_daily.sh scripts/x_watchdog.sh
```

## Seeing the truth, not the intention

| Question | Command |
| :--- | :--- |
| is the timer alive? | `systemctl list-timers 'x-*' --no-pager` |
| did the last pass run, and what did it do? | `tail -25 /root/X/out/scheduler.log` |
| did it fail, and why? | `journalctl -u x-daily -n 40 --no-pager --output=cat` |
| what does YouTube actually have? | `python scripts\youtube_upload.py --workflow <wf> --check` from the VPS |

A pass that produces nothing but prints `today complete` is a *success* — it means both slots are done and the
ledger refused a duplicate.
