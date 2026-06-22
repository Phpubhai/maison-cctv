# Presence Timeline (camera side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a presence layer on top of the existing Yolo_Monitor detection
pipeline that turns per-frame sightings into per-therapist **room presence
intervals + status**, stores them locally, and pushes them to the POS — driving
a "now" board + room×time timeline.

**Architecture:** A new logical **room** layer (`rooms.py`) decouples rooms from
cameras (one camera → many rooms; camera-less rooms inferred from doorway
threshold zones). A shared **`PresenceEngine`**, keyed by resolved identity,
converts sightings into intervals with a status derived from room type + whether
a customer shares the room. Intervals live in a new SQLite table and are pushed
by the existing `PushWorker` to a new `/cctvPresence` endpoint. Penalty rules
keep running untouched.

**Tech Stack:** Python 3, SQLite (`sqlite3`), OpenCV/NumPy (already used),
ultralytics/ByteTrack (already used). No new third-party dependencies.

## Global Constraints

- Single source of truth for tunables is `config.py` (`CONFIG` dict); modules
  read from it, never hard-code — copied from `HANDOFF.md`/`README.md`.
- Tests in this repo are **standalone scripts** run as `python <name>_test.py`,
  using `assert` + `print(... OK)` and a final `print("all ... pass")`. No
  pytest. Match `store_test.py` / `room_event_test.py` exactly.
- Local timestamps are `"%Y-%m-%d %H:%M:%S"` (shop local). The POS contract uses
  ISO-8601 `+07:00` (Asia/Bangkok, no DST).
- Windows: `:` is illegal in filenames (already handled in `save_evidence`).
- Penalty rules (sleep/phone/tidy/floor/greeting) stay running for managers —
  do NOT remove or disable them.
- PDPA: never commit `faces/`, `*.npz`, or registry JSONs; evidence images stay
  LAN-only.
- Identity in THIS plan = whatever the existing pipeline already knows
  (`p.voter.name` from face match, else anonymous `"<camera>:<track_id>"`). The
  full Identity Resolver is Plan 2; the engine takes identity as an input so it
  drops in later with no engine change.

---

### Task 1: Logical room layer (`rooms.py` + rooms registry)

**Files:**
- Create: `rooms.py`
- Modify: `config.py` (add `"rooms"` registry inside `CONFIG`, after the
  existing `"room_zones"` block ~line 125)
- Test: `rooms_test.py`

**Interfaces:**
- Produces:
  - `which_room(camera_id, box, frame_shape, cfg) -> str | None`
  - `which_threshold(camera_id, box, frame_shape, cfg) -> str | None`
  - `room_type(room_name, cfg) -> str | None`  (`"service"|"front"|"back"|"rest"|"facility"`)
  - `rooms_for_camera(camera_id, cfg) -> list[str]`
  - `CONFIG["rooms"]`: `{name: {"type","via","camera", "zone"?(x1,y1,x2,y2), "door"?(x1,y1,x2,y2), "anchor"?}}`

- [ ] **Step 1: Write the failing test**

Create `rooms_test.py`:

```python
# Unit test for the logical room layer (rooms != cameras).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rooms import which_room, which_threshold, room_type, rooms_for_camera

cfg = {"rooms": {
    "Foot Spa": {"type": "service", "via": "camera", "camera": "foot spa"},
    "MAISON 1": {"type": "service", "via": "zone", "camera": "spa room",
                 "zone": (0.60, 0.0, 0.85, 0.90)},
    "MAISON 3": {"type": "service", "via": "zone", "camera": "spa room",
                 "zone": (0.20, 0.0, 0.40, 0.95)},
    "ห้องน้ำ": {"type": "facility", "via": "threshold", "camera": "back hall",
               "door": (0.40, 0.30, 0.60, 0.80)},
}}
frame = (1000, 1000, 3)  # h, w, c

assert which_room("spa room", (700, 400, 740, 500), frame, cfg) == "MAISON 1"
assert which_room("spa room", (290, 400, 310, 500), frame, cfg) == "MAISON 3"
assert which_room("spa room", (490, 400, 510, 500), frame, cfg) is None
assert which_room("foot spa", (10, 10, 50, 50), frame, cfg) == "Foot Spa"
print("1) which_room: zone match + whole-camera default  OK")

assert which_threshold("back hall", (480, 520, 520, 580), frame, cfg) == "ห้องน้ำ"
assert which_threshold("back hall", (10, 10, 30, 30), frame, cfg) is None
assert which_threshold("spa room", (480, 520, 520, 580), frame, cfg) is None
print("2) which_threshold: doorway zone only  OK")

assert room_type("Foot Spa", cfg) == "service"
assert room_type("ห้องน้ำ", cfg) == "facility"
assert room_type("nope", cfg) is None
print("3) room_type  OK")

assert set(rooms_for_camera("spa room", cfg)) == {"MAISON 1", "MAISON 3"}
print("4) rooms_for_camera  OK")
print("all rooms tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python rooms_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'rooms'`

- [ ] **Step 3: Create `rooms.py`**

```python
# rooms.py -- logical "room" layer on top of cameras.
#
# A camera is a device; a room is what reception cares about. One camera can
# hold several rooms (zones), and a room can be camera-less (presence inferred
# from a doorway/threshold zone). Everything is keyed off CONFIG["rooms"]:
#   {name: {"type","via","camera", "zone"?, "door"?, "anchor"?}}
#   type: "service" | "front" | "back" | "rest" | "facility"
#   via:  "camera" (whole frame = this room)
#       | "zone"   (box center inside "zone" (x1,y1,x2,y2) fractions)
#       | "threshold" (camera-less; "door" rect is its doorway on `camera`)


def _center_frac(box, frame_shape):
    h, w = frame_shape[:2]
    cx = ((box[0] + box[2]) / 2.0) / w
    cy = ((box[1] + box[3]) / 2.0) / h
    return cx, cy


def _in_rect(cx, cy, rect):
    x1, y1, x2, y2 = rect
    return x1 <= cx <= x2 and y1 <= cy <= y2


def which_room(camera_id, box, frame_shape, cfg):
    """Logical room for a box on a camera, or None. Priority: a matching
    via:"zone" room (center inside its zone), else the camera's via:"camera"
    room, else None. Threshold rooms are NOT returned here (camera-less)."""
    cx, cy = _center_frac(box, frame_shape)
    default = None
    for name, spec in cfg.get("rooms", {}).items():
        if spec.get("camera") != camera_id:
            continue
        via = spec.get("via")
        if via == "zone" and spec.get("zone") and _in_rect(cx, cy, spec["zone"]):
            return name
        if via == "camera":
            default = name
    return default


def which_threshold(camera_id, box, frame_shape, cfg):
    """Camera-less room whose doorway zone the box center sits in, or None.
    Used to infer presence once the person then disappears (see PresenceEngine)."""
    cx, cy = _center_frac(box, frame_shape)
    for name, spec in cfg.get("rooms", {}).items():
        if (spec.get("camera") == camera_id and spec.get("via") == "threshold"
                and spec.get("door") and _in_rect(cx, cy, spec["door"])):
            return name
    return None


def room_type(room_name, cfg):
    """'service' | 'front' | 'back' | 'rest' | 'facility' | None."""
    spec = cfg.get("rooms", {}).get(room_name)
    return spec.get("type") if spec else None


def rooms_for_camera(camera_id, cfg):
    return [n for n, s in cfg.get("rooms", {}).items()
            if s.get("camera") == camera_id]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python rooms_test.py`
Expected: PASS — ends with `all rooms tests pass`

- [ ] **Step 5: Add the starter rooms registry to `config.py`**

In `config.py`, immediately AFTER the `"room_zones": { ... },` block (ends
~line 125), add this entry to the `CONFIG` dict. The MAISON zone coords are
copied verbatim from the existing `room_zones` so they stay calibrated:

```python
    # logical rooms (rooms != cameras). The presence layer keys off this:
    # name -> {type, via, camera, zone?/door?, anchor?}. via "camera"=whole
    # frame, "zone"=region in the frame, "threshold"=camera-less room reached
    # through a doorway on `camera`. CALIBRATE zone/door rects per real layout
    # (see docs/superpowers/specs/2026-06-22-presence-timeline-design.md §12).
    "rooms": {
        "Reception":  {"type": "front",   "via": "camera", "camera": "reception"},
        "Front Desk": {"type": "front",   "via": "camera", "camera": "front door"},
        "Foot Spa":   {"type": "service", "via": "camera", "camera": "foot spa"},
        "ห้องพัก":     {"type": "rest",    "via": "camera", "camera": "office",
                       "anchor": True},
        "MAISON 1":   {"type": "service", "via": "zone", "camera": "spa room",
                       "zone": (0.63, 0.06, 0.82, 0.90)},
        "MAISON 2":   {"type": "service", "via": "zone", "camera": "spa room",
                       "zone": (0.56, 0.06, 0.63, 0.62)},
        "MAISON 3":   {"type": "service", "via": "zone", "camera": "spa room",
                       "zone": (0.23, 0.01, 0.36, 0.94)},
        "MAISON 4":   {"type": "service", "via": "zone", "camera": "spa room",
                       "zone": (0.36, 0.01, 0.41, 0.73)},
        # camera-less rooms: uncomment + calibrate "door" on the camera that
        # SEES the doorway (the person then disappears -> inferred inside):
        # "ห้องน้ำ":   {"type": "facility", "via": "threshold",
        #             "camera": "<cam that sees the door>", "door": (0.0,0.0,0.1,0.1)},
        # "ห้องซักผ้า": {"type": "back", "via": "threshold",
        #             "camera": "<cam>", "door": (0.0,0.0,0.1,0.1)},
    },
```

- [ ] **Step 6: Verify config still imports**

Run: `python -c "import config; print(sorted(config.CONFIG['rooms']))"`
Expected: prints the room names list (no exception), e.g.
`['Foot Spa', 'Front Desk', 'MAISON 1', 'MAISON 2', 'MAISON 3', 'MAISON 4', 'Reception', 'ห้องพัก']`

- [ ] **Step 7: Commit**

```bash
git add rooms.py rooms_test.py config.py
git commit -m "feat(rooms): logical room layer + rooms registry (rooms != cameras)"
```

---

### Task 2: `presence_intervals` table in the store

**Files:**
- Modify: `event_store.py` (extend `_SCHEMA` ~lines 13-30; add methods to
  `EventStore` after `mark_pushed` ~line 100)
- Test: `presence_store_test.py`

**Interfaces:**
- Consumes: `EventStore(db_path)` (Task uses the existing constructor).
- Produces (new `EventStore` methods):
  - `open_interval(ts, camera, therapist, therapist_id, room, status, confidence=None, source=None) -> int`
  - `close_interval(interval_id, ts) -> None`  (also re-sets `pushed=0`)
  - `fetch_unpushed_presence(limit=50) -> list[sqlite3.Row]`
  - `mark_presence_pushed(ids) -> None`
  - `open_presence() -> list[dict]`  (rows where `ended_at IS NULL`)

- [ ] **Step 1: Write the failing test**

Create `presence_store_test.py`:

```python
# Unit test for the presence_intervals table on EventStore.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from event_store import EventStore

db = os.path.join(tempfile.gettempdir(), "pres_test.db")
if os.path.exists(db):
    os.remove(db)
s = EventStore(db)

i1 = s.open_interval("2026-06-22 13:40:00", "spa room", "Phai", "t1",
                     "MAISON 2", "ทำงาน", 0.9, "engine")
i2 = s.open_interval("2026-06-22 13:30:00", "foot spa", "Nicky", None,
                     "Foot Spa", "ทำงาน", 0.6, "engine")

up = s.fetch_unpushed_presence()
assert {r["id"] for r in up} == {i1, i2}, [r["id"] for r in up]
print("1) freshly opened intervals are unpushed  OK")

s.mark_presence_pushed([i1, i2])
assert s.fetch_unpushed_presence() == []
print("2) mark_presence_pushed clears the queue  OK")

s.close_interval(i1, "2026-06-22 15:00:00")
up = s.fetch_unpushed_presence()
assert [r["id"] for r in up] == [i1], [r["id"] for r in up]
assert up[0]["ended_at"] == "2026-06-22 15:00:00", up[0]["ended_at"]
print("3) closing an interval re-queues it with ended_at  OK")

openset = s.open_presence()
assert {r["therapist"] for r in openset} == {"Nicky"}, openset
print("4) open_presence returns only still-open intervals  OK")

s.close()
os.remove(db)
print("all presence_store tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python presence_store_test.py`
Expected: FAIL — `AttributeError: 'EventStore' object has no attribute 'open_interval'`

- [ ] **Step 3: Extend the schema**

In `event_store.py`, the `_SCHEMA` string currently ends with the
`idx_events_pushed` index and a closing `"""`. Add a second table + index
before that closing `"""`:

```python
CREATE TABLE IF NOT EXISTS presence_intervals (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  therapist    TEXT,
  therapist_id TEXT,
  room         TEXT NOT NULL,
  status       TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  confidence   REAL,
  source       TEXT,
  camera       TEXT,
  pushed       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pres_pushed ON presence_intervals(pushed);
CREATE INDEX IF NOT EXISTS idx_pres_open ON presence_intervals(ended_at);
```

- [ ] **Step 4: Add the methods**

In `event_store.py`, after the `mark_pushed` method (~line 100) and before
`query`, add:

```python
    # --- presence intervals (the room×time timeline) ----------------------
    def open_interval(self, ts, camera, therapist, therapist_id, room, status,
                      confidence=None, source=None):
        """Start a presence interval (ended_at NULL). Born pushed=0."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO presence_intervals (therapist,therapist_id,room,"
                "status,started_at,ended_at,confidence,source,camera,pushed) "
                "VALUES (?,?,?,?,?,NULL,?,?,?,0)",
                (therapist, therapist_id, room, status, ts, confidence,
                 source, camera))
            self.conn.commit()
            return cur.lastrowid

    def close_interval(self, interval_id, ts):
        """Stamp ended_at and re-flag pushed=0 so the closed row (now with a
        duration) is re-pushed; doc id = interval id keeps the POS idempotent."""
        with self.lock:
            self.conn.execute(
                "UPDATE presence_intervals SET ended_at=?, pushed=0 WHERE id=?",
                (ts, interval_id))
            self.conn.commit()

    def fetch_unpushed_presence(self, limit=50):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM presence_intervals WHERE pushed=0 "
                "ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mark_presence_pushed(self, ids):
        if not ids:
            return
        with self.lock:
            self.conn.executemany(
                "UPDATE presence_intervals SET pushed=1 WHERE id=?",
                [(i,) for i in ids])
            self.conn.commit()

    def open_presence(self):
        """Currently-open intervals (the live 'now' set)."""
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM presence_intervals WHERE ended_at IS NULL "
                "ORDER BY id").fetchall()]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python presence_store_test.py`
Expected: PASS — ends with `all presence_store tests pass`

- [ ] **Step 6: Run the existing store test (no regression)**

Run: `python store_test.py`
Expected: PASS — ends with `all event_store tests pass`

- [ ] **Step 7: Commit**

```bash
git add event_store.py presence_store_test.py
git commit -m "feat(store): presence_intervals table + open/close/fetch methods"
```

---

### Task 3: `PresenceEngine` (sightings → intervals + status)

**Files:**
- Create: `presence_engine.py`
- Modify: `config.py` (add 3 tunables in the `--- tracking / timeline ---`
  block ~line 376)
- Test: `presence_engine_test.py`

**Interfaces:**
- Consumes: `room_type` from `rooms.py`; an `EventStore`-like object exposing
  `open_interval(...) -> int` and `close_interval(id, ts)`.
- Produces:
  - `status_for(room, has_customer, cfg) -> str`
  - `PresenceEngine(store, cfg, clock=time.time)` with:
    - `observe(now, key, camera, room, in_threshold_room, has_customer, therapist=None, therapist_id=None, confidence=None)`
    - `tick(now)`
  - `CONFIG["presence_min_dwell"]`, `CONFIG["threshold_timeout"]` (reads
    `CONFIG["track_grace"]` which already exists).

- [ ] **Step 1: Write the failing test**

Create `presence_engine_test.py`:

```python
# Unit test for PresenceEngine: sightings -> intervals + status, min-dwell,
# disappearance, and camera-less threshold inference. Uses a fake store and a
# manual clock so it is fully offline/deterministic.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from presence_engine import PresenceEngine, status_for

cfg = {
    "rooms": {
        "MAISON 2": {"type": "service", "via": "zone", "camera": "spa room"},
        "ห้องน้ำ": {"type": "facility", "via": "threshold", "camera": "back hall"},
    },
    "presence_min_dwell": 10.0,
    "track_grace": 15.0,
    "threshold_timeout": 100.0,
}


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.n = 0
        self.opens = []
        self.closes = []

    def open_interval(self, ts, camera, th, tid, room, status, conf, src):
        self.n += 1
        self.rows[self.n] = {"room": room, "status": status, "ended": None}
        self.opens.append((self.n, room, status))
        return self.n

    def close_interval(self, iid, ts):
        self.rows[iid]["ended"] = ts
        self.closes.append(iid)


assert status_for("MAISON 2", True, cfg) == "ทำงาน"
assert status_for("MAISON 2", False, cfg) == "ว่าง"
print("1) status_for service room (busy/idle)  OK")

st = FakeStore()
eng = PresenceEngine(st, cfg)
eng.observe(0, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
eng.observe(5, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
assert st.opens == [], st.opens
eng.observe(12, "Phai", "spa room", "MAISON 2", None, False, "Phai", "t1", 0.9)
assert st.opens == [(1, "MAISON 2", "ว่าง")], st.opens
print("2) interval opens only after min_dwell  OK")

eng.observe(20, "Phai", "spa room", "MAISON 2", None, True, "Phai", "t1", 0.9)
eng.observe(31, "Phai", "spa room", "MAISON 2", None, True, "Phai", "t1", 0.9)
assert st.closes == [1], st.closes
assert st.opens[-1] == (2, "MAISON 2", "ทำงาน"), st.opens
print("3) customer arrival closes old + opens new status  OK")

eng.tick(50)  # gap 19 > track_grace -> they left
assert st.rows[2]["ended"] is not None
print("4) disappearance closes the open interval  OK")

st2 = FakeStore()
eng2 = PresenceEngine(st2, cfg)
eng2.observe(0, "Bua", "back hall", None, "ห้องน้ำ", False, "Bua", None, 0.8)
eng2.tick(20)  # gap 20 > track_grace -> infer inside ห้องน้ำ
assert st2.opens == [(1, "ห้องน้ำ", "พัก")], st2.opens
eng2.tick(130)  # gone past threshold_timeout -> close
assert st2.rows[1]["ended"] is not None
print("5) threshold inference opens then times out  OK")
print("all presence_engine tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python presence_engine_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'presence_engine'`

- [ ] **Step 3: Create `presence_engine.py`**

```python
# presence_engine.py -- turns per-frame room sightings into therapist presence
# INTERVALS (room + status over time) and writes them to the store.
#
# Keyed by RESOLVED IDENTITY (one key per therapist, anonymous ids included),
# so a therapist seen across two cameras is one continuous timeline. Status =
# room type + whether a customer shares the room. Brief gaps are tolerated
# (track_grace); a person last seen at a camera-less room's doorway who then
# vanishes is inferred to be inside it until they reappear or time out.
import time

from rooms import room_type

# room type -> (status with a customer present, status when alone)
_STATUS = {
    "service":  ("ทำงาน", "ว่าง"),
    "front":    ("ต้อนรับ", "ว่าง"),
    "back":     ("งานหลังบ้าน", "งานหลังบ้าน"),
    "rest":     ("พัก", "พัก"),
    "facility": ("พัก", "พัก"),
}


def status_for(room, has_customer, cfg):
    busy, idle = _STATUS.get(room_type(room, cfg), ("ว่าง", "ว่าง"))
    return busy if has_customer else idle


class _P:
    __slots__ = ("committed", "pending_room", "pending_status", "pending_since",
                 "last_obs", "last_threshold_room", "therapist", "therapist_id",
                 "confidence")

    def __init__(self):
        self.committed = None          # {"id","room","status","camera","inferred"} or None
        self.pending_room = None
        self.pending_status = None
        self.pending_since = 0.0
        self.last_obs = 0.0
        self.last_threshold_room = None
        self.therapist = None
        self.therapist_id = None
        self.confidence = None


class PresenceEngine:
    def __init__(self, store, cfg, clock=time.time):
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.people = {}   # identity key -> _P
        self.min_dwell = cfg.get("presence_min_dwell", 12.0)
        self.track_grace = cfg.get("track_grace", 15.0)
        self.threshold_timeout = cfg.get("threshold_timeout", 1800.0)

    @staticmethod
    def _ts(now):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

    def _commit(self, p, now, room, status, camera, inferred=False):
        if p.committed:
            self.store.close_interval(p.committed["id"], self._ts(now))
        iid = self.store.open_interval(self._ts(now), camera, p.therapist,
                                       p.therapist_id, room, status,
                                       p.confidence, "engine")
        p.committed = {"id": iid, "room": room, "status": status,
                       "camera": camera, "inferred": inferred}
        p.pending_room = p.pending_status = None

    def _close(self, p, now):
        if p.committed:
            self.store.close_interval(p.committed["id"], self._ts(now))
            p.committed = None
        p.pending_room = p.pending_status = None

    def observe(self, now, key, camera, room, in_threshold_room, has_customer,
                therapist=None, therapist_id=None, confidence=None):
        """One sighting of `key` (resolved identity) on `camera`. `room` = the
        visible room (or None); `in_threshold_room` = a camera-less room name
        when the person is standing in its doorway zone (else None)."""
        p = self.people.get(key)
        if p is None:
            p = self.people[key] = _P()
        p.last_obs = now
        p.therapist, p.therapist_id, p.confidence = therapist, therapist_id, confidence

        if room is None and in_threshold_room is not None:
            # at a camera-less room's doorway; not "inside" yet -- remember it,
            # decide on disappearance (tick()).
            p.last_threshold_room = in_threshold_room
            return
        p.last_threshold_room = None
        if room is None:
            return   # in frame but in no room (corridor/transit): keep current

        status = status_for(room, has_customer, self.cfg)
        if (p.committed and p.committed["room"] == room
                and p.committed["status"] == status):
            p.pending_room = p.pending_status = None
            return
        if p.pending_room == room and p.pending_status == status:
            if now - p.pending_since >= self.min_dwell:
                self._commit(p, now, room, status, camera)
        else:
            p.pending_room, p.pending_status, p.pending_since = room, status, now

    def tick(self, now):
        """Handle disappearances: close intervals, or open an inferred
        camera-less room when the last sighting was at its doorway."""
        for key, p in list(self.people.items()):
            gap = now - p.last_obs
            if p.committed and p.committed.get("inferred"):
                if gap > self.threshold_timeout:
                    self._close(p, now)
                continue
            if gap <= self.track_grace:
                continue
            if p.committed is None and p.last_threshold_room is None:
                continue
            if p.last_threshold_room is not None:
                room = p.last_threshold_room
                status = status_for(room, False, self.cfg)
                camera = p.committed["camera"] if p.committed else ""
                self._commit(p, now, room, status, camera, inferred=True)
                p.last_threshold_room = None
            else:
                self._close(p, now)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python presence_engine_test.py`
Expected: PASS — ends with `all presence_engine tests pass`

- [ ] **Step 5: Add the tunables to `config.py`**

In `config.py`, inside the `# --- tracking / timeline ---` block (after
`"re_alert_secs": 300.0,` ~line 382), add:

```python
    # --- presence engine (room×time intervals) ---------------------------
    "presence_min_dwell": 12.0,    # must stay in a room/status this long before
                                   # an interval opens (kills corridor flicker)
    "threshold_timeout": 1800.0,   # inferred camera-less stay (e.g. ห้องน้ำ): if
                                   # never reappears within this, mark "ไม่เห็น"
                                   # (they likely left via another exit)
```

- [ ] **Step 6: Verify config imports**

Run: `python -c "import config; print(config.CONFIG['presence_min_dwell'], config.CONFIG['threshold_timeout'])"`
Expected: `12.0 1800.0`

- [ ] **Step 7: Commit**

```bash
git add presence_engine.py presence_engine_test.py config.py
git commit -m "feat(presence): PresenceEngine -> room intervals + status derivation"
```

---

### Task 4: Wire the engine into `tracker.py` and `main.py`

**Files:**
- Modify: `tracker.py` (import ~line 17-20; `__init__` signature line 71 + body
  ~line 77; `update` ~after line 410 and ~after line 477; add a new method)
- Modify: `main.py` (import ~line 31-35; construct engine ~after line 143; pass
  to `TrackManager` lines 152-153; `tick` in the loop ~after line 219)
- Test: `presence_wire_test.py`

**Interfaces:**
- Consumes: `which_room`, `which_threshold` (Task 1); `PresenceEngine` (Task 3);
  existing `staff_therapist_id` (already imported in `tracker.py`).
- Produces: `TrackManager(camera_id, cfg, logger, eyes, faces=None, enroller=None, engine=None)`;
  `TrackManager._presence_observe(p, tid, box, frame_shape, customer_rooms, now)`.

- [ ] **Step 1: Write the failing test**

Create `presence_wire_test.py`:

```python
# Unit test for TrackManager._presence_observe: it must report a staff person's
# room to the engine, keyed by name (or anon "<camera>:<tid>"), with the room's
# customer flag. Uses stubs so no camera/models are needed.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import TrackManager

cfg = {
    "rooms": {"Foot Spa": {"type": "service", "via": "camera", "camera": "foot spa"}},
    "re_alert_secs": 300.0,
}


class FakeEngine:
    def __init__(self):
        self.calls = []

    def observe(self, now, key, camera, room, door, has_cust, **kw):
        self.calls.append({"key": key, "camera": camera, "room": room,
                           "door": door, "has_cust": has_cust, **kw})


class _Voter:
    def __init__(self, name, role):
        self.name = name
        self.role = role


class _Person:
    def __init__(self, name, role):
        self.announced = True
        self.voter = _Voter(name, role)


tm = TrackManager("foot spa", cfg, logger=None, eyes=None, faces=None)
tm.engine = FakeEngine()
frame = (1000, 1000, 3)

# anonymous staff (no name) -> key "<camera>:<tid>", confidence 0.0
tm._presence_observe(_Person(None, "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
c = tm.engine.calls[-1]
assert c["room"] == "Foot Spa" and c["key"] == "foot spa:7", c
assert c["confidence"] == 0.0 and c["has_cust"] is False, c
print("1) anonymous staff reported with anon key  OK")

# named staff + a customer in the room -> name key, has_cust True, conf 1.0
tm._presence_observe(_Person("Phai", "staff"), 7, (10, 10, 50, 50), frame,
                     {"Foot Spa": True}, 100.0)
c = tm.engine.calls[-1]
assert c["key"] == "Phai" and c["confidence"] == 1.0 and c["has_cust"] is True, c
print("2) named staff + customer in room  OK")

# a customer track is never reported as presence
before = len(tm.engine.calls)
tm._presence_observe(_Person(None, "customer"), 9, (10, 10, 50, 50), frame, {}, 100.0)
assert len(tm.engine.calls) == before
print("3) customers are not reported  OK")
print("all presence_wire tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python presence_wire_test.py`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument` is
NOT raised, but `AttributeError: 'TrackManager' object has no attribute
'_presence_observe'` (the method does not exist yet).

- [ ] **Step 3: Add the import in `tracker.py`**

After the existing `from posture import (...)` import (~line 18-19), add:

```python
from rooms import which_room, which_threshold
```

- [ ] **Step 4: Add the `engine` parameter to `TrackManager.__init__`**

Change the signature on line 71 from:

```python
    def __init__(self, camera_id, cfg, logger, eyes, faces=None, enroller=None):
```

to:

```python
    def __init__(self, camera_id, cfg, logger, eyes, faces=None, enroller=None,
                 engine=None):
```

Then, right after `self.enroller = enroller` (~line 77), add:

```python
        # shared presence engine (keyed by identity, one per process). None ->
        # presence reporting is simply off (penalty pipeline unaffected).
        self.engine = engine
```

- [ ] **Step 5: Add the `_presence_observe` method**

In `tracker.py`, add this method to `TrackManager` immediately before
`_update_presence` (~line 333):

```python
    def _presence_observe(self, p, tid, box, frame_shape, customer_rooms, now):
        """Report one staff person's room to the shared presence engine, keyed
        by resolved identity (name, else anon "<camera>:<tid>")."""
        if self.engine is None or not p.announced or p.voter.role != "staff":
            return
        room = which_room(self.camera_id, box, frame_shape, self.cfg)
        door = which_threshold(self.camera_id, box, frame_shape, self.cfg)
        name = p.voter.name
        key = name or f"{self.camera_id}:{tid}"
        has_cust = bool(customer_rooms.get(room))
        self.engine.observe(now, key, self.camera_id, room, door, has_cust,
                            therapist=name,
                            therapist_id=staff_therapist_id(name),
                            confidence=1.0 if name else 0.0)
```

- [ ] **Step 6: Call it from `update` (compute customer rooms, then observe)**

In `update`, just after `holders = self._phone_holders(phones, rows, frame.shape)`
(~line 410), add the customer-rooms pre-pass:

```python
        # which logical rooms currently hold a customer (drives "ทำงาน" status)
        customer_rooms = {}
        for tid_c, box_c, p_c, _ in rows:
            if p_c.voter.role == "customer":
                rc = which_room(self.camera_id, box_c, frame.shape, self.cfg)
                if rc:
                    customer_rooms[rc] = True
```

Then, inside the `for tid, box, p, pose in rows:` loop, immediately after the
existing room-occupancy block (the `if self.room_zones and p.announced:` block
that ends with `p.room = room`, ~line 477), add:

```python
            # presence layer: feed the shared engine (room intervals + status)
            self._presence_observe(p, tid, box, frame.shape, customer_rooms, now)
```

- [ ] **Step 7: Run the wire test to verify it passes**

Run: `python presence_wire_test.py`
Expected: PASS — ends with `all presence_wire tests pass`

- [ ] **Step 8: Wire the engine into `main.py`**

In `main.py`, add to the imports (after `from pos_timeline import ...` line 31):

```python
from presence_engine import PresenceEngine
```

After the PushWorker block (lines 141-143), add the engine construction:

```python
    # presence engine: ONE shared instance, keyed by identity across cameras
    engine = PresenceEngine(logger.store, CONFIG) if logger.store is not None else None
```

Change the `trackers` construction (lines 152-153) to pass `engine`:

```python
    trackers = {cid: TrackManager(cid, CONFIG, logger, eyes, faces,
                                  enrollers.get(cid), engine) for cid in cam_ids}
```

Inside the analysis branch, right after
`people = trackers[cid].update(now, frame, detections, poses, phones)`
(line 219), add the per-cycle tick:

```python
                if engine is not None:
                    engine.tick(now)
```

- [ ] **Step 9: Verify everything still imports**

Run: `python -c "import main, tracker, presence_engine; print('import ok')"`
Expected: `import ok` (no exception)

- [ ] **Step 10: Run the room-event regression test**

Run: `python room_event_test.py`
Expected: PASS — `PASS: ROOM ENTER carries meta.room`

- [ ] **Step 11: Commit**

```bash
git add tracker.py main.py presence_wire_test.py
git commit -m "feat(presence): wire PresenceEngine into tracker + main loop"
```

---

### Task 5: Push presence intervals to the POS (`/cctvPresence`)

**Files:**
- Modify: `pos_timeline.py` (add a module-level `presence_payload`; extend
  `PushWorker.__init__`, `run`, and add `_flush_presence` ~lines 96-139)
- Test: `presence_push_test.py`

**Interfaces:**
- Consumes: `EventStore.fetch_unpushed_presence`, `mark_presence_pushed`
  (Task 2).
- Produces:
  - `presence_payload(row) -> dict` (module-level, pure)
  - `PushWorker` also flushes presence each cycle to `<base_url>/cctvPresence`.

- [ ] **Step 1: Write the failing test**

Create `presence_push_test.py`:

```python
# Unit test for the presence push payload + flush loop (no real network).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pos_timeline import PushWorker, presence_payload

row = {"id": 5, "therapist": "Phai", "therapist_id": "t1", "room": "MAISON 2",
       "status": "ทำงาน", "started_at": "2026-06-22 13:40:00",
       "ended_at": "2026-06-22 15:00:00", "confidence": 0.9, "camera": "spa room"}
p = presence_payload(row)
assert p["id"] == 5 and p["therapist"] == "Phai" and p["room"] == "MAISON 2"
assert p["startedAt"] == "2026-06-22T13:40:00+07:00", p["startedAt"]
assert p["endedAt"] == "2026-06-22T15:00:00+07:00", p["endedAt"]
print("1) presence_payload maps + ISO+07 stamps  OK")

row2 = dict(row, id=6, ended_at=None)
assert presence_payload(row2)["endedAt"] is None
print("2) open interval -> endedAt null  OK")


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.marked = []

    def fetch_unpushed_presence(self, limit):
        return [r for r in self.rows if r["id"] not in self.marked]

    def mark_presence_pushed(self, ids):
        self.marked += ids


cfg = {"pos_timeline": {"enabled": True, "poll_secs": 5, "batch": 25},
       "pos_api": {"base_url": "http://x", "api_key": "k"}}
w = PushWorker(FakeStore([row, row2]), cfg, None)
posted = []
w._post_presence = lambda payload: (posted.append(payload), True)[1]
w._flush_presence()
assert {p["id"] for p in posted} == {5, 6}, posted
assert set(w.store.marked) == {5, 6}, w.store.marked
print("3) _flush_presence posts + marks every row on success  OK")

posted.clear()
w2 = PushWorker(FakeStore([row, row2]), cfg, None)
w2._post_presence = lambda payload: payload["id"] == 5  # 6 fails
posted2 = []
w2._post_presence = lambda payload: (posted2.append(payload), payload["id"] == 5)[1]
w2._flush_presence()
assert w2.store.marked == [5], w2.store.marked  # stops at the first failure
print("4) _flush_presence stops + retries on POST failure  OK")
print("all presence_push tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python presence_push_test.py`
Expected: FAIL — `ImportError: cannot import name 'presence_payload'`

- [ ] **Step 3: Add `presence_payload` to `pos_timeline.py`**

In `pos_timeline.py`, after the `_image_url` function (~line 88) and before the
`# ── push worker ──` banner, add:

```python
def _iso7(s):
    """'YYYY-MM-DD HH:MM:SS' (shop local) -> ISO-8601 +07:00."""
    return s.replace(" ", "T") + "+07:00" if s else None


def presence_payload(r):
    """Map a presence_intervals row to the /cctvPresence request body."""
    return {
        "id": r["id"],
        "therapist": r["therapist"],
        "therapistId": r["therapist_id"],
        "room": r["room"],
        "status": r["status"],
        "startedAt": _iso7(r["started_at"]),
        "endedAt": _iso7(r["ended_at"]),
        "confidence": r["confidence"],
        "camera": r["camera"],
    }
```

- [ ] **Step 4: Extend `PushWorker` to flush presence**

In `pos_timeline.py`, in `PushWorker.__init__` (~lines 96-108), after the line
`self.url = (api.get("base_url", "").rstrip("/") + "/cctvTimeline") if api.get("base_url") else None`,
add the presence URL:

```python
        self.presence_url = (api.get("base_url", "").rstrip("/") + "/cctvPresence"
                             if api.get("base_url") else None)
```

In `run` (~lines 110-120), change the `try` body so it flushes both:

```python
            try:
                self._flush()
                self._flush_presence()
            except Exception as e:
                print(f"timeline push error: {e}", flush=True)
```

After the `_flush` method (~line 139), add `_flush_presence` and
`_post_presence`:

```python
    def _flush_presence(self):
        if not self.presence_url:
            return
        rows = self.store.fetch_unpushed_presence(self.batch)
        sent = []
        for r in rows:
            if self._post_presence(presence_payload(r)):
                sent.append(r["id"])
            else:
                break  # POS unreachable -- retry the whole batch next cycle
        self.store.mark_presence_pushed(sent)
        if sent:
            print(f"pushed {len(sent)} presence row(s) to POS", flush=True)

    def _post_presence(self, payload):
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.presence_url, data=data, method="POST",
                                     headers={"content-type": "application/json",
                                              "x-cctv-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python presence_push_test.py`
Expected: PASS — ends with `all presence_push tests pass`

- [ ] **Step 6: Commit**

```bash
git add pos_timeline.py presence_push_test.py
git commit -m "feat(push): push presence intervals to /cctvPresence (idempotent)"
```

---

### Task 6: POS contract doc (`cctv-presence.v1.md`)

**Files:**
- Create: `contracts/cctv-presence.v1.md`

**Interfaces:**
- Consumes: the payload shape from Task 5 (`presence_payload`).
- Produces: the work order the maisonPOS team implements (Plan 3).

- [ ] **Step 1: Write the contract**

Create `contracts/cctv-presence.v1.md`:

````markdown
# CCTV → POS Presence — v1 (work order for the POS team)

> **Status:** ready for POS implementation. **Owner:** maisonPOS (server).
> **Producer:** Yolo_Monitor (camera) pushes; POS displays.
> Companion: `cctv-timeline.v1.md` / `cctv-api.v1.md` — same auth, region,
> idempotency pattern. Source design:
> `docs/superpowers/specs/2026-06-22-presence-timeline-design.md`.

The camera tracks which therapist is in which room and pushes **presence
intervals** (room + status over time). The POS shows them as a live "now" board
and a room×time timeline. One Cloud Function + one Firestore collection.

## 1. `POST /cctvPresence` (new Cloud Function)

Mirror `cctvTimeline` exactly: `onRequest({region:"asia-southeast1", cors:true})`,
guarded by `x-cctv-key` (same `CCTV_API_KEY` secret).

### Request

| Part | Value |
|---|---|
| Method | `POST` |
| Headers | `x-cctv-key: <secret>`, `content-type: application/json` |

Body (one interval):
```json
{
  "id": 5,
  "therapist": "Phai",
  "therapistId": "t1",
  "room": "MAISON 2",
  "status": "ทำงาน",
  "startedAt": "2026-06-22T13:40:00+07:00",
  "endedAt": "2026-06-22T15:00:00+07:00",
  "confidence": 0.9,
  "camera": "spa room"
}
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `id` | number | no | camera-local interval id; **use as Firestore doc id** → idempotent |
| `therapist` | string | yes | name when known, else null (anonymous) |
| `therapistId` | string | yes | POS join key when known |
| `room` | string | no | logical room name (NOT camera) |
| `status` | string | no | `ทำงาน`/`ว่าง`/`ต้อนรับ`/`งานหลังบ้าน`/`พัก` |
| `startedAt` | string ISO+07 | no | interval start |
| `endedAt` | string ISO+07 | yes | null = still open (live "now") |
| `confidence` | number | yes | 0–1; low → show as tentative |
| `camera` | string | yes | source camera (debug) |

### Behaviour
1. Validate key → else `401`.
2. **Upsert** `presence/{id}` with the fields above + server `receivedAt`.
   (Doc id = `id`, so re-pushing the same interval — e.g. when it closes and
   `endedAt` is filled — overwrites, never duplicates.)
3. No Data Connect writes — display-only signal.

### Response `200`
```json
{ "ok": true }
```

### Errors
| Code | When |
|---|---|
| `400` | missing `id`/`room`/`status`/`startedAt` |
| `401` | bad/missing key |
| `405` | not POST |
| `500` | Firestore write failed |

## 2. Firestore `presence/{id}`
Same fields as the body + `receivedAt: Timestamp`. **Rules:** staff (authed)
read; only the Cloud Function (admin) writes. **Retention:** scheduled delete of
docs whose `endedAt` is older than N days (e.g. 90); the camera keeps full
history locally.

## 3. POS UI (Plan 3 — Nuxt.js + Pinia + DaisyUI; see design §8)
- Pinia store `usePresenceStore` subscribes `onSnapshot(presence where
  endedAt == null)` once; both pages read from it.
- **หน้า "ตอนนี้":** room cards (DaisyUI avatar group + status badge), grouped
  by room. Tap avatar → correction (needs the `corrections/` channel, Plan 2).
- **หน้า "ไทม์ไลน์":** store action queries a day range → Gantt (rooms × time)
  with status colors + avatar chips.

## 4. Test independently (no camera)
1. `curl` with a fake interval (good key → 200 + doc; same `id` again with
   `endedAt` set → overwrites; bad key → 401; missing `room` → 400).
2. Drop a few mock docs (one open `endedAt:null`, one closed) → verify the
   "now" board shows only open ones and the timeline renders both.

## Changelog
- **v1 (2026-06-22):** initial — `cctvPresence`, `presence/` shape.
````

- [ ] **Step 2: Self-review the contract**

Read the file back and confirm: field names match `presence_payload` from Task 5
exactly (`id, therapist, therapistId, room, status, startedAt, endedAt,
confidence, camera`); the idempotency note matches the `pushed`-reset behaviour
in Task 2 (`close_interval` re-queues so the closed interval overwrites its own
doc). Fix any mismatch inline.

- [ ] **Step 3: Commit**

```bash
git add contracts/cctv-presence.v1.md
git commit -m "docs(contract): cctv-presence.v1 — presence intervals work order"
```

---

## Self-Review (plan vs spec)

- **§4 Room/Zone model** → Task 1 (`rooms.py`, `which_room`/`which_threshold`/
  `room_type`, rooms registry incl. zone + threshold + anchor flag). ✓
- **§6 Presence Engine (intervals + status matrix + customer-in-room +
  debounce + threshold timeout)** → Task 3 (`status_for` matrix, `observe`/
  `tick`, `min_dwell`, `threshold_timeout`) + Task 4 (customer_rooms feed). ✓
- **§6.2 `presence_intervals` table** → Task 2. ✓
- **§7 POS push (`/cctvPresence`, idempotent, `pushed` flag) + contract** →
  Task 5 + Task 6. ✓
- **Keep penalties running** → Tasks 4 only ADDS; never removes penalty paths. ✓
- **Identity as an input (Plan 2 drops in)** → Task 4 keys on `p.voter.name` or
  anon; engine takes identity as args. ✓

**Deferred to follow-up plans (NOT in this plan — see below):** §5 Identity
Resolver, the face-teach tool, the `corrections/` loop, and §8 the two DaisyUI
pages. The "now"/timeline still work in this plan using existing identity (named
where the face pipeline knows the person, anonymous otherwise).

**Placeholder scan:** none — every step has real code/commands. The commented
`ห้องน้ำ`/`ห้องซักผ้า` entries in Task 1 Step 5 are intentional calibration
templates (real config the user fills per layout, per design §12), not code
placeholders.

**Type consistency:** `open_interval`/`close_interval` signatures match between
Task 2, Task 3 (`_commit`/`_close` calls), and the `FakeStore` in tests;
`presence_payload` keys (Task 5) match the `presence_intervals` columns (Task 2)
and the contract (Task 6); `observe(...)` kwargs match between Task 3, Task 4
(`_presence_observe`), and `FakeEngine`.

---

## Follow-up plans (separate, after this one)

- **Plan 2 — Identity Resolver + face-teach tool (this repo, Python).** The
  5-tier resolver (design §5): reception correction > anchor face-tag > POS
  booking prior > track hand-off > anonymous, with roster + confidence decay; a
  supervised "teach who is who" tool capturing snapshots at the anchor camera
  (replacing the disabled `auto_enroll`); and the `corrections/` poll loop. It
  plugs into `_presence_observe` as the identity source — no engine change.
- **Plan 3 — POS pages (maisonPOS repo, Nuxt.js + Pinia + DaisyUI + Cloud
  Function).** Implement `cctvPresence` per `contracts/cctv-presence.v1.md`, the
  `presence/` rules + retention, a Pinia store `usePresenceStore`, and the two
  pages (`ตอนนี้` board + `ไทม์ไลน์` Gantt).
