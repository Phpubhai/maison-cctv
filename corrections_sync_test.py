# Unit test for CorrectionsSync: apply each correction once (idempotent),
# skip malformed ones. Fetcher is injected -> no network.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corrections_sync import CorrectionsSync


class FakeResolver:
    def __init__(self):
        self.calls = []

    def apply_correction(self, uid, name):
        self.calls.append((uid, name))


data = [{"id": 1, "trackUid": "spa room:1", "name": "Phai"},
        {"id": 2, "trackUid": "foot spa:7", "name": "Nicky"},
        {"id": 3, "trackUid": "x", "name": None}]   # malformed -> skipped
r = FakeResolver()
cs = CorrectionsSync(r, lambda: data, {"corrections": {"enabled": True}})

cs.apply_once()
cs.apply_once()  # second pass must NOT re-apply (idempotent on id)
assert r.calls == [("spa room:1", "Phai"), ("foot spa:7", "Nicky")], r.calls
print("1) applies valid corrections once, skips malformed  OK")

data.append({"id": 4, "trackUid": "reception:2", "name": "Bua"})
cs.apply_once()
assert r.calls[-1] == ("reception:2", "Bua"), r.calls
print("2) new corrections on a later poll are applied  OK")
print("all corrections_sync tests pass")
