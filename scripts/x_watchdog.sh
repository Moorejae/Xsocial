#!/bin/sh
# systemd entry point for the watchdog (the pipeline runs on the VPS, not the laptop).
# Everything is appended, because a watchdog whose own output vanished would be exactly the failure it
# exists to catch.
set -u
cd /root/X || exit 1
mkdir -p out
exec /root/X/.venv/bin/python scripts/x_watchdog.py >> out/watchdog.log 2>&1
