#!/usr/bin/env python3
"""Only the START alert saves an evidence image; re-alerts carry none."""
import os

import cv2
import numpy as np

from config import CONFIG
from floor_watch import FloorWatch


class FakeLogger:
    def __init__(self): self.events = []
    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append((event, kw.get("image_path")))
    def save_evidence(self, *a, **k):
        return "evidence.jpg"


clean = cv2.imread("tidy_ref_makeup_room.jpg")
ref = "evid_dedup_ref.jpg"; cv2.imwrite(ref, clean)
CONFIG["floor_watch"] = {"makeup room": {
    "zone": (0.1, 0.55, 0.75, 0.98), "ref": ref}}   # default drift (3h), not hit
log = FakeLogger()
fw = FloorWatch("makeup room", CONFIG, log)
dirty = clean.copy()
cv2.ellipse(dirty, (520, 600), (90, 45), 20, 0, 360, (235, 235, 235), -1)

t0 = 1000.0
# first alert ~60s; one re-alert after re_alert_secs (300) -> ~360s
for t in range(0, 400, 5):
    fw.update(t0 + t, dirty, person_count=0)
alerts = [img for ev, img in log.events if ev == "OBJECT ON FLOOR"]
assert len(alerts) == 2, log.events            # START + one re-alert
assert alerts[0] == "evidence.jpg", "START must carry an image"
assert alerts[1] is None, "re-alert must carry NO image"
os.remove(ref)
print("PASS: only the START alert saves an evidence image")
