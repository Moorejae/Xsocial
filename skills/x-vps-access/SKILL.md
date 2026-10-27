---
name: x-vps-access
description: 'Reach this project''s Contabo VPS (169.58.212.7, vmi3701960) from Windows: the key that works and where it lives, the password fallback, the vps.py runner that avoids every quoting layer, the two traps that cost time (the 0-byte ssh on PATH, and ssh-keygen turning -N "" into a literal passphrase), the credentials that do NOT work so they are not retried, and the recovery path through the Contabo panel. Use when you need to run anything on this server, when a connection is refused, or when a new agent needs to know how to get in.'
---

# Reaching this project's VPS

One host, two working credentials, and a short list of things that look like credentials and are not. Read this
instead of probing, because every probe against a live server leaves a trace and the answers are already known.

## When to use

- You need to run a command, copy a file, or restart a service on `169.58.212.7`.
- A connection is refused and you need to know whether it is the credential, the key, or the network.
- You are a new agent and need to know how to get in before you can do anything.

## The host

| | |
| :--- | :--- |
| Address | `169.58.212.7`, PTR `vmi3701960.contaboserver.net` |
| Contabo instance | `vmi3701960` |
| OS | Ubuntu 24.04.5 LTS |
| Shape | 6 vCPU, 11 960 MB RAM, 193 GB disk (1.9 GB used at bootstrap) |
| Login | `root`, port 22 |
| **Not** REZMO | REZMO production is `178.18.245.136`. Never touch it. See [AGENTS.md](../../AGENTS.md) section 1. |

## The supported way in

```powershell
cd C:\Users\hp\Downloads\X
python scripts\vps.py --check
python scripts\vps.py "systemctl --failed --no-pager"
python scripts\vps.py --file deploy.sh
python scripts\vps.py --put local.txt /root/local.txt
```

`vps.py` reads `.env`, connects, **writes your command to a file on the server and runs that file**, then prints stdout,
stderr and `__EXIT__=<code>`, and deletes the file. The indirection is the point: it removes every quoting layer between
PowerShell, SSH and bash, which is where `$`, backticks, quotes and `&&` get silently mangled.

It tries the **key first, then the password**, and prints which one it used on stderr. That ordering matters: a broken
key should degrade to the password rather than lock the project out.

## The two credentials

| | |
| :--- | :--- |
| **Key** (primary) | `CONTABO_SSH_KEY_PATH` -> `C:\Users\hp\.ssh\x-vps-ed25519`, ED25519, **unencrypted**, comment `x-vps-20261027` |
| **Password** (fallback) | `CONTABO_LOGIN_PASSWORD` in `.env` |
| Server side | the key's public half is the only line in `/root/.ssh/authorized_keys` (`SHA256:53648u+PI8sgDOcXTQTdIy6N+4h4RmXkgnM0lPSSMJQ`) |

Confirmed working 2026-10-27 both through `vps.py` (`connected ... over key x-vps-ed25519`) and with a key-only client
that passed no password and set `look_for_keys=False`.

## Dead ends — do not retry these

Each was tried once and failed. Retrying them costs time and adds noise to the server's auth log.

| Attempted | Result |
| :--- | :--- |
| REZMO's key `keys\rezmo-contabo-ed25519.key` | denied. **Do not copy it here** — the two projects share no credential on purpose. |
| `keys\rezmo-vm-ed25519.key`, `keys\rezmo-vm-rsa.key` | denied / invalid key file |
| the root password in ForChi's `.env` | denied (different host) |
| Contabo REST API with the `CONTABO_CLIENT_ID` / `_SECRET` from ForChi's `.env` | `401 unauthorized_client` — invalid. Mint a fresh API client in the panel if you need API access. |

## Trap 1 — `ssh` on PATH does nothing

`C:\WINDOWS\system32\ssh` is a **0-byte file that exits 0 and prints nothing**. A scripted
`ssh host "systemctl restart x"` therefore reports success while doing nothing.

```powershell
& 'C:\Windows\System32\OpenSSH\ssh.exe' -i $key root@169.58.212.7 "hostname"
```

The tell is an exit code of 0 beside an empty log. Also do not pipe an `ssh` call in PowerShell (`... 2>&1 | Select-Object`
fails with *"Cannot run a document in the middle of a pipeline"*); redirect with `*>` and read the file.

## Trap 2 — `ssh-keygen -N '""'` makes an encrypted key

In PowerShell, `-N '""'` passes the two literal quote characters as the passphrase. The key looks fine, then fails
unattended:

```text
paramiko.ssh_exception.PasswordRequiredException: Private key file is encrypted
```

Generate through `cmd.exe`, where an empty argument is passed as empty:

```powershell
& $env:ComSpec /c "`"C:\Windows\System32\OpenSSH\ssh-keygen.exe`" -t ed25519 -N `"`" -C x-vps-20261027 -f `"$keyPath`" -q"
```

Then **prove** it is unencrypted — an unencrypted key prints its public half instead of prompting:

```powershell
& $env:ComSpec /c "`"C:\Windows\System32\OpenSSH\ssh-keygen.exe`" -y -f `"$keyPath`" -P `"`""
```

## Verifying a command actually ran

**Exit code 0 proves nothing here.** Make the remote command print its own evidence:

```bash
hostname; id -un; date -Is
```

If those lines do not come back, nothing ran, whatever the status said. This is the same rule the deployment runbooks
use: a marker written before the thing it describes exists is a guess.

## Recovery, in order

1. `python scripts\vps.py --check` and read the **last** line — it names the methods tried and the last error.
2. Wrong or rotated password: `my.contabo.com` -> instance `vmi3701960` -> initial password, or **reset password**; put
   it in `CONTABO_LOGIN_PASSWORD`.
3. Key removed server-side: `python scripts\vps.py --install-key "$env:USERPROFILE\.ssh\x-vps-ed25519.pub"` — needs the
   password to work, which is why the password should not be deleted until key access is confirmed.
4. SSH itself refused: the box is not reachable. Contabo panel -> restart, or **rescue mode**.

## Building on this server

Nothing is installed here yet: no application, no reverse proxy, no TLS. When you add something that runs unattended,
read `build-fallbacks` in the shared skill library first — supervision, retry and an alert that reaches a human are part
of shipping, not a later hardening pass.
