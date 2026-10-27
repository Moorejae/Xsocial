"""Run anything on this project's VPS, safely and without printing a credential.

Why this exists rather than a bare `ssh` call: the command is uploaded as a file and executed with
bash, so there is no quoting layer between PowerShell, SSH and the remote shell. That is where `$`,
backticks, quotes and `&&` get silently mangled, and a mangled command that half-succeeds is worse
than one that fails.

Reads connection details from the project's .env file. Never prints the password or the key contents.

Usage:
  python scripts\\vps.py --check
  python scripts\\vps.py "hostname; uptime"
  python scripts\\vps.py --file deploy.sh
  python scripts\\vps.py --put local.txt /root/local.txt
  python scripts\\vps.py --install-key C:\\Users\\me\\.ssh\\id_ed25519.pub
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def read_env(path, name):
    """Read one setting from an env file. The value is returned, never logged."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith(name + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


CFG = {
    "ip": read_env(ENV_FILE, "CONTABO_IP_ADDRESS"),
    "port": int(read_env(ENV_FILE, "CONTABO_SSH_PORT") or 22),
    "user": read_env(ENV_FILE, "CONTABO_LOGIN_USERNAME") or "root",
    "password": read_env(ENV_FILE, "CONTABO_LOGIN_PASSWORD"),
    "key": read_env(ENV_FILE, "CONTABO_SSH_KEY_PATH"),
    "remote_dir": read_env(ENV_FILE, "REMOTE_SCRIPT_DIR") or "/tmp",
}


def die(msg, code=1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def connect():
    if not CFG["ip"]:
        die("CONTABO_IP_ADDRESS is empty in .env")

    attempts = []
    key = CFG["key"]
    if key:
        p = Path(os.path.expandvars(os.path.expanduser(key)))
        if p.exists():
            attempts.append((f"key {p.name}", {"key_filename": str(p)}))
        else:
            print(f"# note: CONTABO_SSH_KEY_PATH is set but {p} is missing, falling back to the password",
                  file=sys.stderr)
    if CFG["password"]:
        attempts.append(("password from .env", {"password": CFG["password"]}))

    if not attempts:
        die(
            "No way in: CONTABO_SSH_KEY_PATH is not set and CONTABO_LOGIN_PASSWORD is empty in .env.\n"
            "  Fill CONTABO_LOGIN_PASSWORD from the Contabo panel (my.contabo.com -> instance "
            f"{read_env(ENV_FILE, 'CONTABO_INSTANCE_NAME') or CFG['ip']} -> initial password or reset password),\n"
            "  then run: python scripts\\vps.py --check\n"
            "  See AGENTS.md section 5."
        )

    # Try each method in turn, so a broken or rotated key does not lock the project out while a
    # working password still exists. The order is key first: it is the one that should end up working.
    last_error = None
    for label, extra in attempts:
        client = paramiko.SSHClient()
        # First contact is accepted and pinned in the known_hosts file; after that, a changed host
        # key is an error rather than something to click through.
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(port=CFG["port"], username=CFG["user"], timeout=25,
                      allow_agent=False, look_for_keys=False)
        kwargs.update(extra)
        try:
            client.connect(CFG["ip"], **kwargs)
        except paramiko.AuthenticationException:
            print(f"# {label} refused, trying the next method", file=sys.stderr)
            last_error = f"{label}: authentication refused"
            client.close()
            continue
        except paramiko.PasswordRequiredException:
            print(f"# {label} is passphrase-protected and cannot be used unattended, skipping",
                  file=sys.stderr)
            last_error = f"{label}: passphrase-protected"
            client.close()
            continue
        except Exception as e:
            die(f"Could not reach {CFG['ip']}:{CFG['port']} ({type(e).__name__}: {e})")

        client.get_transport().set_keepalive(15)
        print(f"# connected to {CFG['ip']} as {CFG['user']} over {label}", file=sys.stderr)
        return client

    die(
        f"Every method failed for {CFG['user']}@{CFG['ip']}:\n  " + "\n  ".join(
            [a[0] for a in attempts]) +
        f"\n  last error: {last_error}\n"
        "  Do not retry in a loop. Check the Contabo panel: the password may have been reset, "
        "or the key removed from authorized_keys."
    )


def run_script(client, body, timeout=900):
    """Upload the script to a unique path, run it, return (rc, stdout, stderr)."""
    name = f"{CFG['remote_dir'].rstrip('/')}/x_cmd_{uuid.uuid4().hex[:12]}.sh"
    body = body.replace("\r\n", "\n")
    if not body.endswith("\n"):
        body += "\n"

    sftp = client.open_sftp()
    try:
        with sftp.file(name, "w") as f:
            f.write(body)
        sftp.chmod(name, 0o700)
    finally:
        sftp.close()

    try:
        _, out, err = client.exec_command(f"bash {name}", timeout=timeout)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
        rc = out.channel.recv_exit_status()
    finally:
        try:
            s = client.open_sftp()
            s.remove(name)
            s.close()
            sftp2 = client.open_sftp()
            sftp2.remove(name + ".tmp")
            sftp2.close()
        except Exception:
            pass
    return rc, stdout, stderr


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("command", nargs="?", help="a command or script body to run")
    ap.add_argument("--check", action="store_true", help="prove access and print basic facts")
    ap.add_argument("--file", help="upload this local script and run it")
    ap.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"), help="copy a file up")
    ap.add_argument("--install-key", metavar="PUBKEY", help="append a public key to root authorized_keys")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    if a.check:
        body = r"""
hostname
id -un
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
echo "cores: $(nproc)"
free -m | sed -n '2p'
df -h / | tail -1
echo "load: $(cut -d' ' -f1-3 /proc/loadavg)"
echo "date: $(date -Is)"
"""
        client = connect()
        try:
            rc, out, err = run_script(client, body)
        finally:
            client.close()
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write(err)
        return rc

    if a.install_key:
        pub_path = Path(os.path.expandvars(os.path.expanduser(a.install_key)))
        if not pub_path.exists():
            die(f"no public key at {pub_path}")
        pub = pub_path.read_text(encoding="utf-8").strip()
        if not pub.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-")):
            die("that does not look like an OpenSSH public key")
        body = f"""
set -eu
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if grep -qxF '{pub}' /root/.ssh/authorized_keys; then
  echo "already present"
else
  printf '%s\\n' '{pub}' >> /root/.ssh/authorized_keys
  echo "installed"
fi
echo "authorized_keys lines: $(wc -l < /root/.ssh/authorized_keys)"
"""
        client = connect()
        try:
            rc, out, err = run_script(client, body)
        finally:
            client.close()
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write(err)
        return rc

    if a.put:
        local, remote = a.put
        lp = Path(os.path.expandvars(os.path.expanduser(local)))
        if not lp.exists():
            die(f"no file at {lp}")
        client = connect()
        try:
            sftp = client.open_sftp()
            sftp.put(str(lp), remote)
            sftp.close()
            print(f"copied {lp.name} -> {remote}")
        finally:
            client.close()
        return 0

    if a.file:
        fp = Path(os.path.expandvars(os.path.expanduser(a.file)))
        if not fp.exists():
            die(f"no script at {fp}")
        body = fp.read_text(encoding="utf-8")
    elif a.command:
        body = a.command
    else:
        ap.print_help()
        return 2

    client = connect()
    try:
        rc, out, err = run_script(client, body, timeout=a.timeout)
    finally:
        client.close()
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err)
    print(f"__EXIT__={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
