# Validate the tidy-watch thresholds with real frames + the state machine.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2

from config import CONFIG
from room_tidy import TidyMonitor


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append(event)
        print(f"  {event} ({sev}): {desc}")

    def save_evidence(self, *a, **k):
        pass


log = FakeLogger()
tm = TidyMonitor("makeup room", CONFIG, log)
same = cv2.imread("tidy_ref_makeup_room.jpg")  # the reference itself
moved = cv2.imread("nvr_ch1.jpg")     # 09:58 -- chairs arranged differently
f1, _ = tm.diff(same)
f2, _ = tm.diff(moved)
thr = CONFIG["tidy_diff_frac"]
print(f"same arrangement diff: {f1:.3f}  (threshold {thr})")
print(f"moved chairs diff:     {f2:.3f}")
assert f1 < thr, "tidy frame misread as messy"
assert f2 >= thr, "messy frame misread as tidy"

# state machine: empty + messy long enough -> one alert; recovery logged
t0 = 1000.0
for t in range(0, 600, 10):
    tm.update(t0 + t, moved, person_count=0)
assert log.events.count("ROOM MESSY") == 1, log.events
for t in range(600, 700, 10):
    tm.update(t0 + t, same, person_count=0)
assert "ROOM TIDY" in log.events, log.events

# person present -> never judged
log2 = FakeLogger()
tm2 = TidyMonitor("makeup room", CONFIG, log2)
for t in range(0, 700, 10):
    tm2.update(t0 + t, moved, person_count=1)
assert not log2.events, log2.events
print("state machine OK: alert + recovery + occupied-room exemption")
