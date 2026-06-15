# Floor-watch tests without a bound camera: synthetic "cloth" patch on a
# real frame, plus the unbound/no-op and state-machine paths.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

from config import CONFIG
from floor_watch import FloorWatch


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append(event)
        print(f"  {event} ({sev}): {desc}")

    def save_evidence(self, *a, **k):
        pass


# 1) unbound camera -> completely inert
log = FakeLogger()
fw = FloorWatch("makeup room", CONFIG, log)
assert not fw.enabled
fw.update(0, np.zeros((720, 1280, 3), np.uint8), 0)
print("1) unbound camera: inert  OK")

# 2) bind a test zone on a real frame; drop a synthetic cloth on the floor
clean = cv2.imread("tidy_ref_makeup_room.jpg")
ref_path = "floor_ref_test.jpg"
cv2.imwrite(ref_path, clean)
CONFIG["floor_watch"] = {"makeup room": {
    "zone": (0.1, 0.55, 0.75, 0.98),  # the visible floor in this room
    "ref": ref_path,
}}
log = FakeLogger()
fw = FloorWatch("makeup room", CONFIG, log)

dirty = clean.copy()
cv2.ellipse(dirty, (520, 600), (90, 45), 20, 0, 360, (235, 235, 235), -1)  # "towel"

found, box = fw.scan(dirty)
print(f"   scan(dirty): {found}")
assert found, "synthetic cloth not detected"
found_clean, _ = fw.scan(clean)
assert not found_clean, f"clean floor misread: {found_clean}"

# 3) state machine: persists -> alert; cleared -> recovery; person -> paused
t0 = 1000.0
for t in range(0, 120, 5):
    fw.update(t0 + t, dirty, person_count=0)
assert log.events.count("OBJECT ON FLOOR") == 1, log.events
for t in range(120, 140, 5):
    fw.update(t0 + t, clean, person_count=0)
assert "FLOOR CLEAR" in log.events, log.events

log2 = FakeLogger()
fw2 = FloorWatch("makeup room", CONFIG, log2)
for t in range(0, 200, 5):
    fw2.update(t0 + t, dirty, person_count=1)  # someone present the whole time
assert not log2.events, log2.events
os.remove(ref_path)
print("3) state machine OK: alert + clear + person-present pause")
print("all floor-watch tests pass")
