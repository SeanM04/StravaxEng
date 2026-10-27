@echo off
rem Runs the full Strava sync (manage.py sync) and appends the output to sync.log.
rem Registered with Windows Task Scheduler; see docs/management-commands.md.
cd /d "%~dp0.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" manage.py sync >> sync.log 2>&1
