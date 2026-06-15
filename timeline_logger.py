# Per-camera event timeline: append-only events.jsonl on disk, live broadcast
# over a WebSocket, evidence snapshots for alerts, and a small in-memory tail
# for the on-screen strip.
#
# Connect a client with e.g.:  websocat ws://127.0.0.1:8765
# Every event is one JSON object per line / per message:
#   {"timestamp": "...", "camera_id": "CAM_02", "label": "STAFF",
#    "event": "SLEEPING", "description": "...", "severity": "alert"}
import asyncio
import json
import os
import threading
import time
from collections import defaultdict, deque

import cv2

try:
    from websockets.asyncio.server import serve
except ImportError:  # package missing -> file logging still works
    serve = None


class TimelineLogger:
    # staff-misbehavior evidence is quarantined in its own folder
    PENALTY_EVENTS = {"SLEEPING", "PHONE USE", "GREETING MISSED", "ROOM MESSY",
                      "OBJECT ON FLOOR"}

    def __init__(self, cfg):
        self.path = cfg["events_path"]
        self.evid_dir = cfg["evidence_dir"]
        self.penalty_dir = cfg["penalty_dir"]
        self.tl_dir = cfg["timeline_dir"]
        os.makedirs(self.evid_dir, exist_ok=True)
        os.makedirs(self.penalty_dir, exist_ok=True)
        os.makedirs(self.tl_dir, exist_ok=True)
        self.recent = defaultdict(lambda: deque(maxlen=cfg["timeline_events"]))
        self.lock = threading.Lock()
        self.clients = set()
        self.loop = None
        if serve is None:
            print("websockets not installed -> events.jsonl only, no broadcast", flush=True)
        else:
            threading.Thread(target=self._ws_thread,
                             args=(cfg["ws_host"], cfg["ws_port"]), daemon=True).start()

    # --- public API -------------------------------------------------------
    def log(self, camera_id, label, event, description, severity,
            therapist_id=None):
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "camera_id": camera_id,
            "label": label,
            "event": event,
            "description": description,
            "severity": severity,  # normal | warning | alert
            "therapist_id": therapist_id,  # POS join key, null when unknown
        }
        line = json.dumps(entry, ensure_ascii=False)
        # each camera also keeps its own human-readable timeline .txt
        # (openable in Notepad); the combined events.jsonl is for software
        txt = (f"[{entry['timestamp']}] {entry['event']:<12} "
               f"{entry['label']:<10} {entry['description']}  ({entry['severity']})\n")
        tl_path = os.path.join(self.tl_dir,
                               f"{camera_id.replace(' ', '_')}_timeline.txt")
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            with open(tl_path, "a", encoding="utf-8") as f:
                f.write(txt)
        self.recent[camera_id].append(entry)
        print(f"[{entry['timestamp']}] {camera_id} {event}: {label} - {description}", flush=True)
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._broadcast(line), self.loop)

    def save_evidence(self, frame, box, camera_id, label, event,
                      duration=None, started=None):
        """Snapshot with the person marked in red. Staff misbehavior
        (PENALTY_EVENTS) lands in the Penalty folder, everything else in the
        general evidence folder. With duration/started the image (and the
        filename) say how long the behavior has been going on and since when."""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        tag = f"{label} {event}"
        safe = tag.replace(":", "_").replace(" ", "_")  # ":" is illegal on Windows
        fname = f"{stamp}_{camera_id.replace(' ', '_')}_{safe}"
        if duration is not None:
            fname += f"_{int(duration)}s"
        ev = frame.copy()
        cv2.rectangle(ev, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])),
                      (0, 0, 255), 3)
        ty = max(30, int(box[1]) - (36 if duration is not None else 8))
        cv2.putText(ev, tag, (int(box[0]), ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
        if duration is not None:
            since = (" since " + time.strftime("%H:%M:%S", time.localtime(started))
                     if started else "")
            cv2.putText(ev, f"for {int(duration)}s{since}", (int(box[0]), ty + 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
        out_dir = self.penalty_dir if event in self.PENALTY_EVENTS else self.evid_dir
        cv2.imwrite(os.path.join(out_dir, fname + ".jpg"), ev)

    def tail(self, camera_id):
        """Most recent events for one camera (oldest first), for the overlay."""
        return list(self.recent[camera_id])

    # --- websocket plumbing (background thread, own event loop) -----------
    def _ws_thread(self, host, port):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.clients.add(ws)
            try:
                await ws.wait_closed()
            finally:
                self.clients.discard(ws)

        async def main():
            async with serve(handler, host, port):
                print(f"event websocket on ws://{host}:{port}", flush=True)
                await asyncio.Future()  # run forever

        try:
            self.loop.run_until_complete(main())
        except OSError as e:
            print(f"websocket disabled ({e}) -> events.jsonl only", flush=True)
            self.loop = None

    async def _broadcast(self, message):
        for ws in list(self.clients):
            try:
                await ws.send(message)
            except Exception:
                self.clients.discard(ws)
