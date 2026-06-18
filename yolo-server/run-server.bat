@echo off
REM  Start the event server. Double-click this file, or run it from a terminal.
REM  Reads the shared API_KEY from ..\cctv-env.bat so it always matches the camera.

call "%~dp0..\cctv-env.bat"
set PORT=8080
set DB_PATH=%~dp0events.db

echo ============================================================
echo  Event server  port=%PORT%  db=%DB_PATH%
echo  Open the timeline at  http://localhost:%PORT%/
echo  (other LAN machines: http://THIS-PC-IP:%PORT%/)
echo  Press Ctrl+C to stop.
echo ============================================================
py "%~dp0server.py"
pause
