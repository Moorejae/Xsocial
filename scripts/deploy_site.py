#!/usr/bin/env python3
"""Publish web/site/ to this project's VPS and prove it is being served.

The OAuth consent screen needs public URLs for the app homepage, the privacy policy and the terms
of service. Until a domain points here, nginx answers on the plain IP address, which is enough to
build and check the pages; Google's verification wants a real domain over HTTPS, so that step is
recorded as still-open in the handoff log rather than pretended to be done.

    python scripts\\build_site.py && python scripts\\deploy_site.py

Only ever runs against CONTABO_IP_ADDRESS from .env (169.58.212.7). It imports vps.py, so the same
key-then-password connection path and the same "write a file, run the file" quoting protection apply.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Remote output is UTF-8 (apt prints arrows and dashes). A Windows console defaults to cp1252 and
# would crash on printing it, which turns a successful deploy into a traceback.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import vps  # noqa: E402  (local module, path set above)

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "web" / "site"
REMOTE_ROOT = "/srv/x-site"

SETUP = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq nginx
fi

install -d -m 0755 {REMOTE_ROOT}
cat > /etc/nginx/sites-available/x-site <<'CONF'
server {{
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    root {REMOTE_ROOT};
    index index.html;
    charset utf-8;
    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy no-referrer;
    location / {{ try_files $uri $uri/ =404; }}
}}
CONF
ln -sf /etc/nginx/sites-available/x-site /etc/nginx/sites-enabled/x-site
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx >/dev/null 2>&1 || true
systemctl reload nginx
echo "nginx: $(nginx -v 2>&1)"
"""

VERIFY = f"""
set -uo pipefail
hostname; id -un; date -Is
echo "files: $(find {REMOTE_ROOT} -type f | wc -l)"
for u in / /jephter/ /jephter/privacy.html /jephter/terms.html /emmanuel/ /emmanuel/privacy.html /emmanuel/terms.html; do
  printf '%s %s\\n' "$(curl -s -o /dev/null -w '%{{http_code}}' "http://127.0.0.1$u")" "$u"
done
echo "youtube-disclosure hits in jephter/privacy.html: $(grep -c 'YouTube API Services' {REMOTE_ROOT}/jephter/privacy.html)"
"""


def upload_tree(client) -> int:
    sftp = client.open_sftp()

    def mkdirs(path: str) -> None:
        parts = path.strip("/").split("/")
        cur = ""
        for part in parts:
            cur += "/" + part
            try:
                sftp.stat(cur)
            except IOError:
                sftp.mkdir(cur)

    count = 0
    for local in sorted(SITE.rglob("*")):
        if local.is_dir():
            continue
        rel = local.relative_to(SITE).as_posix()
        remote = f"{REMOTE_ROOT}/{rel}"
        mkdirs(remote.rsplit("/", 1)[0])
        sftp.put(str(local), remote)
        sftp.chmod(remote, 0o644)
        count += 1
        print(f"  -> {remote}")
    sftp.close()
    return count


def main() -> int:
    if not SITE.exists():
        print("web/site is missing — run: python scripts\\build_site.py", file=sys.stderr)
        return 2

    client = vps.connect()
    try:
        rc, out, err = vps.run_script(client, SETUP)
        print(out.strip())
        if err.strip():
            print(err.strip(), file=sys.stderr)
        if rc != 0:
            print(f"setup failed (rc={rc})", file=sys.stderr)
            return rc

        uploaded = upload_tree(client)
        print(f"uploaded {uploaded} files to {REMOTE_ROOT}")

        rc, out, err = vps.run_script(client, VERIFY)
        print(out.strip())
        if err.strip():
            print(err.strip(), file=sys.stderr)
        return rc
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
