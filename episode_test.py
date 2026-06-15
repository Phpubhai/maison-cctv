# Offline simulation: verify start/end timeline events for SLEEPING and
# PHONE USE episodes (no cameras, no models -- fake analyzer + fake logger).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from config import CONFIG
from tracker import TrackManager, _Tracked


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append(event)
        print(f"  t={int(T[0])}s -> {event} ({sev}): {desc}")

    def save_evidence(self, *a, **k):
        pass


class FakeAnalyzer:
    state, held = "active", 0.0

    def update(self, *a):
        return self.state, self.held, "still+head-down"


log = FakeLogger()
tm = TrackManager("CAM", CONFIG, log, eyes=None)
p = _Tracked(CONFIG, None, None, [0, 0, 100, 200], 0)
p.voter.role = "staff"
p.analyzer = FakeAnalyzer()
frame = np.zeros((10, 10, 3), np.uint8)
T = [0]

PTS = np.zeros((17, 2), np.float32)
PTS[9] = PTS[10] = (50, 50)
KCONF = np.ones(17, np.float32)


def step(now, state="active", held=0.0, holding=False):
    T[0] = now
    p.analyzer.state, p.analyzer.held = state, held
    tm._analyze_staff(p, now, frame, [0, 0, 100, 200], PTS, KCONF, False, False,
                      holding)


print("--- sleep episode: starts, re-alerts, then ends")
step(200, "sleeping", 180)            # SLEEPING (start info)
step(260, "sleeping", 240)            # suppressed (cooldown)
step(600, "sleeping", 580)            # re-alert (continuous, >300s)
step(660, "active", 0)                # SLEEPING END (total ~640s)

print("--- phone episode: dwell, alert, put away")
t = 1000.0
while t < 1050:                       # holding a phone 50s -> alert at 45s
    step(t, holding=True)
    t += 1
while t < 1075:                       # phone gone; grace 18s then episode ends
    step(t)
    t += 1

print("--- phone ownership: nearest wrist wins")
from tracker import TrackManager as TM
PHONE = [[95, 95, 125, 125]]          # center (110, 110)
staff_pose = {"pts": np.zeros((17, 2), np.float32), "kconf": np.ones(17, np.float32)}
cust_pose = {"pts": np.zeros((17, 2), np.float32), "kconf": np.ones(17, np.float32)}
staff_pose["pts"][9] = staff_pose["pts"][10] = (150, 110)   # 40 px away (working)
cust_pose["pts"][9] = cust_pose["pts"][10] = (112, 112)     # touching the phone
rows = [(1, [0, 0, 200, 400], None, staff_pose),
        (2, [60, 60, 260, 460], None, cust_pose)]
SHAPE = (720, 1280, 3)
holders = tm._phone_holders(PHONE, rows, SHAPE)
assert holders == {2}, holders        # the customer owns it, NOT the staff
print("  holder resolved to the customer  OK")

# exempt zone: a phone inside the service area counts for NOBODY, even when
# only the staff's wrist is anywhere near it (lying customer undetected)
tm.cfg = dict(CONFIG, service_zones={"CAM": [(0.0, 0.0, 0.5, 0.5)]})
rows_staff_only = [(1, [0, 0, 200, 400], None, cust_pose)]  # wrist ON the phone
holders = tm._phone_holders(PHONE, rows_staff_only, SHAPE)  # (110,110) in zone
assert holders == set(), holders
print("  exempt service zone blocks attribution  OK")

expected = ["SLEEPING", "SLEEPING", "SLEEPING END", "PHONE USE", "PHONE USE END"]
assert log.events == expected, f"\nexpected {expected}\ngot      {log.events}"
print("PASS: start/end events + nearest-wrist phone ownership")
