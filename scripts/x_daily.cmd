@echo off
rem Task Scheduler entry point for the two channels. One pass, then exit.
rem The redirect is the point: without it a failed run leaves nothing anywhere, and "absence produces
rem nothing" is the failure AGENTS.md section 8 calls out. Evidence lives in out\scheduler.log.
cd /d "%~dp0.."
python "scripts\x_scheduler.py" --once --privacy public >> "out\scheduler.log" 2>&1
exit /b %ERRORLEVEL%
