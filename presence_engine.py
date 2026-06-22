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
