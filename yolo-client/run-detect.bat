@echo off
REM  Start detection + push for ONE camera. Double-click, or run from a terminal.
REM  Reads the shared API_KEY/SERVER_URL from ..\cctv-env.bat (matches the server).
REM  Edit CAMERA_ID and SOURCE below for this camera.

call "%~dp0..\cctv-env.bat"

REM  a name for this camera/branch -- shows on the POS timeline
set CAMERA_ID=front-door

REM  SOURCE: 0 = webcam (good for a first test, no RTSP contention)
REM          or an RTSP url for the live camera, e.g.
REM          set SOURCE=rtsp://192.168.1.70:554/user=USER&password=PASS&channel=3
set SOURCE=0

REM  optional: only push these classes (comma separated). blank = all.
set CLASSES=person

echo ============================================================
echo  Detect+push  camera=%CAMERA_ID%  source=%SOURCE%  -^> %SERVER_URL%
echo  Press Ctrl+C to stop.
echo ============================================================
py "%~dp0detect_and_push.py"
pause
