#!/usr/bin/env python3
"""Re-alerts collapse to one START + one END on the pushed path; the DB
pushed flag follows the same decision."""
import tempfile

tmp = tempfile.mkdtemp()
cfg = {
    "events_path": tmp + "/e.jsonl", "evidence_dir": tmp + "/ev",
    "penalty_dir": tmp + "/pen", "timeline_dir": tmp + "/tl",
    "timeline_events": 6, "timeline_db": tmp + "/e.db",
    "ws_host": "127.0.0.1", "ws_port": 8823,
    "customer_flow_cameras": ["front door"],
    "event_push": {"enabled": False},
}

from timeline_logger import TimelineLogger

log = TimelineLogger(cfg)

class StubPusher:
    def __init__(self): self.events = []
    def push(self, e): self.events.append(e)
    def upload(self, *a): pass

log.pusher = StubPusher()

def labels():
    return [(e["camera_id"], e["label"], e["actor"]) for e in log.pusher.events]

# 1) two PHONE USE re-alerts (same cam/actor) -> one push
log.log("reception", "STAFF:Tan", "PHONE USE", "start", "alert", duration=60)
log.log("reception", "STAFF:Tan", "PHONE USE", "re-alert", "alert", duration=360)
assert labels().count(("reception", "PHONE USE", "Tan")) == 1, labels()

# 2) END pushes (forced) and re-opens for a later episode
log.log("reception", "STAFF:Tan", "PHONE USE END", "done", "normal", duration=400)
assert ("reception", "PHONE USE END", "Tan") in labels(), labels()
log.log("reception", "STAFF:Tan", "PHONE USE", "again", "alert", duration=60)
assert labels().count(("reception", "PHONE USE", "Tan")) == 2, labels()

# 3) OBJECT ON FLOOR x3 then FLOOR CLEARED -> START + END only (2 pushes)
for d in (120, 420, 720):
    log.log("foot spa", "STAFF", "OBJECT ON FLOOR", "x", "alert", duration=d)
log.log("foot spa", "STAFF", "FLOOR CLEARED", "gone", "normal")
floor = [l for l in labels() if l[0] == "foot spa"]
assert floor == [("foot spa", "OBJECT ON FLOOR", "STAFF"),
                 ("foot spa", "FLOOR CLEARED", "STAFF")], floor

# 4) a different event on the same cam/actor is independent
log.log("reception", "STAFF:Tan", "SLEEPING", "head down", "alert", duration=90)
assert ("reception", "SLEEPING", "Tan") in labels(), labels()

# 5) customer ENTER at the entrance still pushes
log.log("front door", "customer", "ENTER", "arrived", "normal")
assert any(l[1] == "ENTER" for l in labels()), labels()

# 6) DB path: the suppressed re-alert is stored pushed=1, the START pushed=0
rows = log.store.fetch_unpushed()
phone_unpushed = [r for r in rows
                  if r["camera"] == "reception" and r["event"] == "PHONE USE"]
# only the two STARTs (not the suppressed re-alert) are unpushed
assert len(phone_unpushed) == 2, [dict(r) for r in phone_unpushed]
assert any(r["event"] == "PHONE USE END" for r in rows), "END must be unpushed too"

print("PASS: incidents collapse to START + END on both paths")
