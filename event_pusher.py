#!/usr/bin/env python3
"""Outbound push of timeline events to the realtime event server (yolo-server).

The main monitor records every event locally (SQLite + jsonl); this also sends
the pushable subset (penalties + customer events) to the POS timeline server at
{server_url}/events, so the POS sees them live.

Outbound-only HTTP POST on a background thread with a retry queue + exponential
backoff: it never blocks detection and never loses an event on a network blip.
Mirrors the standalone yolo-client contract, so ONE server serves both.
"""
import queue
import threading
import time

try:
    import requests
except ImportError:                       # monitor still runs; push just off
    requests = None


class EventPusher(threading.Thread):
    BATCH_MAX = 25
    BACKOFF_MAX = 60.0
    DROP_CODES = {400, 404, 413, 422}     # malformed event -> drop, don't wedge

    def __init__(self, server_url, api_key):
        super().__init__(daemon=True)
        self.endpoint = server_url.rstrip("/") + "/events"
        self.q = queue.Queue()            # unbounded -> never drops on enqueue
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key,
                                     "Content-Type": "application/json"})

    def push(self, event):
        self.q.put(event)                 # non-blocking; the monitor never waits

    def run(self):
        while True:
            batch = [self.q.get()]
            try:
                while len(batch) < self.BATCH_MAX:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            self._deliver(batch)

    def _deliver(self, batch):
        delay = 1.0
        while True:
            try:
                r = self.session.post(self.endpoint, json=batch, timeout=10)
                if r.status_code == 201:
                    return
                if r.status_code in self.DROP_CODES:
                    print(f"[push] {r.status_code} dropping {len(batch)} event(s): "
                          f"{r.text[:160]}", flush=True)
                    return
                if r.status_code in (401, 403):
                    print(f"[push] {r.status_code} auth failed -- check API_KEY; "
                          f"holding {len(batch)} event(s), retry {delay:.0f}s", flush=True)
                else:
                    print(f"[push] {r.status_code} retry in {delay:.0f}s", flush=True)
            except requests.RequestException as e:
                print(f"[push] network error ({e}); retry in {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, self.BACKOFF_MAX)


def maybe_start(cfg):
    """Start and return an EventPusher if event_push is enabled+configured,
    else None. Safe to call unconditionally -- callers push only when non-None."""
    ep = cfg.get("event_push") or {}
    if not ep.get("enabled"):
        return None
    if requests is None:
        print("event push disabled (requests not installed)", flush=True)
        return None
    p = EventPusher(ep["server_url"], ep["api_key"])
    p.start()
    print(f"event push -> {p.endpoint}", flush=True)
    return p
