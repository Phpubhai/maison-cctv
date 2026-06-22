#!/usr/bin/env python3
"""ROOM ENTER pushes a structured meta.room."""
import tempfile

tmp = tempfile.mkdtemp()
cfg = {
    "events_path": tmp + "/e.jsonl", "evidence_dir": tmp + "/ev",
    "penalty_dir": tmp + "/pen", "timeline_dir": tmp + "/tl",
    "timeline_events": 6, "timeline_db": tmp + "/e.db",
    "ws_host": "127.0.0.1", "ws_port": 8821,
    "customer_flow_cameras": ["front door"],
    "event_push": {"enabled": False},   # no real network; we stub the pusher
}

from timeline_logger import TimelineLogger

log = TimelineLogger(cfg)

class StubPusher:
    def __init__(self): self.events = []
    def push(self, e): self.events.append(e)
    def upload(self, *a): pass

log.pusher = StubPusher()   # capture what would be pushed
log.log("spa room", "STAFF", "ROOM ENTER", "entered MAISON 1", "normal",
        room="MAISON 1")

assert log.pusher.events, "nothing was pushed"
e = log.pusher.events[-1]
assert e["label"] == "ROOM ENTER", e
assert e["meta"]["room"] == "MAISON 1", e["meta"]
print("PASS: ROOM ENTER carries meta.room")
