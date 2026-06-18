#!/usr/bin/env python3
"""Integration test: timeline_logger.log() -> event_pusher -> event server.
Verifies the who/what/duration mapping and the pushable filter (penalty +
customer pushed; staff-normal NOT pushed). Run server.py on :8097 first.
"""
import tempfile
import time

import requests

PORT = 8097
KEY = "testkey"
tmp = tempfile.mkdtemp()
cfg = {
    "events_path": tmp + "/events.jsonl",
    "evidence_dir": tmp + "/evid", "penalty_dir": tmp + "/pen",
    "timeline_dir": tmp + "/tl", "timeline_events": 6,
    "timeline_db": tmp + "/events.db",
    "ws_host": "127.0.0.1", "ws_port": 8799,
    "event_push": {"enabled": True,
                   "server_url": f"http://127.0.0.1:{PORT}", "api_key": KEY},
}

from timeline_logger import TimelineLogger

log = TimelineLogger(cfg)
time.sleep(0.3)

# what we log -> what we expect on the POS timeline
log.log("front door", "STAFF:Phai", "PHONE USE", "phone in hand", "alert", duration=42)
log.log("reception", "customer", "ENTER", "arrived", "normal")          # customer -> push
log.log("foot spa", "STAFF:Nicky", "SLEEPING", "head down", "alert", duration=185)
log.log("office", "STAFF", "LEAVE", "left frame", "normal")             # staff normal -> NO push
log.log("makeup room", "STAFF", "ROOM MESSY", "messy", "alert", duration=320)
time.sleep(1.5)

g = requests.get(f"http://127.0.0.1:{PORT}/events?limit=20",
                 headers={"X-API-Key": KEY}, timeout=5).json()
rows = list(reversed(g["events"]))   # oldest first
print(f"server received {g['count']} event(s):")
for e in rows:
    who = e.get("actor")
    who = who.encode("ascii", "replace").decode() if who else who   # console-safe
    print(f"  who={who!r:10} what={e.get('label')!r:14} "
          f"dur={e.get('duration')!r:6} cam={e.get('camera_id')}")

labels = [(e["actor"], e["label"], e["duration"]) for e in rows]
assert ("Phai", "PHONE USE", 42.0) in labels, "phone use not pushed correctly"
assert ("Nicky", "SLEEPING", 185.0) in labels, "sleeping not pushed correctly"
assert ("ลูกค้า", "ENTER", None) in labels, "customer ENTER not pushed"
assert ("STAFF", "ROOM MESSY", 320.0) in labels, "room messy not pushed"
assert not any(l[1] == "LEAVE" for l in labels), "staff-normal LEAVE must NOT be pushed"
print("\nPASS: penalty+customer pushed with who/what/duration; staff-normal filtered")
