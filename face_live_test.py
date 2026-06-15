# Live test of the face-recognition path: watch one camera for a while,
# run pose + RoleVoter (uniform votes OFF? no -- full logic) per person, and
# report when the enrolled face is matched.
# Usage: python face_live_test.py <ip> [seconds]
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2

from config import CONFIG
from person_labeler import FaceMatcher
from sleep_analyzer import PoseEstimator

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.87"
secs = int(sys.argv[2]) if len(sys.argv) > 2 else 90

cfg = r"C:\Program Files\Agent\Media\XML\objects.json"
with open(cfg, encoding="utf-8-sig") as f:
    cams = json.load(f)["cameras"]
creds = next(c["settings"] for c in cams if c["settings"].get("login"))
pw = urllib.parse.quote(creds["password"], safe="")
host = ip if ":" in ip else f"{ip}:554"
cap = cv2.VideoCapture(f"rtsp://{creds['login']}:{pw}@{host}/stream2", cv2.CAP_FFMPEG)

pose = PoseEstimator(CONFIG)
faces = FaceMatcher(CONFIG)
matches, checks, last = 0, 0, 0.0
t_end = time.time() + secs
while time.time() < t_end:
    ok, frame = cap.read()
    if not ok or time.time() - last < 1.5:
        continue
    last = time.time()
    for p in pose.estimate(frame):
        checks += 1
        name = faces.match(frame, p["pts"], p["kconf"])
        if name:
            matches += 1
            print(f"[{time.strftime('%H:%M:%S')}] MATCH {name} "
                  f"at box {[int(v) for v in p['box'][:2]]}", flush=True)
cap.release()
print(f"done: {matches} matches out of {checks} person-sightings in {secs}s")
