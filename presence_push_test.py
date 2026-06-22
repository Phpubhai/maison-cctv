# Unit test for the presence push payload + flush loop (no real network).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pos_timeline import PushWorker, presence_payload

row = {"id": 5, "therapist": "Phai", "therapist_id": "t1", "room": "MAISON 2",
       "status": "ทำงาน", "started_at": "2026-06-22 13:40:00",
       "ended_at": "2026-06-22 15:00:00", "confidence": 0.9, "camera": "spa room"}
p = presence_payload(row)
assert p["id"] == 5 and p["therapist"] == "Phai" and p["room"] == "MAISON 2"
assert p["startedAt"] == "2026-06-22T13:40:00+07:00", p["startedAt"]
assert p["endedAt"] == "2026-06-22T15:00:00+07:00", p["endedAt"]
print("1) presence_payload maps + ISO+07 stamps  OK")

row2 = dict(row, id=6, ended_at=None)
assert presence_payload(row2)["endedAt"] is None
print("2) open interval -> endedAt null  OK")


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.marked = []

    def fetch_unpushed_presence(self, limit):
        return [r for r in self.rows if r["id"] not in self.marked]

    def mark_presence_pushed(self, ids):
        self.marked += ids


cfg = {"pos_timeline": {"enabled": True, "poll_secs": 5, "batch": 25},
       "pos_api": {"base_url": "http://x", "api_key": "k"}}
w = PushWorker(FakeStore([row, row2]), cfg, None)
posted = []
w._post_presence = lambda payload: (posted.append(payload), True)[1]
w._flush_presence()
assert {p["id"] for p in posted} == {5, 6}, posted
assert set(w.store.marked) == {5, 6}, w.store.marked
print("3) _flush_presence posts + marks every row on success  OK")

posted.clear()
w2 = PushWorker(FakeStore([row, row2]), cfg, None)
w2._post_presence = lambda payload: payload["id"] == 5  # 6 fails
posted2 = []
w2._post_presence = lambda payload: (posted2.append(payload), payload["id"] == 5)[1]
w2._flush_presence()
assert w2.store.marked == [5], w2.store.marked  # stops at the first failure
print("4) _flush_presence stops + retries on POST failure  OK")
print("all presence_push tests pass")
