# Rough throughput benchmark: time one full analysis pass (detect+track,
# phone zoom pass, pose) on a saved snapshot of each camera, 10 reps.
# Run while the production monitor is up to see real shared-GPU numbers.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2

from config import CONFIG
from detector import PersonDetector
from sleep_analyzer import PoseEstimator

SNAPS = [("front door 720p", "nvr_ch4.jpg"), ("reception 1080p", "nvr_ch2.jpg"),
         ("makeup 720p", "nvr_ch1.jpg"), ("foot spa 1296p", "nvr_ch3.jpg"),
         ("street 720p", "nvr_ch5.jpg")]

det = PersonDetector(CONFIG)
pose = PoseEstimator(CONFIG)

total = 0.0
for name, path in SNAPS:
    frame = cv2.imread(path)
    if frame is None:
        print(f"{name}: snapshot missing")
        continue
    det.detect(frame)          # warm-up (model load, cudnn autotune)
    pose.estimate(frame)
    t0 = time.perf_counter()
    reps = 10
    for _ in range(reps):
        persons, phones = det.detect(frame)
        if persons:
            pose.estimate(frame)
    dt = (time.perf_counter() - t0) / reps
    total += dt
    print(f"{name:18s} {dt*1000:6.1f} ms/pass  ({len(persons)} people)  -> max {1/dt:5.1f} pass/s")

print(f"\none round of ALL 5 cameras: {total*1000:.0f} ms -> max {1/total:.1f} full rounds/s")
print(f"configured pace: {CONFIG['sample_fps']} passes/s total "
      f"({CONFIG['sample_fps']/len(SNAPS):.2f} per camera)")
