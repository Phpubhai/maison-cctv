#!/usr/bin/env python3
"""Floor drift guard: an object stuck past drift_secs warns ONCE, then mutes,
then re-arms after a clear."""
import os

import cv2
import numpy as np

from config import CONFIG
from floor_watch import FloorWatch


class FakeLogger:
    def __init__(self): self.events = []
    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append((event, sev, kw.get("image_path")))
    def save_evidence(self, *a, **k):
        return "evidence.jpg"


clean = cv2.imread("tidy_ref_makeup_room.jpg")
ref = "floor_drift_ref.jpg"; cv2.imwrite(ref, clean)
CONFIG["floor_watch"] = {"makeup room": {
    "zone": (0.1, 0.55, 0.75, 0.98), "ref": ref, "drift_secs": 100}}
log = FakeLogger()
fw = FloorWatch("makeup room", CONFIG, log)
dirty = clean.copy()
cv2.ellipse(dirty, (520, 600), (90, 45), 20, 0, 360, (235, 235, 235), -1)  # "towel"

t0 = 1000.0
# object present continuously; first alert ~60s, drift at >=100s
for t in range(0, 200, 5):
    fw.update(t0 + t, dirty, person_count=0)
evs = [e for e, _, _ in log.events]
assert evs.count("OBJECT ON FLOOR") == 1, evs
assert evs.count("REFERENCE DRIFT") == 1, evs
assert any(e == "REFERENCE DRIFT" and s == "warning" for e, s, _ in log.events)

# still present -> stays muted (no new alerts of either kind)
for t in range(200, 500, 5):
    fw.update(t0 + t, dirty, person_count=0)
evs = [e for e, _, _ in log.events]
assert evs.count("OBJECT ON FLOOR") == 1, evs
assert evs.count("REFERENCE DRIFT") == 1, evs

# clear the zone -> recovery + re-arm
for t in range(500, 520, 5):
    fw.update(t0 + t, clean, person_count=0)
assert "FLOOR CLEAR" in [e for e, _, _ in log.events]
# a fresh object alerts again (drift_flagged was reset)
for t in range(520, 620, 5):
    fw.update(t0 + t, dirty, person_count=0)
assert [e for e, _, _ in log.events].count("OBJECT ON FLOOR") == 2, log.events
os.remove(ref)
print("PASS: floor drift guard warns once, mutes, then re-arms")
