# Unit test for the SQLite event store + push predicate.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from event_store import EventStore, is_pushable

db = os.path.join(tempfile.gettempdir(), "ev_test.db")
if os.path.exists(db):
    os.remove(db)
s = EventStore(db)

i1 = s.add("2026-06-15 10:00:00", "front door", "staff", "Phai", "",
           "PHONE USE", "phone in hand 45s", "alert", "/p/a.jpg")
i2 = s.add("2026-06-15 10:01:00", "front door", "customer", "x", None,
           "ENTER", "arrives", "normal", None)
i3 = s.add("2026-06-15 10:02:00", "office", "staff", "Nicky", "",
           "ENTER", "staff enters room", "normal", None)  # NOT pushable

up = s.fetch_unpushed()
assert {r["id"] for r in up} == {i1, i2}, [r["id"] for r in up]
print("1) pushable subset = penalty + customer, office-ENTER excluded  OK")

s.mark_pushed([i1, i2])
assert s.fetch_unpushed() == [], "should be empty after mark_pushed"
print("2) mark_pushed clears the queue  OK")

q = s.query(actor="Phai")
assert len(q) == 1 and q[0]["event"] == "PHONE USE" and q[0]["image_path"] == "/p/a.jpg"
print("3) query by actor returns the row + image_path  OK")

assert is_pushable("ENTER", "normal", "staff") is False
assert is_pushable("SLEEPING", "alert", "staff") is True
assert is_pushable("ENTER", "normal", "customer") is True
print("4) push predicate correct  OK")

# retention: an old row is purged, its image path returned for cleanup
old = s.add("2020-01-01 00:00:00", "front door", "staff", "Phai", "",
            "SLEEPING", "old", "alert", "/p/old.jpg")
orphans = s.purge_old(days=30)
assert "/p/old.jpg" in orphans, orphans
assert not s.query(actor="Phai", since="2019-01-01")[-1:] or \
    all(r["id"] != old for r in s.query())
print("5) purge_old removes old rows + returns orphan image paths  OK")

s.close()
os.remove(db)
print("all event_store tests pass")
