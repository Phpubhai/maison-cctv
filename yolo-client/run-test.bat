@echo off
REM  Send ONE fake event to the server, then read it back -- proves the whole
REM  pipe (key + POST + store + GET) without needing a camera. Expect 201.

call "%~dp0..\cctv-env.bat"
set CAMERA_ID=test-cam
py "%~dp0send_test_event.py"
pause
