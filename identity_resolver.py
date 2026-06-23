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
