@echo off
rem Task Scheduler entry point for the watchdog. Silence here must never be mistaken for health,
rem so every pass is appended: an empty watchdog log means the watchdog itself stopped.
cd /d "%~dp0.."
python "scripts\x_watchdog.py" >> "out\watchdog.log" 2>&1
exit /b %ERRORLEVEL%
