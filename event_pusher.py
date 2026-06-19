#!/usr/bin/env python3
"""Outbound push of timeline events (and their snapshot images) to the event
server (yolo-server).

The main monitor records every event locally (SQLite + jsonl); this also sends
the pushable subset (penalties + customer events) to the POS timeline server at
{server_url}/events, and uploads the matching snapshot to {server_url}/snapshot
so the server has its own copy (works even when the server runs on a different
machine than the cameras).

Two background queues so a large image upload never delays the realtime events.
Both retry with exponential backoff and never block detection or lose data.
"""
import os
import queue
import threading
import time

try:
    import requests
except ImportError:                       # monitor still runs; push just off
    requests = None


class EventPusher:
    BATCH_MAX = 25
    BACKOFF_MAX = 60.0
    DROP_CODES = {400, 404, 413, 422}     # malformed/oversized -> drop, not wedge
    MAX_IMG_BYTES = 10 * 1024 * 1024

    def __init__(self, server_url, api_key, upload_images=True):
        base = server_url.rstrip("/")
        self.events_ep = base + "/events"
        self.snap_ep = base + "/snapshot/"
        self.upload_images = upload_images
        self.eq = queue.Queue()           # event dicts
        self.iq = queue.Queue()           # (rel, local_path) image jobs
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def start(self):
        threading.Thread(target=self._events_loop, daemon=True).start()
        if self.upload_images:
            threading.Thread(target=self._images_loop, daemon=True).start()
        return self

    # --- producers (called from the monitor; never block) -----------------
    def push(self, event):
        self.eq.put(event)

    def upload(self, rel, local_path):
        if self.upload_images:
            self.iq.put((rel, local_path))

    # --- workers ----------------------------------------------------------
    def _events_loop(self):
        while True:
            batch = [self.eq.get()]
            try:
                while len(batch) < self.BATCH_MAX:
                    batch.append(self.eq.get_nowait())
            except queue.Empty:
                pass
            self._send(f"{len(batch)} event(s)",
                       lambda: self.session.post(
                           self.events_ep, json=batch, timeout=10,
                           headers={"Content-Type": "application/json"}))

    def _images_loop(self):
        while True:
            rel, path = self.iq.get()
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError as e:
                print(f"[push] snapshot read failed ({e}); skip {rel}", flush=True)
                continue
            if len(data) > self.MAX_IMG_BYTES:
                print(f"[push] snapshot too large; skip {rel}", flush=True)
                continue
            url = self.snap_ep + rel.lstrip("/")
            self._send(f"snapshot {rel}",
                       lambda u=url, d=data: self.session.post(
                           u, data=d, timeout=30,
                           headers={"Content-Type": "image/jpeg"}))

    def _send(self, what, do_post):
        """POST with retry/backoff. 200/201 = done; permanent 4xx = drop;
        auth/transient/network = keep retrying so nothing is lost."""
        delay = 1.0
        while True:
            try:
                r = do_post()
                if r.status_code in (200, 201):
                    return
                if r.status_code in self.DROP_CODES:
                    print(f"[push] {r.status_code} dropping {what}: "
                          f"{r.text[:120]}", flush=True)
                    return
                if r.status_code in (401, 403):
                    print(f"[push] {r.status_code} auth failed -- check API_KEY; "
                          f"holding {what}, retry {delay:.0f}s", flush=True)
                else:
                    print(f"[push] {r.status_code} retry {what} in {delay:.0f}s",
                          flush=True)
            except requests.RequestException as e:
                print(f"[push] network error ({e}); retry {what} in {delay:.0f}s",
                      flush=True)
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
    p = EventPusher(ep["server_url"], ep["api_key"],
                    upload_images=ep.get("push_snapshots", True))
    p.start()
    print(f"event push -> {p.events_ep}"
          f"{'  (+snapshots)' if p.upload_images else ''}", flush=True)
    return p
