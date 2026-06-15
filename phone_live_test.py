# Live end-to-end test of the phone-use path on the front door camera.
# Runs the full v3 pipeline and prints the staff tag whenever it changes;
# expect "phone Ns" to climb and PHONE USE to fire at phone_secs.
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
CONFIG["events_path"] = "events_phone_test.jsonl"
CONFIG["timeline_dir"] = "timelines_phone_test"
CONFIG["ws_port"] = 8766  # the real monitor owns 8765

from detector import PersonDetector
from person_labeler import FaceMatcher
from sleep_analyzer import EyeScorer, PoseEstimator
from timeline_logger import TimelineLogger
from tracker import TrackManager

secs = int(sys.argv[1]) if len(sys.argv) > 1 else 100

cfg = r"C:\Program Files\Agent\Media\XML\objects.json"
with open(cfg, encoding="utf-8-sig") as f:
    cams = json.load(f)["cameras"]
creds = next(c["settings"] for c in cams if c["settings"].get("login"))
pw = urllib.parse.quote(creds["password"], safe="")
cap = cv2.VideoCapture(f"rtsp://{creds['login']}:{pw}@192.168.1.87:554/stream2", cv2.CAP_FFMPEG)

logger = TimelineLogger(CONFIG)
det = PersonDetector(CONFIG)
pose = PoseEstimator(CONFIG)
eyes = EyeScorer(CONFIG)
faces = FaceMatcher(CONFIG)
tm = TrackManager("front door", CONFIG, logger, eyes, faces)

last, last_print = 0.0, ""
t_end = time.time() + secs
while time.time() < t_end:
    ok, frame = cap.read()
    if not ok or time.time() - last < 1.0:
        continue
    last = now = time.time()
    persons, phones = det.detect(frame)
    poses = pose.estimate(frame) if persons else []
    for p in tm.update(now, frame, persons, poses, phones):
        if p["tag"] != last_print:
            last_print = p["tag"]
            print(f"[{time.strftime('%H:%M:%S')}] {p['tag']}", flush=True)
cap.release()
print("--- done")
