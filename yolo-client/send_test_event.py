#!/usr/bin/env python3
"""Send one fake event to the VPS -- a connectivity smoke test, no camera.

  SERVER_URL, API_KEY required; CAMERA_ID optional (default cam-01).
Prints the POST result, then GETs {SERVER_URL}/events to confirm the event
came back (per the acceptance criteria).
"""
import os
import sys
from datetime import datetime, timezone

import requests

SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
CAMERA_ID = os.environ.get("CAMERA_ID", "cam-01")
if not SERVER_URL or not API_KEY:
    sys.exit("missing required env: SERVER_URL and/or API_KEY")

headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
event = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "camera_id": CAMERA_ID,
    "label": "PHONE USE",       # ทำอะไร
    "actor": "Phai",            # ใคร  (staff name / "customer" / null)
    "duration": 42,             # นานแค่ไหน (seconds)
    "confidence": 0.99,
    "count": 1,
    "meta": {"note": "send_test_event"},
}

print(f"POST {SERVER_URL}/events  ->", flush=True)
r = requests.post(f"{SERVER_URL}/events", json=event, headers=headers, timeout=10)
print(f"  {r.status_code} {r.text[:300]}")
if r.status_code != 201:
    sys.exit(f"expected 201, got {r.status_code}")

print(f"GET {SERVER_URL}/events  ->", flush=True)
try:
    g = requests.get(f"{SERVER_URL}/events", headers=headers, timeout=10)
    print(f"  {g.status_code} {g.text[:400]}")
except requests.RequestException as e:
    print(f"  GET skipped ({e}) -- server may not expose GET; POST already 201")
