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

import event_pusher
from event_store import EventStore, is_pushable

try:
    from websockets.asyncio.server import serve
except ImportError:  # package missing -> file logging still works
    serve = None


# Ongoing-condition events that open an incident on START and close it on END.
# The POS timeline gets one START (first alert) + one END (with total duration);
# the periodic re-alerts in between are not pushed. END events are force-pushed
# (they are logged "normal", which is_pushable would otherwise reject).
END_OF = {
    "SLEEPING END": "SLEEPING",
    "PHONE USE END": "PHONE USE",
    "ROOM TIDY": "ROOM MESSY",
    "FLOOR CLEARED": "OBJECT ON FLOOR",
    "TABLE CLEARED": "UNCLEARED TABLE",
}
START_EVENTS = set(END_OF.values())   # only these alerts are collapsed


# map the on-screen label to a structured (actor_type, actor_name) for the DB.
# "STAFF:Phai" -> ("staff","Phai"); "STAFF" -> ("staff",None);
# "customer" -> ("customer",None); "?" -> ("unknown",None)
def _split_actor(label):
    if not label or label == "?":
        return "unknown", None
    if label.startswith("STAFF:"):
        return "staff", label.split(":", 1)[1]
    if label == "STAFF":
        return "staff", None
    if label == "customer":
        return "customer", None
    return "staff", label  # a bare name (face-identified) -> staff by name


class TimelineLogger:
    # staff-misbehavior evidence is quarantined in its own folder
    PENALTY_EVENTS = {"SLEEPING", "PHONE USE", "GREETING MISSED", "ROOM MESSY",
                      "OBJECT ON FLOOR", "UNCLEARED TABLE"}

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
        # SQLite history (full local source of truth). Best-effort: if it
        # fails to open, the jsonl/.txt sinks still work.
        self.store = None
        try:
            self.store = EventStore(cfg["timeline_db"])
            days = cfg.get("timeline_retention_days")
            if days:
                for p in self.store.purge_old(days):
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
        except Exception as e:
            print(f"event store disabled ({e}) -> jsonl only", flush=True)
        # realtime push to the POS timeline server (None if not configured)
        self.pusher = event_pusher.maybe_start(cfg)
        # customer enter/leave only counts as a shop arrival at the entrance
        self.customer_flow_cameras = cfg.get("customer_flow_cameras") or []
        # open incidents -> {(camera_id, who, start_event)}; collapses re-alerts
        self._open_incidents = set()
        if serve is None:
            print("websockets not installed -> events.jsonl only, no broadcast", flush=True)
        else:
            threading.Thread(target=self._ws_thread,
                             args=(cfg["ws_host"], cfg["ws_port"]), daemon=True).start()

    # --- public API -------------------------------------------------------
    def _should_push(self, camera_id, event, severity, actor_type, who):
        """Which events reach the POS timeline, collapsing re-alerts.

        An ongoing condition pushes ONE START (first alert) and ONE END (its
        close event, force-pushed with the total duration); the periodic
        re-alerts in between are suppressed. Everything else uses the base
        subset (penalties + customer + warning/alert), with customer
        ENTER/LEAVE counting only at the entrance camera(s)."""
        # END of an ongoing condition: close the incident, always push (closure)
        if event in END_OF:
            self._open_incidents.discard((camera_id, who, END_OF[event]))
            return True
        if event == "ROOM ENTER":
            return True   # service tracking: always push room occupancy
        if not is_pushable(event, severity, actor_type):
            return False
        if actor_type == "customer" and event in ("ENTER", "LEAVE"):
            return camera_id in self.customer_flow_cameras
        # ongoing-condition START: push the first, suppress repeats until END
        if event in START_EVENTS and severity in ("warning", "alert"):
            key = (camera_id, who, event)
            if key in self._open_incidents:
                return False           # re-alert of an already-open incident
            self._open_incidents.add(key)
            return True
        return True

    def log(self, camera_id, label, event, description, severity,
            therapist_id=None, image_path=None, duration=None, room=None):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        actor_type, actor_name = _split_actor(label)
        who = actor_name or {"customer": "ลูกค้า", "staff": "STAFF"}.get(actor_type)
        push_it = self._should_push(camera_id, event, severity, actor_type, who)
        entry = {
            "timestamp": ts,
            "camera_id": camera_id,
            "label": label,
            "event": event,
            "description": description,
            "severity": severity,  # normal | warning | alert
            "therapist_id": therapist_id,  # POS join key, null when unknown
        }
        line = json.dumps(entry, ensure_ascii=False)
        # SQLite history (the queryable time/who/what table)
        if self.store is not None:
            try:
                self.store.add(ts, camera_id, actor_type, actor_name,
                               therapist_id, event, description, severity,
                               image_path, pushed=(0 if push_it else 1))
            except Exception as e:
                print(f"event store write failed: {e}", flush=True)
        # realtime push to the POS timeline: same subset the store marks
        # pushable (penalties + customer events). who = name, else role.
        if self.pusher is not None and push_it:
            meta = {"severity": severity, "description": description,
                    "therapist_id": therapist_id}
            if room:
                meta["room"] = room          # structured room id for the POS
            # snapshot stays local; send a URL the POS can open (served by the
            # event server's /snapshot route, key-protected, LAN only)
            if image_path:
                try:
                    rel = os.path.relpath(image_path,
                                          os.path.dirname(os.path.abspath(__file__)))
                    rel = rel.replace("\\", "/")
                    meta["image_url"] = "/snapshot/" + rel
                    self.pusher.upload(rel, image_path)   # send the file up too
                except ValueError:
                    pass                        # different drive -> no url
            self.pusher.push({
                "ts": ts,                       # เวลา (shop local time)
                "camera_id": camera_id,
                "label": event,                 # ทำอะไร (SLEEPING / PHONE USE ...)
                "actor": who,                   # ใคร (staff name / customer)
                "duration": duration,           # นานแค่ไหน (seconds, may be None)
                "meta": meta,
            })
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
        path = os.path.join(out_dir, fname + ".jpg")
        cv2.imwrite(path, ev)
        return path   # caller passes this to log(image_path=...) to link the row

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
