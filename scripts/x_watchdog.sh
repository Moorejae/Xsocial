#!/bin/sh
# systemd entry point for the watchdog (the pipeline runs on the VPS, not the laptop).
# Everything is appended, because a watchdog whose own output vanished would be exactly the failure it
# exists to catch.
set -u
cd /root/X || exit 1
mkdir -p out
PY=/root/X/.venv/bin/python

# The project's own tests first, as the publisher does. A watchdog that has drifted from the code it
# watches reports "healthy" with stale rules, which is worse than no watchdog at all. Only a failure
# is copied into the log, so a passing run does not flood it.
if ! "$PY" tests/run_tests.py > out/.watchdog-tests.tmp 2>&1; then
    printf '=== watchdog tests FAILED at %s ===\n' "$(date -Is)" >> out/watchdog.log
    tail -n 20 out/.watchdog-tests.tmp >> out/watchdog.log
fi
rm -f out/.watchdog-tests.tmp

exec "$PY" scripts/x_watchdog.py >> out/watchdog.log 2>&1
