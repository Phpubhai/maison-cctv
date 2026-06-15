# Offline simulation of the greeting rule (no cameras, no models).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from config import CONFIG
from tracker import TrackManager

CONFIG["greeting_secs"] = 30.0
CONFIG["greeting_cooldown"] = 180.0


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append(event)
        print(f"  t? {label} {event} ({sev}): {desc}")

    def save_evidence(self, *a, **k):
        pass


frame = np.zeros((100, 100, 3), np.uint8)
CUST = [{"track_id": 1, "box": [10, 10, 40, 90], "conf": 0.9}]
BOTH = CUST + [{"track_id": 2, "box": [60, 10, 90, 90], "conf": 0.9}]


def make_tm():
    log = FakeLogger()
    tm = TrackManager("front door", CONFIG, log, eyes=None)
    return tm, log


def classify(tm, tid, role, posture="unknown"):
    tm.people[tid].voter.role = role
    tm.people[tid].posture = posture


# A) customer enters, staff stands at +10s -> no penalty
tm, log = make_tm()
tm.update(0, frame, BOTH, [], [])
classify(tm, 1, "customer")
classify(tm, 2, "staff", "sitting")
tm.update(2, frame, BOTH, [], [])          # ENTER x2, watch starts
tm.people[2].posture = "standing"
tm.update(10, frame, BOTH, [], [])         # staff stands -> satisfied
tm.update(40, frame, BOTH, [], [])         # deadline passes quietly
assert "GREETING MISSED" not in log.events, log.events
print("A) staff stood within 30s -> no penalty  OK\n")

# B) customer enters, staff keeps sitting -> penalty at +30s
tm, log = make_tm()
tm.update(0, frame, BOTH, [], [])
classify(tm, 1, "customer")
classify(tm, 2, "staff", "sitting")
tm.update(2, frame, BOTH, [], [])
tm.update(20, frame, BOTH, [], [])
assert "GREETING MISSED" not in log.events
tm.update(33, frame, BOTH, [], [])
assert log.events.count("GREETING MISSED") == 1, log.events
# cooldown: another customer right away does not re-trigger
tm.update(40, frame, BOTH + [{"track_id": 3, "box": [45, 10, 55, 90], "conf": 0.9}], [], [])
classify(tm, 3, "customer")
tm.update(42, frame, BOTH + [{"track_id": 3, "box": [45, 10, 55, 90], "conf": 0.9}], [], [])
tm.update(80, frame, BOTH, [], [])
assert log.events.count("GREETING MISSED") == 1, log.events
print("B) nobody stood -> exactly one penalty, cooldown blocks repeats  OK\n")

# C) no staff in frame at all -> penalty too
tm, log = make_tm()
tm.update(0, frame, CUST, [], [])
classify(tm, 1, "customer")
tm.update(2, frame, CUST, [], [])
tm.update(35, frame, CUST, [], [])
assert log.events.count("GREETING MISSED") == 1, log.events
print("C) no staff present -> penalty  OK\n")

# D) "new customer" appearing INSIDE the service zone (re-tracked customer
# being serviced at the pedicure chairs) never starts a greeting check
IN_ZONE = [{"track_id": 9, "box": [60, 10, 90, 50], "conf": 0.9}]  # center (75,30)
tm, log = make_tm()
tm.update(0, frame, IN_ZONE, [], [])
classify(tm, 9, "customer")
tm.update(2, frame, IN_ZONE, [], [])
tm.update(40, frame, IN_ZONE, [], [])
assert "GREETING MISSED" not in log.events, log.events
print("D) customer in the service zone -> no greeting check  OK\n")

print("all greeting-rule tests pass")
