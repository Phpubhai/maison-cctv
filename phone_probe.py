# Diagnose in-hand phone detection: for ~45s, compare phone scores on
# (a) the full 1280 frame vs (b) a 2x-upscaled crop of each detected person.
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
from ultralytics import YOLO

from config import CONFIG

cfg = r"C:\Program Files\Agent\Media\XML\objects.json"
with open(cfg, encoding="utf-8-sig") as f:
    cams = json.load(f)["cameras"]
creds = next(c["settings"] for c in cams if c["settings"].get("login"))
pw = urllib.parse.quote(creds["password"], safe="")
cap = cv2.VideoCapture(f"rtsp://{creds['login']}:{pw}@192.168.1.87:554/stream2", cv2.CAP_FFMPEG)

m = YOLO(CONFIG["det_model"])
full_hits, crop_hits, checks, last = [], [], 0, 0.0
t_end = time.time() + 45
while time.time() < t_end:
    ok, frame = cap.read()
    if not ok or time.time() - last < 1.0:
        continue
    last = time.time()
    checks += 1
    # (a) full frame, very low threshold
    r = m(frame, conf=0.10, classes=[67], imgsz=CONFIG["imgsz"], verbose=False)[0]
    for b in r.boxes:
        full_hits.append(float(b.conf))
    # (b) 2x crop per person
    rp = m(frame, conf=0.4, classes=[0], imgsz=CONFIG["imgsz"], verbose=False)[0]
    for pb in rp.boxes:
        x1, y1, x2, y2 = (max(0, int(v)) for v in pb.xyxy[0])
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        big = cv2.resize(crop, None, fx=2, fy=2)
        rc = m(big, conf=0.10, classes=[67], imgsz=640, verbose=False)[0]
        for b in rc.boxes:
            crop_hits.append(float(b.conf))
cap.release()

def report(name, hits):
    if hits:
        s = sorted(hits)
        print(f"{name}: {len(hits)} hits / {checks} frames, "
              f"conf min {s[0]:.2f} med {s[len(s)//2]:.2f} max {s[-1]:.2f}")
    else:
        print(f"{name}: 0 hits / {checks} frames")

report("full frame @0.10 ", full_hits)
report("2x person crop @0.10", crop_hits)
