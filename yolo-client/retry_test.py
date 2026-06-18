#!/usr/bin/env python3
"""Acceptance test: events survive a VPS outage.
Enqueue while the server is DOWN -> sender retries with backoff (no crash, no
block); bring the server UP -> the backlog flushes; GET confirms nothing lost.
Self-contained: starts an in-process mock on a port that is initially dead.
"""
import os
import threading
import time
from http.server import HTTPServer

PORT = 8100
# force our own settings (hermetic) -- ignore any SERVER_URL exported in the
# shell so the test always targets its own in-process server on PORT.
os.environ["SERVER_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["API_KEY"] = "testkey"
os.environ["CAMERA_ID"] = "cam-01"

import requests

from detect_and_push import Sender          # noqa: E402 (env must precede import)
from mock_server import Handler             # noqa: E402

sender = Sender()
sender.start()

# 1) server DOWN -> enqueue two events; sender must retry, not crash/block
print("--- server DOWN: enqueue 2 events ---")
t0 = time.time()
sender.enqueue({"ts": "2026-06-18T00:00:00Z", "camera_id": "cam-01",
                "label": "person", "confidence": 0.9, "count": 1, "meta": {}})
sender.enqueue({"ts": "2026-06-18T00:00:01Z", "camera_id": "cam-01",
                "label": "cup", "confidence": 0.8, "count": 2, "meta": {}})
# the detection side never blocks -- enqueue returns instantly
assert time.time() - t0 < 0.5, "enqueue blocked (it must not)"
print("  enqueue returned instantly (detection never blocks)  OK")
time.sleep(4)                               # observe retry/backoff logs above

# 2) server UP -> backlog must flush
print("--- server UP: backlog should flush ---")
httpd = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(5)

# 3) confirm BOTH events arrived, in order, nothing lost
g = requests.get(f"http://127.0.0.1:{PORT}/events",
                 headers={"X-API-Key": "testkey"}, timeout=5).json()
labels = [e["label"] for e in g["events"]]
print(f"  server has {g['count']} event(s): {labels}")
assert labels == ["person", "cup"], labels
print("PASS: queued events delivered after reconnect, none lost, no crash")
httpd.shutdown()
