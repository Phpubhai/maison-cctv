# Unit test for the presence_intervals table on EventStore.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from event_store import EventStore

db = os.path.join(tempfile.gettempdir(), "pres_test.db")
if os.path.exists(db):
    os.remove(db)
s = EventStore(db)

i1 = s.open_interval("2026-06-22 13:40:00", "spa room", "Phai", "t1",
                     "MAISON 2", "ทำงาน", 0.9, "engine")
i2 = s.open_interval("2026-06-22 13:30:00", "foot spa", "Nicky", None,
                     "Foot Spa", "ทำงาน", 0.6, "engine")

up = s.fetch_unpushed_presence()
assert {r["id"] for r in up} == {i1, i2}, [r["id"] for r in up]
print("1) freshly opened intervals are unpushed  OK")

s.mark_presence_pushed([i1, i2])
assert s.fetch_unpushed_presence() == []
print("2) mark_presence_pushed clears the queue  OK")

s.close_interval(i1, "2026-06-22 15:00:00")
up = s.fetch_unpushed_presence()
assert [r["id"] for r in up] == [i1], [r["id"] for r in up]
assert up[0]["ended_at"] == "2026-06-22 15:00:00", up[0]["ended_at"]
print("3) closing an interval re-queues it with ended_at  OK")

openset = s.open_presence()
assert {r["therapist"] for r in openset} == {"Nicky"}, openset
print("4) open_presence returns only still-open intervals  OK")

s.close()
os.remove(db)
print("all presence_store tests pass")
