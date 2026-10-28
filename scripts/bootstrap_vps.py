#!/usr/bin/env python3
"""Install what the video pipeline needs on this project's VPS, and prove it works.

Run against `169.58.212.7` only (from `.env`). Verified 2026-10-27: that box had nginx and no
**ffmpeg at all**, and only the `dejavu` font family — so a render there would have failed at the
first cut, or produced captions in the wrong face with no warning.

    python scripts\\bootstrap_vps.py            # install + verify
    python scripts\\bootstrap_vps.py --check    # verify only

What it installs, and why each is needed:

| Package | Why |
| :--- | :--- |
| `ffmpeg` | cuts, concat, the `subtitles` filter that burns captions, `drawtext` for the watermark |
| `fonts-liberation` | a fallback Latin face so libass never renders tofu before our font is copied in |
| `python3`, `python3-pip` | the pipeline itself |

The project fonts are copied to `/opt/x/fonts` rather than installed system-wide: the caption face is
part of the brand, and a system-wide Jost would silently change every other tool that asks for it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import vps  # noqa: E402

REMOTE_ROOT = "/opt/x"

INSTALL = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
need=""
command -v ffmpeg  >/dev/null 2>&1 || need="$need ffmpeg"
command -v ffprobe >/dev/null 2>&1 || need="$need ffmpeg"
command -v python3 >/dev/null 2>&1 || need="$need python3"
if [ -n "$need" ]; then
  apt-get update -qq
  apt-get install -y -qq $need fonts-liberation
fi
install -d -m 0755 {REMOTE_ROOT} {REMOTE_ROOT}/fonts {REMOTE_ROOT}/out
echo "installed: ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
"""

VERIFY = f"""
set -uo pipefail
hostname; id -un; date -Is
echo "ffmpeg:  $(command -v ffmpeg  || echo MISSING)"
echo "ffprobe: $(command -v ffprobe || echo MISSING)"
echo "filters: $(ffmpeg -hide_banner -filters 2>/dev/null | grep -cE '\\b(subtitles|drawtext|zoompan|amix|alimiter)\\b')/5 present"
ffmpeg -hide_banner -filters 2>/dev/null | grep -E '\\b(subtitles|drawtext|amix|alimiter)\\b' | sed 's/^/  /'
echo "fonts in {REMOTE_ROOT}/fonts: $(ls -1 {REMOTE_ROOT}/fonts 2>/dev/null | wc -l)"
python3 -c 'import sys; print("python", sys.version.split()[0])'
df -h / | tail -1
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare the X VPS for video rendering")
    ap.add_argument("--check", action="store_true", help="verify only, install nothing")
    a = ap.parse_args()

    client = vps.connect()
    try:
        if not a.check:
            rc, out, err = vps.run_script(client, INSTALL, timeout=1800)
            print(out.strip())
            if err.strip():
                print(err.strip(), file=sys.stderr)
            if rc != 0:
                print(f"install failed (rc={rc})", file=sys.stderr)
                return rc

        # Copy the brand fonts up, so the caption face is the one we measured, not whatever the
        # distro happens to call "Jost".
        sftp = client.open_sftp()
        try:
            for local in sorted((Path(__file__).resolve().parent.parent / "fonts").glob("*.ttf")):
                sftp.put(str(local), f"{REMOTE_ROOT}/fonts/{local.name}")
                print(f"  -> {REMOTE_ROOT}/fonts/{local.name}")
        finally:
            sftp.close()

        rc, out, err = vps.run_script(client, VERIFY)
        print(out.strip())
        if err.strip():
            print(err.strip(), file=sys.stderr)
        return rc
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
