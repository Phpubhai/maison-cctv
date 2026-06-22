# Unit test for TrackManager._presence_observe: it must report a staff person's
# room to the engine, keyed by name (or anon "<camera>:<tid>"), with the room's
# customer flag. Uses stubs so no camera/models are needed.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import TrackManager

cfg = {
    "rooms": {"Foot Spa": {"type": "service", "via": "camera", "camera": "foot spa"}},
    "re_alert_secs": 300.0,
}


class FakeEngine:
    def __init__(self):
        self.calls = []

    def observe(self, now, key, camera, room, door, has_cust, **kw):
        self.calls.append({"key": key, "camera": camera, "room": room,
                           "door": door, "has_cust": has_cust, **kw})


class _Voter:
    def __init__(self, name, role):
        self.name = name
        self.role = role


class _Person:
    def __init__(self, name, role):
        self.announced = True
        self.voter = _Voter(name, role)


tm = TrackManager("foot spa", cfg, logger=None, eyes=None, faces=None)
tm.engine = FakeEngine()
frame = (1000, 1000, 3)

# anonymous staff (no name) -> key "<camera>:<tid>", confidence 0.0
tm._presence_observe(_Person(None, "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
c = tm.engine.calls[-1]
assert c["room"] == "Foot Spa" and c["key"] == "foot spa:7", c
assert c["confidence"] == 0.0 and c["has_cust"] is False, c
print("1) anonymous staff reported with anon key  OK")

# named staff + a customer in the room -> name key, has_cust True, conf 1.0
tm._presence_observe(_Person("Phai", "staff"), 7, (10, 10, 50, 50), frame,
                     {"Foot Spa": True}, 100.0)
c = tm.engine.calls[-1]
assert c["key"] == "Phai" and c["confidence"] == 1.0 and c["has_cust"] is True, c
print("2) named staff + customer in room  OK")

# a customer track is never reported as presence
before = len(tm.engine.calls)
tm._presence_observe(_Person(None, "customer"), 9, (10, 10, 50, 50), frame, {}, 100.0)
assert len(tm.engine.calls) == before
print("3) customers are not reported  OK")
print("all presence_wire tests pass")
