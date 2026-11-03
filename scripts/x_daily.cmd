@echo off
rem Task Scheduler entry point for the two channels. One pass, then exit.
rem The redirect is the point: without it a failed run leaves nothing anywhere, and "absence produces
rem nothing" is the failure AGENTS.md section 8 calls out. Evidence lives in out\scheduler.log.
cd /d "%~dp0.."

rem Gate the pass on the offline test suite. 2026-11-03: a fallback that was never exercised let a
rem burst of HTTP 503 exhaust the model ladder, and both channels went silent for a day. A broken
rem fallback is now a reason NOT to publish, and the reason lands in the log the watchdog reads.
python "tests\run_tests.py" >> "out\tests.log" 2>&1
if errorlevel 1 (
  echo === %DATE% %TIME% TESTS FAILED - pass skipped, see out\tests.log ===>> "out\scheduler.log"
  exit /b 1
)

python "scripts\x_scheduler.py" --once --privacy public >> "out\scheduler.log" 2>&1
exit /b %ERRORLEVEL%
