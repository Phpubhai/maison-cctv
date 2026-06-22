#!/usr/bin/env python3
"""EventStore.add honors an explicit pushed flag; defaults to is_pushable."""
import tempfile

from event_store import EventStore

tmp = tempfile.mkdtemp()
s = EventStore(tmp + "/e.db")

# default (no pushed arg): a normal staff event is NOT pushable -> pushed=1,
# so it must NOT appear in fetch_unpushed()
i_default = s.add("t", "cam", "staff", "X", None,
                  "PHONE USE END", "done", "normal")
# explicit pushed=0: force it onto the unpushed queue even though normal/staff
i_forced = s.add("t", "cam", "staff", "X", None,
                 "PHONE USE END", "done", "normal", pushed=0)
# explicit pushed=1: keep an otherwise-pushable alert OFF the queue
i_suppressed = s.add("t", "cam", "staff", "X", None,
                     "PHONE USE", "re-alert", "alert", pushed=1)

unpushed_ids = [r["id"] for r in s.fetch_unpushed()]
assert i_default not in unpushed_ids, "default normal event should be pushed=1"
assert i_forced in unpushed_ids, "explicit pushed=0 should be unpushed"
assert i_suppressed not in unpushed_ids, "explicit pushed=1 should be suppressed"
print("PASS: EventStore.add honors explicit pushed flag")
