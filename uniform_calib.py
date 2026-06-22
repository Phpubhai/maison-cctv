# Calibrate uniform-set HSV ranges against real frames from the NVR.
#
# Grabs one snapshot per camera (from CONFIG["cameras"]) and paints every
# pixel that falls inside each uniform set with a distinct color:
#   set 1 -> green, set 2 -> blue, set 3 -> magenta ...
# Saves uniform_calib_<cam>.jpg and prints the in-range % per set.
#
# Workflow when adding a NEW set (e.g. reception):
#   1. have someone wear it in front of a camera
#   2. add a rough HSV guess to CONFIG["uniform_sets"] in config.py
#   3. run this, look at the overlay, tighten/widen the ranges, repeat
# Usage: python uniform_calib.py [camera name]   (default: all cameras)
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
import numpy as np

from config import CONFIG

SET_COLORS = [(0, 255, 0), (255, 80, 0), (255, 0, 255), (0, 255, 255)]  # BGR

only = sys.argv[1] if len(sys.argv) > 1 else None
for cam in CONFIG["cameras"]:
    name, ch = cam["name"], cam["ch"]
    if only and name != only:
        continue
    cap = cv2.VideoCapture(CONFIG["nvr_url"].format(ch=ch), cv2.CAP_FFMPEG)
    frame, t_end = None, time.time() + 20
    while time.time() < t_end and frame is None:
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    if frame is None:
        print(f"{name}: offline / no frame")
        continue

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    overlay = frame.copy()
    stats = []
    for i, (set_name, ranges) in enumerate(CONFIG["uniform_sets"].items()):
        mask = None
        for lo, hi in ranges:
            m = cv2.inRange(hsv, np.array(lo), np.array(hi))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        overlay[mask > 0] = SET_COLORS[i % len(SET_COLORS)]
        stats.append(f"{set_name}: {100.0 * np.count_nonzero(mask) / mask.size:.1f}%")
    out = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)
    label = f"{name} | " + "  ".join(stats)
    cv2.putText(out, label, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(out, label, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

    fname = f"uniform_calib_{name.replace(' ', '_')}.jpg"
    cv2.imwrite(fname, out)
    print(f"{name}: saved {fname}  ({'; '.join(stats)})")
