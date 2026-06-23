# Unit test: _presence_observe must route identity through the resolver when one
# is set (forwarding face_id = p.voter.name), and fall back to Plan 1 behavior
# when there is no resolver. Stubs only -- no camera/models.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import TrackManager

cfg = {"rooms": {"Foot Spa": {"type": "service", "via": "camera",
                              "camera": "foot spa"}},
       "re_alert_secs": 300.0}


class FakeEngine:
    def __init__(self):
        self.calls = []

    def observe(self, now, key, camera, room, door, has_cust, **kw):
        self.calls.append({"key": key, "room": room, "has_cust": has_cust, **kw})


class FakeResolver:
    def __init__(self):
        self.seen = []

    def resolve(self, now, track_uid, face_id=None):
        self.seen.append((track_uid, face_id))
        return {"key": "Phai", "name": "Phai", "therapist_id": "t1",
                "confidence": 0.9, "source": "face"}


class _V:
    def __init__(self, name, role):
        self.name = name
        self.role = role


class _P:
    def __init__(self, name, role):
        self.announced = True
        self.voter = _V(name, role)


tm = TrackManager("foot spa", cfg, logger=None, eyes=None, faces=None)
tm.engine = FakeEngine()
tm.resolver = FakeResolver()
frame = (1000, 1000, 3)

tm._presence_observe(_P("staff_04", "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
assert tm.resolver.seen == [("foot spa:7", "staff_04")], tm.resolver.seen
c = tm.engine.calls[-1]
assert c["key"] == "Phai" and c["therapist"] == "Phai", c
assert c["therapist_id"] == "t1" and c["confidence"] == 0.9, c
print("1) _presence_observe routes through the resolver  OK")

tm.resolver = None
tm._presence_observe(_P("staff_04", "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
c = tm.engine.calls[-1]
assert c["key"] == "staff_04" and c["confidence"] == 1.0, c
print("2) no resolver -> Plan 1 fallback (key = face id)  OK")
print("all identity_wire tests pass")
