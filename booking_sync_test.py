# Unit test for the roster derived from bookings. cctvBookings has therapistName
# + status but NO room, so this only narrows WHO is on shift. Fetcher injected.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from booking_sync import roster_from_bookings, RosterSync

bks = [{"therapistName": "Phai", "status": "confirmed"},
       {"therapistName": "Nicky", "status": "cancelled"},   # not on shift
       {"therapistName": "Bua", "status": "checked_in"},
       {"therapistName": None, "status": "pending"}]         # no name -> skip
assert roster_from_bookings(bks) == {"Phai", "Bua"}, roster_from_bookings(bks)
print("1) roster = active bookings' therapist names  OK")

assert roster_from_bookings([]) == set()
print("2) no bookings -> empty roster  OK")


class FakeResolver:
    def __init__(self):
        self.roster = None

    def set_roster(self, names):
        self.roster = set(names)


r = FakeResolver()
RosterSync(r, lambda: bks, {"roster": {"enabled": True}}).refresh_once()
assert r.roster == {"Phai", "Bua"}, r.roster
print("3) RosterSync.refresh_once sets the resolver roster  OK")

r2 = FakeResolver()
RosterSync(r2, lambda: [], {"roster": {"enabled": True}}).refresh_once()
assert r2.roster is None, r2.roster   # empty roster never applied (fail-open)
print("4) empty roster is NOT applied (fail-open, allow all)  OK")
print("all booking_sync tests pass")
