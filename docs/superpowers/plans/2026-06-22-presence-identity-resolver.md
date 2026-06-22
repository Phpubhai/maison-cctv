# Presence — Identity Resolver + face-teach Implementation Plan (Plan 2/3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-therapist identity reliable for the presence layer: a name
sticks through the many frames where CCTV faces don't match, reception can fix a
wrong/anonymous track, off-shift names are ignored, and there's a supervised
"teach who is who" tool at the staff-room (anchor) camera.

**Architecture:** A new `IdentityResolver` fuses signals by confidence —
reception correction > face match (filtered by today's roster) > sticky prior
with time decay > anonymous — and is consulted from `TrackManager._presence_observe`
(the hook Plan 1 left). Two daemon syncers feed it: `RosterSync` (today's names
from `GET /cctvBookings`) and `CorrectionsSync` (reception fixes from the POS).
A `face_teach` tool captures candidate faces at the anchor camera into a review
queue that a human labels into the registry (replacing the disabled
`auto_enroll`).

**Tech Stack:** Python 3, `sqlite3`, OpenCV (YuNet/SFace, already used), NumPy,
`urllib` (already the HTTP client in `pos_timeline.py`). No new dependencies.

## Global Constraints

- Tunables live in `config.py` (`CONFIG`); modules read from it, never hard-code.
- Tests are **standalone scripts** run as `python <name>_test.py` (assert +
  `print(... OK)` + final `print("all ... pass")`). No pytest. Match the style
  of `store_test.py` / `presence_engine_test.py`.
- Windows console is cp1252: a command that merely PRINTS Thai may raise
  `UnicodeEncodeError` — re-run with `PYTHONIOENCODING=utf-8`. A genuine assert
  failure that prints Thai means a real bug.
- Builds on Plan 1 (`docs/superpowers/plans/2026-06-22-presence-timeline-camera.md`):
  `PresenceEngine`, `presence_intervals`, and `TrackManager._presence_observe`
  already exist. The resolver plugs into `_presence_observe`; the
  `PresenceEngine` does NOT change.
- Identity keying contract (unchanged from Plan 1): a NAMED person's engine key
  is their display name (so they merge across cameras); an anonymous person's
  key is `"<camera>:<track_id>"` (per-camera). The resolver returns the key.
- Honesty: this plan does NOT attempt cross-camera re-identification of
  anonymous people (identical uniforms + unreliable CCTV faces make it unsafe).
  See "Deferred / out of scope".
- PDPA: never commit `faces/`, `*.npz`, the face queue, or registry JSONs.
- `cctvBookings` (see `contracts/cctv-api.v1.md`) has `therapistName` +
  `startTime` but **no room**, so the roster can only narrow WHO is on shift,
  not WHICH room — see Task 3.

---

### Task 1: `IdentityResolver` core

**Files:**
- Create: `identity_resolver.py`
- Modify: `config.py` (add an `--- identity resolver ---` block in the
  `CONFIG` dict, after the new `presence_min_dwell`/`threshold_timeout` lines
  from Plan 1)
- Test: `identity_resolver_test.py`

**Interfaces:**
- Consumes: `staff_name`, `staff_therapist_id` from `person_labeler.py`.
- Produces:
  - `IdentityResolver(cfg, clock=time.time)` with:
    - `resolve(now, track_uid, face_id=None) -> {"key","name","therapist_id","confidence","source"}`
    - `apply_correction(track_uid, name)`
    - `set_roster(names | None)`
    - `depart(track_uid)`
  - `CONFIG["id_face_conf"]`, `CONFIG["id_conf_decay"]`, `CONFIG["id_min_conf"]`,
    plus `CONFIG["corrections"]`, `CONFIG["corrections_poll_secs"]`,
    `CONFIG["roster"]`, `CONFIG["roster_poll_secs"]`, `CONFIG["face_teach"]`
    (used by later tasks; all added here in one cohesive block).

- [ ] **Step 1: Write the failing test**

Create `identity_resolver_test.py`:

```python
# Unit test for IdentityResolver: correction > face(+roster) > sticky-decay >
# anonymous. No models/network -- pure logic with a manual clock (the `now`
# argument). Runs without a staff.json (registry empty -> staff_name returns
# the face id verbatim, which is what these asserts expect).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from identity_resolver import IdentityResolver

cfg = {"id_face_conf": 0.9, "id_conf_decay": 0.1, "id_min_conf": 0.3}
r = IdentityResolver(cfg)

res = r.resolve(0, "spa room:1", face_id="Phai")
assert res["name"] == "Phai" and res["key"] == "Phai" and res["source"] == "face"
assert res["confidence"] == 0.9, res
print("1) face match -> named, key = name (merges across cameras)  OK")

res = r.resolve(1, "spa room:1", face_id=None)
assert res["name"] == "Phai" and res["source"] == "sticky", res
assert res["confidence"] == 0.8, res
print("2) sticky keeps the name through a faceless frame (decays)  OK")

res = r.resolve(10, "spa room:1", face_id=None)  # big gap -> conf < min
assert res["name"] is None and res["source"] == "anonymous", res
assert res["key"] == "spa room:1", res
print("3) confidence below floor -> anonymous (never a wrong name)  OK")

r.apply_correction("foot spa:7", "Nicky")
res = r.resolve(20, "foot spa:7", face_id=None)
assert res["name"] == "Nicky" and res["source"] == "correction", res
assert res["confidence"] == 1.0, res
print("4) reception correction overrides everything  OK")

r.set_roster({"Phai", "Nicky"})
res = r.resolve(30, "reception:3", face_id="Bua")     # Bua not on shift
assert res["name"] is None and res["source"] == "anonymous", res
res = r.resolve(31, "reception:4", face_id="Phai")    # Phai on shift
assert res["name"] == "Phai" and res["source"] == "face", res
print("5) roster filter ignores off-shift face names  OK")

r.depart("foot spa:7")
res = r.resolve(40, "foot spa:7", face_id=None)
assert res["name"] is None, res                       # correction forgotten
print("6) depart() clears per-track state  OK")
print("all identity_resolver tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python identity_resolver_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'identity_resolver'`

- [ ] **Step 3: Create `identity_resolver.py`**

```python
# identity_resolver.py -- decides which therapist a tracked person is, fusing
# signals by confidence. Plan 1's presence layer used the raw face id; this adds
# what makes per-person identity usable on CCTV:
#   - STICKINESS WITH DECAY: CCTV faces match only every so often, so a name
#     must survive the faceless frames in between, fading only if never
#     re-confirmed (better than flickering name -> anon -> name).
#   - RECEPTION CORRECTION: a human fix is sticky and wins over everything.
#   - ROSTER FILTER: a face match to someone not on today's shift is ignored.
#   - ANONYMOUS: a stable per-track key, never a guessed name.
#
# It does NOT re-identify anonymous people across cameras (identical uniforms +
# unreliable CCTV faces make that unsafe). A NAMED person's key is their name
# (so they merge across cameras); an anonymous person's key is the track_uid.
import time

from person_labeler import staff_name, staff_therapist_id


class IdentityResolver:
    def __init__(self, cfg, clock=time.time):
        self.cfg = cfg
        self.clock = clock
        self.assign = {}        # track_uid -> {name, face_id, conf, t}
        self.corrections = {}   # track_uid -> display name (sticky override)
        self.roster = None      # set of allowed display names, or None = all
        self.high_conf = cfg.get("id_face_conf", 0.9)
        self.decay = cfg.get("id_conf_decay", 0.02)   # conf lost per second
        self.min_conf = cfg.get("id_min_conf", 0.3)   # below -> anonymous

    # --- inputs ----------------------------------------------------------
    def set_roster(self, names):
        """Today's on-duty display names. None = allow all (no filtering)."""
        self.roster = set(names) if names is not None else None

    def apply_correction(self, track_uid, name):
        """Reception fixed this track's name -- sticky, wins over everything."""
        self.corrections[track_uid] = name

    def depart(self, track_uid):
        """Track ended (LEAVE): forget its per-track state."""
        self.assign.pop(track_uid, None)
        self.corrections.pop(track_uid, None)

    # --- resolution ------------------------------------------------------
    @staticmethod
    def _result(key, name, therapist_id, conf, source):
        return {"key": key, "name": name, "therapist_id": therapist_id,
                "confidence": round(conf, 3), "source": source}

    def resolve(self, now, track_uid, face_id=None):
        """Return {key, name, therapist_id, confidence, source} for one sighting.
        `face_id` is the enrolled id FaceMatcher produced this frame (or None)."""
        # 1. manual correction -- absolute, sticky
        if track_uid in self.corrections:
            name = self.corrections[track_uid]
            return self._result(name, name, None, 1.0, "correction")

        # 2. face match this frame, subject to the roster filter
        if face_id:
            name = staff_name(face_id)
            if self.roster is None or name in self.roster:
                self.assign[track_uid] = {"name": name, "face_id": face_id,
                                          "conf": self.high_conf, "t": now}
                return self._result(name, name, staff_therapist_id(face_id),
                                    self.high_conf, "face")

        # 3. sticky prior assignment, decayed since its last sighting
        a = self.assign.get(track_uid)
        if a is not None:
            conf = a["conf"] - self.decay * max(0.0, now - a["t"])
            a["t"] = now
            if conf >= self.min_conf:
                a["conf"] = conf
                return self._result(a["name"], a["name"],
                                    staff_therapist_id(a["face_id"]), conf,
                                    "sticky")
            del self.assign[track_uid]   # too uncertain -> anonymous

        # 4. anonymous: stable per-track key, no name
        return self._result(track_uid, None, None, 0.0, "anonymous")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python identity_resolver_test.py`
Expected: PASS — ends with `all identity_resolver tests pass`

- [ ] **Step 5: Add the identity config block to `config.py`**

In `config.py`, right after the Plan 1 presence block (the
`"threshold_timeout": 1800.0,` line in the `--- presence engine ---` block),
add:

```python
    # --- identity resolver (Plan 2) --------------------------------------
    "id_face_conf": 0.9,       # confidence assigned on a face match
    "id_conf_decay": 0.02,     # confidence lost per second a face is NOT re-seen
    "id_min_conf": 0.3,        # below this, a name is dropped -> anonymous
    # reception name corrections pulled from the POS (write side = Plan 3).
    "corrections": {"enabled": False},
    "corrections_poll_secs": 5,
    # today's roster from bookings -> a face name not on shift is ignored.
    # NOTE: cctvBookings has therapistName but no room, so this narrows WHO is
    # on shift, not WHICH room (see contracts/cctv-api.v1.md).
    "roster": {"enabled": False},
    "roster_poll_secs": 300,
    # supervised face teaching at the anchor (staff-room) camera. The anchor
    # camera is the room flagged anchor:True in CONFIG["rooms"].
    "face_teach": {"enabled": False,
                   "queue_dir": os.path.join(_HERE, "face_queue"),
                   "capture_every": 1.5,   # seconds between captures per track
                   "samples_cap": 30},     # max embeddings kept per person
```

- [ ] **Step 6: Verify config imports**

Run: `python -c "import config; print(config.CONFIG['id_face_conf'], config.CONFIG['roster']['enabled'])"`
Expected: `0.9 False`

- [ ] **Step 7: Commit**

```bash
git add identity_resolver.py identity_resolver_test.py config.py
git commit -m "feat(identity): IdentityResolver (correction > face/roster > sticky > anon)"
```

---

### Task 2: Corrections sync (consume reception fixes)

**Files:**
- Create: `corrections_sync.py`
- Test: `corrections_sync_test.py`

**Interfaces:**
- Consumes: `IdentityResolver.apply_correction` (Task 1); `CONFIG["pos_api"]`,
  `CONFIG["corrections"]`, `CONFIG["corrections_poll_secs"]`.
- Produces:
  - `CorrectionsSync(resolver, fetch, cfg)` (a `threading.Thread`) with
    `apply_once()` and `run()`.
  - `make_corrections_fetch(cfg) -> callable() -> list[dict]` (HTTP; returns
    `[{"id","trackUid","name"}, ...]`).

- [ ] **Step 1: Write the failing test**

Create `corrections_sync_test.py`:

```python
# Unit test for CorrectionsSync: apply each correction once (idempotent),
# skip malformed ones. Fetcher is injected -> no network.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corrections_sync import CorrectionsSync


class FakeResolver:
    def __init__(self):
        self.calls = []

    def apply_correction(self, uid, name):
        self.calls.append((uid, name))


data = [{"id": 1, "trackUid": "spa room:1", "name": "Phai"},
        {"id": 2, "trackUid": "foot spa:7", "name": "Nicky"},
        {"id": 3, "trackUid": "x", "name": None}]   # malformed -> skipped
r = FakeResolver()
cs = CorrectionsSync(r, lambda: data, {"corrections": {"enabled": True}})

cs.apply_once()
cs.apply_once()  # second pass must NOT re-apply (idempotent on id)
assert r.calls == [("spa room:1", "Phai"), ("foot spa:7", "Nicky")], r.calls
print("1) applies valid corrections once, skips malformed  OK")

data.append({"id": 4, "trackUid": "reception:2", "name": "Bua"})
cs.apply_once()
assert r.calls[-1] == ("reception:2", "Bua"), r.calls
print("2) new corrections on a later poll are applied  OK")
print("all corrections_sync tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python corrections_sync_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'corrections_sync'`

- [ ] **Step 3: Create `corrections_sync.py`**

```python
# corrections_sync.py -- pulls reception's name corrections from the POS and
# applies them to the IdentityResolver. The POS WRITE side (a `corrections`
# feed reception writes when it taps an avatar) is Plan 3; here we only consume
# whatever the fetcher returns. Daemon thread; fetcher injected for testing.
import threading
import time
import urllib.request

import json as _json


class CorrectionsSync(threading.Thread):
    def __init__(self, resolver, fetch, cfg):
        super().__init__(daemon=True)
        self.resolver = resolver
        self.fetch = fetch          # callable() -> [{"id","trackUid","name"}, ...]
        self.poll = cfg.get("corrections_poll_secs", 5)
        self.enabled = bool(cfg.get("corrections", {}).get("enabled"))
        self._seen = set()          # correction ids already applied (idempotent)

    def apply_once(self):
        for c in self.fetch() or []:
            cid = c.get("id")
            if cid in self._seen:
                continue
            uid, name = c.get("trackUid"), c.get("name")
            if uid and name:
                self.resolver.apply_correction(uid, name)
            self._seen.add(cid)     # mark seen even if malformed (won't requeue)

    def run(self):
        if not self.enabled:
            print("corrections sync disabled", flush=True)
            return
        while True:
            try:
                self.apply_once()
            except Exception as e:
                print(f"corrections sync error: {e}", flush=True)
            time.sleep(self.poll)


def make_corrections_fetch(cfg):
    """HTTP fetcher: GET <pos_api.base_url>/cctvCorrections -> list. Returns an
    empty list on any error so the sync loop just retries next poll."""
    api = cfg.get("pos_api", {})
    base = api.get("base_url", "").rstrip("/")
    key = api.get("api_key", "")
    url = base + "/cctvCorrections" if base else None

    def fetch():
        if not url:
            return []
        req = urllib.request.Request(url, headers={"x-cctv-key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return []
                body = _json.loads(resp.read().decode("utf-8"))
                return body.get("corrections", []) if isinstance(body, dict) else body
        except Exception:
            return []

    return fetch
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python corrections_sync_test.py`
Expected: PASS — ends with `all corrections_sync tests pass`

- [ ] **Step 5: Commit**

```bash
git add corrections_sync.py corrections_sync_test.py
git commit -m "feat(identity): CorrectionsSync -> apply reception fixes to resolver"
```

---

### Task 3: Roster sync (today's names from bookings)

**Files:**
- Create: `booking_sync.py`
- Test: `booking_sync_test.py`

**Interfaces:**
- Consumes: `IdentityResolver.set_roster` (Task 1); `CONFIG["pos_api"]`,
  `CONFIG["roster"]`, `CONFIG["roster_poll_secs"]`.
- Produces:
  - `roster_from_bookings(bookings) -> set[str]` (pure)
  - `RosterSync(resolver, fetch, cfg)` (a `threading.Thread`) with
    `refresh_once()` and `run()`.
  - `make_bookings_fetch(cfg) -> callable() -> list[dict]` (HTTP).

- [ ] **Step 1: Write the failing test**

Create `booking_sync_test.py`:

```python
# Unit test for the roster derived from bookings. cctvBookings has therapistName
# + status but NO room, so this only narrows WHO is on shift. Fetcher injected.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from booking_sync import roster_from_bookings, RosterSync

bks = [{"therapistName": "Phai", "status": "confirmed"},
       {"therapistName": "Nicky", "status": "cancelled"},   # not on shift
       {"therapistName": "Bua", "status": "checked_in"},
       {"therapistName": None, "status": "pending"}]         # no name -> skip
assert roster_from_bookings(bks) == {"Phai", "Bua"}, roster_from_bookings(bks)
print("1) roster = active bookings' therapist names  OK")

assert roster_from_bookings([]) == set()
print("2) no bookings -> empty roster  OK")


class FakeResolver:
    def __init__(self):
        self.roster = None

    def set_roster(self, names):
        self.roster = set(names)


r = FakeResolver()
RosterSync(r, lambda: bks, {"roster": {"enabled": True}}).refresh_once()
assert r.roster == {"Phai", "Bua"}, r.roster
print("3) RosterSync.refresh_once sets the resolver roster  OK")

r2 = FakeResolver()
RosterSync(r2, lambda: [], {"roster": {"enabled": True}}).refresh_once()
assert r2.roster is None, r2.roster   # empty roster never applied (fail-open)
print("4) empty roster is NOT applied (fail-open, allow all)  OK")
print("all booking_sync tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python booking_sync_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'booking_sync'`

- [ ] **Step 3: Create `booking_sync.py`**

```python
# booking_sync.py -- pulls today's bookings from the POS (GET /cctvBookings, see
# contracts/cctv-api.v1.md) and feeds the IdentityResolver a ROSTER: the
# therapist names expected today. A face match to someone NOT on the roster is
# then ignored (cuts wrong matches).
#
# LIMITATION: cctvBookings has therapistName + startTime but NO room, so this
# can only narrow WHO is on shift -- it cannot say WHICH ROOM a therapist is in.
# Room-level booking matching would need a `room` field added to the contract.
import threading
import time
import urllib.request

import json as _json

_ACTIVE = {"pending", "confirmed", "checked_in"}


def roster_from_bookings(bookings):
    """Set of therapist names from today's still-active bookings."""
    return {b["therapistName"] for b in bookings or []
            if b.get("therapistName") and b.get("status") in _ACTIVE}


class RosterSync(threading.Thread):
    def __init__(self, resolver, fetch, cfg):
        super().__init__(daemon=True)
        self.resolver = resolver
        self.fetch = fetch          # callable() -> [booking dict, ...]
        self.poll = cfg.get("roster_poll_secs", 300)
        self.enabled = bool(cfg.get("roster", {}).get("enabled"))

    def refresh_once(self):
        roster = roster_from_bookings(self.fetch() or [])
        if roster:                  # fail-open: never lock to an empty roster
            self.resolver.set_roster(roster)

    def run(self):
        if not self.enabled:
            print("roster sync disabled", flush=True)
            return
        while True:
            try:
                self.refresh_once()
            except Exception as e:
                print(f"roster sync error: {e}", flush=True)
            time.sleep(self.poll)


def make_bookings_fetch(cfg):
    """HTTP fetcher: GET <pos_api.base_url>/cctvBookings -> [bookings]. Returns
    an empty list on any error so the loop just retries next poll."""
    api = cfg.get("pos_api", {})
    base = api.get("base_url", "").rstrip("/")
    key = api.get("api_key", "")
    url = base + "/cctvBookings" if base else None

    def fetch():
        if not url:
            return []
        req = urllib.request.Request(url, headers={"x-cctv-key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return []
                body = _json.loads(resp.read().decode("utf-8"))
                return body.get("bookings", []) if isinstance(body, dict) else body
        except Exception:
            return []

    return fetch
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python booking_sync_test.py`
Expected: PASS — ends with `all booking_sync tests pass`

- [ ] **Step 5: Commit**

```bash
git add booking_sync.py booking_sync_test.py
git commit -m "feat(identity): RosterSync -> today's names from bookings filter"
```

---

### Task 4: Wire the resolver into `tracker.py` and `main.py`

**Files:**
- Modify: `tracker.py` (`TrackManager.__init__` — add `resolver=None`;
  `_presence_observe` — route through the resolver; the LEAVE loop ~line 566 —
  call `resolver.depart`)
- Modify: `main.py` (construct resolver + start the two syncers; pass resolver
  to `TrackManager`)
- Test: `identity_wire_test.py`

**Interfaces:**
- Consumes: `IdentityResolver` (Task 1), `RosterSync`/`make_bookings_fetch`
  (Task 3), `CorrectionsSync`/`make_corrections_fetch` (Task 2).
- Produces: `TrackManager(..., engine=None, resolver=None)`; `_presence_observe`
  now consults `self.resolver` when present.

- [ ] **Step 1: Write the failing test**

Create `identity_wire_test.py`:

```python
# Unit test: _presence_observe must route identity through the resolver when one
# is set (forwarding face_id = p.voter.name), and fall back to Plan 1 behavior
# when there is no resolver. Stubs only -- no camera/models.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import TrackManager

cfg = {"rooms": {"Foot Spa": {"type": "service", "via": "camera",
                              "camera": "foot spa"}},
       "re_alert_secs": 300.0}


class FakeEngine:
    def __init__(self):
        self.calls = []

    def observe(self, now, key, camera, room, door, has_cust, **kw):
        self.calls.append({"key": key, "room": room, "has_cust": has_cust, **kw})


class FakeResolver:
    def __init__(self):
        self.seen = []

    def resolve(self, now, track_uid, face_id=None):
        self.seen.append((track_uid, face_id))
        return {"key": "Phai", "name": "Phai", "therapist_id": "t1",
                "confidence": 0.9, "source": "face"}


class _V:
    def __init__(self, name, role):
        self.name = name
        self.role = role


class _P:
    def __init__(self, name, role):
        self.announced = True
        self.voter = _V(name, role)


tm = TrackManager("foot spa", cfg, logger=None, eyes=None, faces=None)
tm.engine = FakeEngine()
tm.resolver = FakeResolver()
frame = (1000, 1000, 3)

tm._presence_observe(_P("staff_04", "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
assert tm.resolver.seen == [("foot spa:7", "staff_04")], tm.resolver.seen
c = tm.engine.calls[-1]
assert c["key"] == "Phai" and c["therapist"] == "Phai", c
assert c["therapist_id"] == "t1" and c["confidence"] == 0.9, c
print("1) _presence_observe routes through the resolver  OK")

tm.resolver = None
tm._presence_observe(_P("staff_04", "staff"), 7, (10, 10, 50, 50), frame, {}, 100.0)
c = tm.engine.calls[-1]
assert c["key"] == "staff_04" and c["confidence"] == 1.0, c
print("2) no resolver -> Plan 1 fallback (key = face id)  OK")
print("all identity_wire tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python identity_wire_test.py`
Expected: FAIL — the assertion `tm.resolver.seen == [("foot spa:7", "staff_04")]`
fails (the current `_presence_observe` never calls a resolver), or an
`AttributeError` on `self.resolver`. Either way it does NOT pass.

- [ ] **Step 3: Add the `resolver` parameter to `TrackManager.__init__`**

Change the signature (the Plan 1 form):

```python
    def __init__(self, camera_id, cfg, logger, eyes, faces=None, enroller=None,
                 engine=None):
```

to:

```python
    def __init__(self, camera_id, cfg, logger, eyes, faces=None, enroller=None,
                 engine=None, resolver=None):
```

and right after the Plan 1 line `self.engine = engine`, add:

```python
        # identity resolver (Plan 2): None -> fall back to raw face id (Plan 1)
        self.resolver = resolver
```

- [ ] **Step 4: Route `_presence_observe` through the resolver**

Replace the body of `_presence_observe` (the Plan 1 version that computed
`name`/`key` from `p.voter.name`) with:

```python
    def _presence_observe(self, p, tid, box, frame_shape, customer_rooms, now):
        """Report one staff person's room to the shared presence engine, keyed
        by resolved identity. With a resolver: name/key/confidence come from it
        (correction > face > sticky > anon). Without one: Plan 1 fallback."""
        if self.engine is None or not p.announced or p.voter.role != "staff":
            return
        room = which_room(self.camera_id, box, frame_shape, self.cfg)
        door = which_threshold(self.camera_id, box, frame_shape, self.cfg)
        has_cust = bool(customer_rooms.get(room))
        if self.resolver is not None:
            res = self.resolver.resolve(now, f"{self.camera_id}:{tid}",
                                        face_id=p.voter.name)
            key, name = res["key"], res["name"]
            tid_pos, conf = res["therapist_id"], res["confidence"]
        else:
            name = p.voter.name
            key = name or f"{self.camera_id}:{tid}"
            tid_pos = staff_therapist_id(name)
            conf = 1.0 if name else 0.0
        self.engine.observe(now, key, self.camera_id, room, door, has_cust,
                            therapist=name, therapist_id=tid_pos, confidence=conf)
```

- [ ] **Step 5: Call `resolver.depart` on LEAVE**

In the LEAVE loop, right after `del self.people[tid]` (~line 566), add:

```python
                if self.resolver is not None:
                    self.resolver.depart(f"{self.camera_id}:{tid}")
```

(It sits inside the existing `if tid not in seen and now - p.last_seen > ...:`
block, at the same indentation as `del self.people[tid]`.)

- [ ] **Step 6: Run the wire test to verify it passes**

Run: `python identity_wire_test.py`
Expected: PASS — ends with `all identity_wire tests pass`

- [ ] **Step 7: Wire `main.py`**

Add to the imports (after `from presence_engine import PresenceEngine`):

```python
from identity_resolver import IdentityResolver
from booking_sync import RosterSync, make_bookings_fetch
from corrections_sync import CorrectionsSync, make_corrections_fetch
```

After the `engine = PresenceEngine(...)` line (Plan 1), add:

```python
    # identity resolver (Plan 2) + its two POS-fed syncers
    resolver = IdentityResolver(CONFIG)
    RosterSync(resolver, make_bookings_fetch(CONFIG), CONFIG).start()
    CorrectionsSync(resolver, make_corrections_fetch(CONFIG), CONFIG).start()
```

Change the `trackers` construction to pass `resolver` (Plan 1 passed `engine`):

```python
    trackers = {cid: TrackManager(cid, CONFIG, logger, eyes, faces,
                                  enrollers.get(cid), engine, resolver)
                for cid in cam_ids}
```

- [ ] **Step 8: Verify everything imports**

Run: `python -c "import main, tracker, identity_resolver, booking_sync, corrections_sync; print('import ok')"`
Expected: `import ok`

- [ ] **Step 9: Run the presence + identity regression tests**

Run: `python presence_wire_test.py` then `python room_event_test.py`
Expected: `all presence_wire tests pass` and `PASS: ROOM ENTER carries meta.room`

- [ ] **Step 10: Commit**

```bash
git add tracker.py main.py identity_wire_test.py
git commit -m "feat(identity): wire IdentityResolver + syncers into tracker + main"
```

---

### Task 5: Supervised face-teach tool at the anchor camera

**Files:**
- Create: `face_teach.py` (capture sink + non-interactive labeling CLI)
- Modify: `main.py` (construct `AnchorCapture` for the anchor camera; feed it in
  the loop)
- Test: `face_teach_test.py` (the pure enrollment-record logic)

**Interfaces:**
- Consumes: `prune_to_cap` from `face_enroller.py`; `CONFIG["face_teach"]`,
  `CONFIG["faces_dir"]`, `CONFIG["staff_registry"]`, `CONFIG["face_det_model"]`,
  `CONFIG["face_rec_model"]`, `CONFIG["rooms"]` (anchor flag).
- Produces:
  - `enroll_record(name, embeddings, faces_dir, registry_path, cap=30) -> int`
    (pure file/numpy)
  - `anchor_camera(cfg) -> str | None` (the camera of the `anchor:True` room)
  - `AnchorCapture(cfg, camera_id)` with `feed(now, frame, rows)`
  - CLI: `python face_teach.py list|assign <queue_subdir> <name>|drop <queue_subdir>`

- [ ] **Step 1: Write the failing test**

Create `face_teach_test.py`:

```python
# Unit test for the pure enrollment-record logic (no cv2 models). enroll_record
# must append embeddings to <name>.npz (capped) and ensure a registry entry.
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_teach import enroll_record, anchor_camera

d = tempfile.mkdtemp()
faces = os.path.join(d, "faces")
reg = os.path.join(d, "staff.json")

n = enroll_record("staff_09", [np.ones(128, np.float32),
                               np.zeros(128, np.float32)], faces, reg, cap=30)
assert n == 2, n
assert os.path.exists(os.path.join(faces, "staff_09.npz"))
feats = np.load(os.path.join(faces, "staff_09.npz"))["feats"]
assert feats.shape == (2, 128), feats.shape
print("1) enroll_record writes the npz with both embeddings  OK")

with open(reg, encoding="utf-8") as f:
    entry = json.load(f)["staff_09"]
assert entry == {"name": "", "therapist_id": "", "source": "face-teach"}, entry
print("2) enroll_record creates the registry stub  OK")

# appending more, with a cap, keeps exactly `cap` embeddings
many = [np.random.RandomState(i).rand(128).astype(np.float32) for i in range(40)]
n = enroll_record("staff_09", many, faces, reg, cap=30)
assert n == 30, n
print("3) enroll_record caps the stored embeddings  OK")

# anchor_camera reads the anchor:True room's camera from CONFIG["rooms"]
cfg = {"rooms": {"ห้องพัก": {"type": "rest", "via": "camera",
                            "camera": "office", "anchor": True},
                 "Foot Spa": {"type": "service", "via": "camera",
                              "camera": "foot spa"}}}
assert anchor_camera(cfg) == "office", anchor_camera(cfg)
assert anchor_camera({"rooms": {}}) is None
print("4) anchor_camera finds the anchor room's camera  OK")
print("all face_teach tests pass")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python face_teach_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'face_teach'`

- [ ] **Step 3: Create `face_teach.py`**

```python
# face_teach.py -- supervised "teach who is who" at the anchor (staff-room)
# camera. Replaces the disabled auto_enroll: a HUMAN labels faces, which is why
# it is reliable where CCTV faces are not separable on their own.
#
# Two parts:
#   1. AnchorCapture: during a run, save candidate face crops + embeddings from
#      the anchor camera into a review queue (queue_dir/<camera>_<tid>_<stamp>/),
#      one subdir per track. NO automatic enrollment.
#   2. CLI: review the queue and assign a subdir to a name -> enrolls it into the
#      registry so every camera recognizes that person.
#
# Usage:
#   python face_teach.py list
#   python face_teach.py assign <queue_subdir> <name|staff_id>
#   python face_teach.py drop <queue_subdir>
import json
import os
import sys
import time

import numpy as np

from face_enroller import prune_to_cap


def anchor_camera(cfg):
    """Camera of the room flagged anchor:True in CONFIG['rooms'], or None."""
    for spec in cfg.get("rooms", {}).values():
        if spec.get("anchor"):
            return spec.get("camera")
    return None


def enroll_record(name, embeddings, faces_dir, registry_path, cap=30):
    """Append `embeddings` (list of 1-D float32 arrays) to person `name`'s npz
    (capped + diversified) and ensure a registry stub exists. Returns the number
    of embeddings now stored. Pure file/numpy -- no models -> unit-testable."""
    os.makedirs(faces_dir, exist_ok=True)
    npz = os.path.join(faces_dir, f"{name}.npz")
    dim = embeddings[0].size
    existing = (np.load(npz)["feats"] if os.path.exists(npz)
                else np.empty((0, dim), np.float32))
    feats = np.vstack([existing] + [e.reshape(1, -1).astype(np.float32)
                                    for e in embeddings])
    feats = prune_to_cap(feats, cap)
    np.savez(npz, feats=feats)
    reg = {}
    if os.path.exists(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            reg = json.load(f)
    reg.setdefault(name, {"name": "", "therapist_id": "", "source": "face-teach"})
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    return len(feats)


class AnchorCapture:
    """Saves candidate face crops + embeddings from the anchor camera into a
    review queue. Mirrors AutoEnroller._embed for consistent crops. Gated by
    cfg['face_teach']['enabled']; does nothing on other cameras."""

    def __init__(self, cfg, camera_id):
        import cv2
        self.cfg = cfg
        self.camera_id = camera_id
        spec = cfg.get("face_teach", {})
        self.enabled = bool(spec.get("enabled")) and camera_id == anchor_camera(cfg)
        self.every = spec.get("capture_every", 1.5)
        self.qdir = spec.get("queue_dir", "face_queue")
        self.last = {}   # tid -> last capture time
        if self.enabled:
            os.makedirs(self.qdir, exist_ok=True)
            self.det = cv2.FaceDetectorYN.create(cfg["face_det_model"], "",
                                                 (320, 320), cfg["enroll_min_score"])
            self.rec = cv2.FaceRecognizerSF.create(cfg["face_rec_model"], "")

    def _embed(self, frame, box):
        import cv2
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        crop = frame[y1:int(box[3]), x1:int(box[2])]
        if crop.size == 0:
            return None, None
        big = cv2.resize(crop, None, fx=2, fy=2)
        self.det.setInputSize((big.shape[1], big.shape[0]))
        ok, faces = self.det.detect(big)
        if faces is None or len(faces) == 0:
            return None, None
        if sum(1 for f in faces if f[14] >= 0.6) > 1:
            return None, None   # two faces -> ambiguous, skip
        face = max(faces, key=lambda f: f[14])
        if (face[14] < self.cfg["enroll_min_score"]
                or min(face[2], face[3]) < self.cfg["enroll_min_face"]):
            return None, None
        aligned = self.rec.alignCrop(big, face)
        return self.rec.feature(aligned), aligned

    def feed(self, now, frame, rows):
        """rows: [(track_id, box), ...] currently in the anchor room."""
        if not self.enabled:
            return
        import cv2
        for tid, box in rows:
            if now - self.last.get(tid, 0) < self.every:
                continue
            self.last[tid] = now
            emb, aligned = self._embed(frame, box)
            if emb is None:
                continue
            sub = os.path.join(self.qdir, f"{self.camera_id.replace(' ', '_')}_{tid}")
            os.makedirs(sub, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            np.save(os.path.join(sub, f"{stamp}.npy"), emb.flatten())
            cv2.imwrite(os.path.join(sub, f"{stamp}.jpg"), aligned)


def _cli(argv):
    from config import CONFIG
    qdir = CONFIG.get("face_teach", {}).get("queue_dir", "face_queue")
    faces_dir, reg = CONFIG["faces_dir"], CONFIG["staff_registry"]
    cap = CONFIG.get("face_teach", {}).get("samples_cap", 30)
    cmd = argv[0] if argv else "list"

    if cmd == "list":
        if not os.path.isdir(qdir):
            print("(queue empty)")
            return
        for sub in sorted(os.listdir(qdir)):
            p = os.path.join(qdir, sub)
            n = len([f for f in os.listdir(p) if f.endswith(".npy")])
            print(f"{sub}\t{n} faces")
        return

    if cmd == "assign" and len(argv) >= 3:
        sub, name = argv[1], argv[2]
        p = os.path.join(qdir, sub)
        embs = [np.load(os.path.join(p, f)) for f in os.listdir(p)
                if f.endswith(".npy")]
        if not embs:
            print(f"no embeddings in {sub}")
            return
        total = enroll_record(name, embs, faces_dir, reg, cap)
        for f in os.listdir(p):
            os.remove(os.path.join(p, f))
        os.rmdir(p)
        print(f"enrolled {len(embs)} face(s) as {name} (now {total} stored). "
              f"Set a display name + therapist_id in {reg}.")
        return

    if cmd == "drop" and len(argv) >= 2:
        p = os.path.join(qdir, argv[1])
        for f in os.listdir(p):
            os.remove(os.path.join(p, f))
        os.rmdir(p)
        print(f"dropped {argv[1]}")
        return

    print("usage: python face_teach.py list | assign <subdir> <name> | drop <subdir>")


if __name__ == "__main__":
    _cli(sys.argv[1:])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python face_teach_test.py`
Expected: PASS — ends with `all face_teach tests pass`

- [ ] **Step 5: Verify the CLI runs (empty queue)**

Run: `python face_teach.py list`
Expected: `(queue empty)` (no queue dir yet) — confirms the CLI imports
`config` and runs.

- [ ] **Step 6: Wire `AnchorCapture` into `main.py`**

Add to imports:

```python
from face_teach import AnchorCapture, anchor_camera
```

After the `trackers = {...}` construction, add:

```python
    # supervised face capture on the anchor (staff-room) camera only
    anchor_cap = {cid: AnchorCapture(CONFIG, cid) for cid in cam_ids
                  if cid == anchor_camera(CONFIG)}
```

In the analysis branch, right after
`detections, phones = detectors[cid].detect(frame)` (Plan 1 / existing line),
add:

```python
                if cid in anchor_cap:
                    anchor_cap[cid].feed(now, frame,
                                         [(d["track_id"], d["box"]) for d in detections])
```

- [ ] **Step 7: Verify imports still work**

Run: `python -c "import main, face_teach; print('import ok')"`
Expected: `import ok`

- [ ] **Step 8: Manual live-verify (documented; not a unit test)**

This step needs the real anchor camera and is verified by a human, since it
involves cv2 face models + live frames:
1. In `config.py`, set `CONFIG["face_teach"]["enabled"] = True` and confirm a
   room has `anchor: True` with the correct `camera`.
2. Run `python main.py`; have a staff member stand in the anchor room.
3. Confirm crops + `.npy` files appear under `face_queue/<camera>_<tid>/`.
4. `python face_teach.py list` → shows the subdir; `python face_teach.py assign
   <subdir> staff_01` → enrolls; confirm `faces/staff_01.npz` exists and the
   FaceMatcher picks it up on next launch (or live if sharing the matcher).
5. Set the display name + `therapist_id` for that id in `staff.json`.
Record the outcome in the commit message or a follow-up note.

- [ ] **Step 9: Commit**

```bash
git add face_teach.py face_teach_test.py main.py
git commit -m "feat(identity): supervised face-teach capture + CLI at anchor cam"
```

---

## Self-Review (plan vs spec/design §5)

- **Tier 1 reception correction** → Task 1 (`apply_correction`, tier 1 in
  `resolve`) + Task 2 (`CorrectionsSync` pulls them from POS). ✓
- **Tier 2 anchor face-tag** → Task 1 (face tier, roster-filtered) + Task 5
  (anchor capture + teach tool builds the registry the face match uses). ✓
- **Tier 3 booking prior** → Task 3 (`RosterSync`), implemented as a roster
  filter because `cctvBookings` has no room (limitation documented). ✓
- **Tier 4 track hand-off (cross-camera)** → DEFERRED (see below). Within a
  camera, stickiness+decay (Task 1) already carries a name across faceless
  frames. ✓ (partial, by design)
- **Tier 5 anonymous** → Task 1 (anonymous result, stable per-track key). ✓
- **roster narrows candidates** → Task 1 (`set_roster`) + Task 3. ✓
- **confidence decay / "show unknown over a wrong name"** → Task 1 decay + floor. ✓
- **supervised teach replaces auto_enroll** → Task 5. ✓

**Placeholder scan:** none — every step has real code/commands. Task 5 Step 8 is
a documented MANUAL verification (cv2 + live camera), not a placeholder; the
testable enrollment logic has a real unit test in Step 1.

**Type consistency:** `resolve(...)` returns `{key,name,therapist_id,confidence,
source}` used identically in Task 4's `_presence_observe`; `apply_correction(uid,
name)` matches between Task 1, Task 2, and the `FakeResolver`s; `set_roster`
matches Task 1 / Task 3; `enroll_record(name, embeddings, faces_dir,
registry_path, cap)` matches Task 5's CLI call and test; `TrackManager(...,
engine, resolver)` ordering matches main.py's construction.

---

## Deferred / out of scope (honest)

- **Cross-camera hand-off of NAMED people (design §5 tier 4, full form).**
  Following a specific therapist from camera A to camera B when the face stops
  matching needs camera-adjacency geometry + a departed-identity pool with
  spatial/temporal matching. It is genuinely error-prone with identical
  uniforms; left for a focused follow-up once the resolver + teach loop are in
  use and we can measure how often it's needed. Today, a named person re-appears
  named on the next camera ONLY when the face re-matches there (then sticky
  carries it); otherwise they show anonymous on that camera.
- **POS write side of corrections** (`corrections/` collection + the avatar-tap
  UI) — that is Plan 3 (maisonPOS, Nuxt + Pinia). This plan only CONSUMES it.
- **Room-level booking matching** — needs a `room` field added to
  `cctvBookings` (contract change); until then the roster is shift-only.
