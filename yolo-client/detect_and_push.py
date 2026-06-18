#!/usr/bin/env python3
"""Detect objects with YOLO and push events to the VPS over outbound HTTP.

Outbound-only: the camera machine never opens an inbound port. Events are
POSTed to {SERVER_URL}/events with an X-API-Key header. A background sender
thread with a retry queue + exponential backoff keeps the detection loop
non-blocking and survives network outages without losing events.

Config comes from environment variables (see README):
  SERVER_URL  required, e.g. https://api.yourdomain.com
  API_KEY     required, same value as the server
  CAMERA_ID   required, e.g. cam-01
  SOURCE      0 (webcam) | rtsp://... | path to a file   (default 0)
  MODEL       ultralytics weights                        (default yolov8n.pt)
  CONF        detection confidence threshold             (default 0.4)
  COOLDOWN_S  min seconds between repeat events per label (default 10)
  CLASSES     optional comma-separated class names to keep (e.g. person,cup)
"""
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import requests
from ultralytics import YOLO

# ── config ─────────────────────────────────────────────────────────────────
SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
CAMERA_ID = os.environ.get("CAMERA_ID", "")
SOURCE = os.environ.get("SOURCE", "0")
MODEL = os.environ.get("MODEL", "yolov8n.pt")
CONF = float(os.environ.get("CONF", "0.4"))
COOLDOWN_S = float(os.environ.get("COOLDOWN_S", "10"))
CLASSES = [c.strip() for c in os.environ.get("CLASSES", "").split(",") if c.strip()]

ENDPOINT = f"{SERVER_URL}/events"
BATCH_MAX = 25          # send up to this many queued events per request
BACKOFF_MAX = 60.0      # cap exponential backoff at this many seconds
# 4xx codes that mean THIS event is malformed and will never succeed -> drop it
# (a bad event must not wedge the queue). Auth errors (401/403) are NOT here:
# they're a fixable config problem, so we keep retrying and lose nothing.
DROP_CODES = {400, 404, 413, 422}


def now_iso():
    """Event time as ISO-8601 UTC, e.g. 2026-06-18T10:00:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def webcam_source(src):
    """'0' -> webcam index 0; otherwise an rtsp url / file path unchanged."""
    return int(src) if src.isdigit() else src


# ── sender: background thread, retry queue, exponential backoff ─────────────
class Sender(threading.Thread):
    """Owns the outbound HTTP. Detection only enqueues (never blocks). Events
    are retried until delivered; transient/5xx errors back off exponentially,
    permanent 4xx (bad body) are logged and dropped so one bad event can't
    wedge the queue forever."""

    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()              # unbounded -> no event loss
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": API_KEY,
                                     "Content-Type": "application/json"})

    def enqueue(self, event):
        self.q.put(event)                   # non-blocking, never drops

    def run(self):
        while True:
            batch = [self.q.get()]          # block until at least one event
            try:
                while len(batch) < BATCH_MAX:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            self._deliver(batch)

    def _deliver(self, batch):
        delay = 1.0
        while True:
            try:
                r = self.session.post(ENDPOINT, json=batch, timeout=10)
                if r.status_code == 201:
                    print(f"[sender] 201 stored {len(batch)} event(s)", flush=True)
                    return
                if r.status_code in DROP_CODES:
                    # malformed event -> won't fix on retry; drop + log
                    print(f"[sender] {r.status_code} dropping {len(batch)} "
                          f"event(s): {r.text[:160]}", flush=True)
                    return
                if r.status_code in (401, 403):
                    # key mismatch -> recoverable: hold the events and retry so
                    # nothing is lost once API_KEY is fixed on either side
                    print(f"[sender] {r.status_code} auth failed -- check API_KEY "
                          f"matches the server; holding {len(batch)} event(s), "
                          f"retrying in {delay:.0f}s", flush=True)
                else:
                    print(f"[sender] {r.status_code} retrying in {delay:.0f}s", flush=True)
            except requests.RequestException as e:
                print(f"[sender] network error ({e}); retrying in {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX)   # exponential backoff


# ── per-frame aggregation by label ─────────────────────────────────────────
def aggregate(result):
    """Collapse one frame's detections into {label: (count, max_conf, bbox)}.
    bbox is the highest-confidence box for that label, as [x1,y1,x2,y2] ints."""
    names = result.names
    agg = {}
    if result.boxes is None:
        return agg
    for b in result.boxes:
        label = names[int(b.cls)]
        if CLASSES and label not in CLASSES:
            continue
        conf = float(b.conf)
        cnt, best, _ = agg.get(label, (0, -1.0, None))
        box = [int(v) for v in b.xyxy[0]] if conf > best else _
        agg[label] = (cnt + 1, max(best, conf), box if conf > best else _)
    return agg


def main():
    missing = [n for n, v in (("SERVER_URL", SERVER_URL), ("API_KEY", API_KEY),
                              ("CAMERA_ID", CAMERA_ID)) if not v]
    if missing:
        sys.exit(f"missing required env: {', '.join(missing)}")

    sender = Sender()
    sender.start()
    print(f"detect_and_push: model={MODEL} source={SOURCE} conf={CONF} "
          f"cooldown={COOLDOWN_S}s -> {ENDPOINT}", flush=True)

    model = YOLO(MODEL)
    last_emit = {}        # label -> last emit wall time
    prev_present = set()  # labels seen in the previous frame

    for result in model.predict(source=webcam_source(SOURCE), stream=True,
                                conf=CONF, verbose=False):
        agg = aggregate(result)
        now = time.time()
        for label, (count, conf, bbox) in agg.items():
            newly = label not in prev_present
            due = now - last_emit.get(label, 0.0) >= COOLDOWN_S
            # debounce: emit when a label first appears OR it has persisted
            # past the cooldown -- never every frame
            if newly or due:
                sender.enqueue({
                    "ts": now_iso(),
                    "camera_id": CAMERA_ID,
                    "label": label,
                    "confidence": round(conf, 3),
                    "count": count,
                    "meta": {"bbox": bbox},
                })
                last_emit[label] = now
        prev_present = set(agg)


if __name__ == "__main__":
    main()
