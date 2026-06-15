# Probe NVR channels: grab one snapshot per channel so we can map channel
# numbers to camera names before switching the topology.
# Usage: python nvr_probe.py [max_channel]
import os
import sys

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2

from config import CONFIG  # NVR URL comes from local_settings.py (gitignored)

NVR = CONFIG["nvr_url"]
max_ch = int(sys.argv[1]) if len(sys.argv) > 1 else 4

for ch in range(1, max_ch + 1):
    url = NVR.format(ch=ch)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    frame = None
    for _ in range(10):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    if frame is None:
        print(f"channel {ch}: no stream")
        continue
    out = f"nvr_ch{ch}.jpg"
    cv2.imwrite(out, frame)
    print(f"channel {ch}: {frame.shape[1]}x{frame.shape[0]} -> {out}")
