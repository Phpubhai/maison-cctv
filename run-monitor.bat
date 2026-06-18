@echo off
REM  Start the main behavior monitor (main.py) WITH realtime push to the
REM  event server. Calls cctv-env.bat so it uses the SAME API key + SERVER_URL
REM  as run-server.bat and the POS viewer -- so events flow straight to the
REM  timeline page (เวลา / ใคร / ทำอะไร / นานแค่ไหน).
REM
REM  Run run-server.bat FIRST (the monitor pushes to it). If you launch main.py
REM  any other way (without these env vars), push is simply disabled -- the
REM  monitor still records everything locally as before.

call "%~dp0cctv-env.bat"
cd /d "%~dp0"
echo ============================================================
echo  Behavior monitor  ->  push events to %SERVER_URL%
echo  (make sure run-server.bat is already running)
echo ============================================================
py main.py
pause
