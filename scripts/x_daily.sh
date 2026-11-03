#!/bin/sh
# systemd entry point for the two channels (the pipeline runs on the VPS, not the laptop).
#
# The test gate is the whole point: on 2026-11-03 a fallback that had never been exercised let a burst
# of HTTP 503 exhaust the model ladder, and both channels went silent for a day. A failing suite now
# blocks publishing, and says so in the log the watchdog reads.
set -u
cd /root/X || exit 1
mkdir -p out
PY=/root/X/.venv/bin/python

"$PY" tests/run_tests.py >> out/tests.log 2>&1
if [ $? -ne 0 ]; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') TESTS FAILED - pass skipped, see out/tests.log ===" >> out/scheduler.log
    exit 1
fi

exec "$PY" scripts/x_scheduler.py --once --privacy public >> out/scheduler.log 2>&1
