#!/usr/bin/env python3
"""End-to-end test for the event server (no camera needed).
Run server.py first (API_KEY=testkey PORT=8080), then this.
Covers: health, auth, validation, POST+GET round-trip, and live SSE relay.
"""
import json
import os
import threading
import time
import urllib.request

import requests

BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
KEY = os.environ.get("API_KEY", "testkey")     # must match the running server
H = {"X-API-Key": KEY}


def check(name, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'FAIL'} {name}: {got} (want {want})")
    assert ok, name


print("--- health (no key) ---")
check("health", requests.get(f"{BASE}/health", timeout=5).status_code, 200)

print("--- auth ---")
check("wrong key -> 401", requests.post(f"{BASE}/events", json={"camera_id": "c", "label": "x"},
      headers={"X-API-Key": "NOPE"}, timeout=5).status_code, 401)
check("no key -> 401", requests.get(f"{BASE}/events", timeout=5).status_code, 401)

print("--- validation ---")
check("missing label -> 400", requests.post(f"{BASE}/events", json={"camera_id": "c"},
      headers=H, timeout=5).status_code, 400)
check("missing camera_id -> 400", requests.post(f"{BASE}/events", json={"label": "x"},
      headers=H, timeout=5).status_code, 400)

print("--- SSE relay: subscribe, then POST, expect the event pushed live ---")
received = []


def listen():
    req = urllib.request.Request(f"{BASE}/stream", headers=H)
    with urllib.request.urlopen(req, timeout=10) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                received.append(json.loads(line[5:].strip()))
                return                       # got our one event, stop


t = threading.Thread(target=listen, daemon=True)
t.start()
time.sleep(1.0)                              # let the subscriber connect

print("--- POST one event (object) ---")
r = requests.post(f"{BASE}/events", headers=H, timeout=5, json={
    "ts": "2026-06-18T10:00:00Z", "camera_id": "cam-01", "label": "person",
    "confidence": 0.91, "count": 1, "meta": {"bbox": [1, 2, 3, 4]}})
check("POST -> 201", r.status_code, 201)
check("stored 1", r.json()["stored"], 1)

print("--- POST a batch (array) ---")
r = requests.post(f"{BASE}/events", headers=H, timeout=5, json=[
    {"camera_id": "cam-01", "label": "cup", "count": 2},
    {"camera_id": "cam-02", "label": "phone", "count": 1}])
check("batch -> 201", r.status_code, 201)
check("stored 2", r.json()["stored"], 2)

print("--- GET /events ---")
g = requests.get(f"{BASE}/events?limit=10", headers=H, timeout=5).json()
print(f"  server holds {g['count']} event(s): {[e['label'] for e in g['events']]}")
assert g["count"] == 3, g["count"]

t.join(timeout=3)
print(f"--- SSE: subscriber received live -> {received and received[0]['label']}")
assert received and received[0]["label"] == "person", "SSE did not push the event"

print("\nALL PASS: auth, validation, POST/GET, batch, and realtime SSE relay work")
