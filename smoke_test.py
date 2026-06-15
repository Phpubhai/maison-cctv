# Offline smoke test: drive the full v3 pipeline (detect+track -> pose ->
# role vote -> sleep/imbalance -> timeline -> overlay) with a saved frame
# instead of a live camera. Expectations on the known front-door frame:
# the seated man in the beige uniform becomes STAFF, the woman in white
# becomes customer, ENTER events reach events_smoke.jsonl.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2

from config import CONFIG
from detector import PersonDetector
from overlay import compose, draw_people, draw_phones
from sleep_analyzer import EyeScorer, PoseEstimator
from timeline_logger import TimelineLogger
from tracker import TrackManager

CONFIG["events_path"] = "events_smoke.jsonl"
CONFIG["min_visible"] = 0.5
CONFIG["role_min_samples"] = 3

frame_path = sys.argv[1] if len(sys.argv) > 1 else "../raw_192_168_1_87.jpg"
base = cv2.imread(frame_path)
assert base is not None, f"cannot read {frame_path}"

logger = TimelineLogger(CONFIG)
det = PersonDetector(CONFIG)
pose = PoseEstimator(CONFIG)
eyes = EyeScorer(CONFIG)
tm = TrackManager("CAM_01", CONFIG, logger, eyes)

t0 = time.time()
people, phones = [], []
for step in range(10):
    now = t0 + step * 0.5
    frame = base.copy()
    detections, phones = det.detect(frame)
    poses = pose.estimate(frame) if detections else []
    people = tm.update(now, frame, detections, poses, phones)
    print(f"t+{step * 0.5:.1f}s -> " + ", ".join(p["tag"] for p in people))

draw_people(frame, people)
draw_phones(frame, phones)
out = compose(frame, "CAM_01", logger.tail("CAM_01"), CONFIG["timeline_events"])
cv2.imwrite("smoke_overlay.jpg", out)
print("overlay saved -> smoke_overlay.jpg")
print("events written:", sum(1 for _ in open(CONFIG["events_path"], encoding="utf-8")))
