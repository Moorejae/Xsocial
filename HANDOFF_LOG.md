# X — handoff log

Newest first. Every change gets an entry, with evidence. See [AGENTS.md](AGENTS.md) §6 for the shape.

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
