# Unit test for PresenceEngine: sightings -> intervals + status, min-dwell,
# disappearance, and camera-less threshold inference. Uses a fake store and a
# manual clock so it is fully offline/deterministic.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from presence_engine import PresenceEngine, status_for

cfg = {
    "rooms": {
        "MAISON 2": {"type": "service", "via": "zone", "camera": "spa room"},
        "ห้องน้ำ": {"type": "facility", "via": "threshold", "camera": "back hall"},
    },
    "presence_min_dwell": 10.0,
    "track_grace": 15.0,
    "threshold_timeout": 100.0,
}


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.n = 0
        self.opens = []
        self.closes = []

    def open_interval(self, ts, camera, th, tid, room, status, conf, src):
        self.n += 1
        self.rows[self.n] = {"room": room, "status": status, "ended": None}
        self.opens.append((self.n, room, status))
        return self.n

    def close_interval(self, iid, ts):
        self.rows[iid]["ended"] = ts
        self.closes.append(iid)


assert status_for("MAISON 2", True, cfg) == "ทำงาน"
assert status_for("MAISON 2", False, cfg) == "ว่าง"
print("1) status_for service room (busy/idle)  OK")

st = FakeStore()
eng = PresenceEngine(st, cfg)
eng.observe(0, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
eng.observe(5, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
assert st.opens == [], st.opens
eng.observe(12, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
assert st.opens == [(1, "MAISON 2", "ว่าง")], st.opens
print("2) interval opens only after min_dwell  OK")

eng.observe(20, "Phai", "spa room", "MAISON 2", None, True, "Phai", "t1", 0.9)
eng.observe(31, "Phai", "spa room", "MAISON 2", None, True, "Phai", "t1", 0.9)
assert st.closes == [1], st.closes
assert st.opens[-1] == (2, "MAISON 2", "ทำงาน"), st.opens
print("3) customer arrival closes old + opens new status  OK")

eng.tick(50)  # gap 19 > track_grace -> they left
assert st.rows[2]["ended"] is not None
print("4) disappearance closes the open interval  OK")

st2 = FakeStore()
eng2 = PresenceEngine(st2, cfg)
eng2.observe(0, "Bua", "back hall", None, "ห้องน้ำ", False, "Bua", None, 0.8)
eng2.tick(20)  # gap 20 > track_grace -> infer inside ห้องน้ำ
assert st2.opens == [(1, "ห้องน้ำ", "พัก")], st2.opens
eng2.tick(130)  # gone past threshold_timeout -> close
assert st2.rows[1]["ended"] is not None
print("5) threshold inference opens then times out  OK")
print("all presence_engine tests pass")
