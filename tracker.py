# Per-camera person registry: the orchestration heart of v3.
#
# For every tracked person: vote on the role (uniform color), then route the
# analysis by role --
#   STAFF    -> sleep state machine (posture + stillness + eyes) and the
#               phone-use dwell timer
#   customer -> posture-imbalance estimate while standing (massage
#               conversation starter)
#   "?"      -> box + label only; guessing wrong in either direction is worse,
#               and "?" normally only lasts the first seconds of a track
# Track ids stay private to this module; events and overlays use the label.
import math
import statistics
import time
from collections import deque

from person_labeler import RoleVoter, display_label, staff_name, staff_therapist_id
from posture import (L_WRIST, R_WRIST, classify_posture, describe_causes,
                     face_hidden, imbalance_metrics, kpt)
from sleep_analyzer import SleepAnalyzer


def _clock(ts):
    """Epoch seconds -> HH:MM:SS for timeline descriptions."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"


def _point_in_poly(x, y, poly):
    """Ray-casting test: is the (x, y) fraction inside the polygon [(x,y),...]?"""
    n, inside, j = len(poly), False, len(poly) - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0


class _Tracked:
    def __init__(self, cfg, eyes, faces, box, now):
        self.box = box
        self.first_seen = now
        self.last_seen = now
        self.announced = False      # ENTER already logged?
        self.voter = RoleVoter(cfg, faces)
        self.analyzer = SleepAnalyzer(cfg, eyes)
        self.imb_hist = deque()     # (time, metrics) while standing
        self.imb_short = ""         # short on-screen imbalance note
        self.phone_since = None
        self.phone_seen = None      # last time a phone sat near this person
        self.phone_announced = False  # PHONE USE alert fired this episode?
        self.sleep_started = None   # when the current sleep episode began
        self.posture = "unknown"
        self.state = "active"
        self.held = 0.0
        self.last_alert = {}        # event name -> last emit time
        self.room = None            # last room zone entered (occupancy cams)


class TrackManager:
    def __init__(self, camera_id, cfg, logger, eyes, faces=None, enroller=None):
        self.camera_id = camera_id
        self.cfg = cfg
        self.logger = logger
        self.eyes = eyes
        self.faces = faces
        self.enroller = enroller
        self.watch_only = camera_id in cfg.get("watch_only", [])
        self.presence = camera_id in cfg.get("presence_cameras", [])
        # no-penalty cameras: still track + label staff/customer (ENTER/LEAVE),
        # but run NO penalty analysis (sleep/phone/posture). For treatment rooms
        # where staff legitimately handle tools all shift -> penalties = noise.
        self.no_penalty = camera_id in cfg.get("no_penalty_cameras", [])
        # selectively switch OFF individual staff penalties on a camera (e.g.
        # foot spa: no "sleep"/"phone", but floor-object alerts still run).
        self.disabled = set(cfg.get("disable_penalties", {}).get(camera_id, []))
        # room occupancy: doorway zones in one camera (e.g. spa room corridor
        # with 4 rooms) -> log who enters which room. [{"name","zone"(x1y1x2y2)}]
        self.room_zones = cfg.get("room_zones", {}).get(camera_id, [])
        # no-phone cameras: keep every other check (sleep, floor objects...) but
        # never raise PHONE USE -- treatment rooms where staff hold tools/bottles
        # all shift make phone detection almost all false positives.
        self.no_phone = camera_id in cfg.get("no_phone_cameras", [])
        self.ignore_zones = cfg.get("ignore_zones", {}).get(camera_id, [])
        # fixed staff positions (e.g. the reception desk seat): a person whose
        # box center sits here is staff regardless of uniform/face -- rescues
        # seated staff the uniform check keeps missing. (x1,y1,x2,y2) fractions.
        self.staff_zones = cfg.get("staff_zones", {}).get(camera_id, [])
        # rest zone (the staff break room): force staff AND skip penalty
        # analysis -- resting (phone/nap) on break is not a violation. Value is
        # either a list of (x1,y1,x2,y2) rects (rest INSIDE them), or a dict
        # {"poly": [(x,y)...], "mode": "inside"|"outside"} for a polygon -- e.g.
        # reception, where the whole staff room is everything OUTSIDE the glass
        # lounge polygon.
        self.rest_zone = cfg.get("rest_zones", {}).get(camera_id)
        self.people = {}  # internal track id -> _Tracked
        # greeting rule state (active on greeting_cameras only)
        self.greet_enabled = camera_id in cfg.get("greeting_cameras", [])
        self.greet_watch = None   # (customer _Tracked, deadline) while checking
        self.greet_last = float("-inf")  # when the last check resolved (cooldown)

    # --- helpers ------------------------------------------------------------
    def _cooldown_ok(self, p, name, now):
        if now - p.last_alert.get(name, 0) >= self.cfg["re_alert_secs"]:
            p.last_alert[name] = now
            return True
        return False

    def _flagged_imbalance(self, p, now, metrics):
        """v2's persistent-median logic: {metric: signed median degrees}."""
        if metrics:
            p.imb_hist.append((now, metrics))
        while p.imb_hist and now - p.imb_hist[0][0] > self.cfg["imb_window"]:
            p.imb_hist.popleft()
        if len(p.imb_hist) < self.cfg["imb_min_samples"]:
            return {}
        flagged = {}
        for key, limit in (("shoulder", self.cfg["shoulder_tilt_deg"]),
                           ("hip", self.cfg["hip_tilt_deg"]),
                           ("head", self.cfg["head_tilt_deg"]),
                           ("lean", self.cfg["lean_deg"])):
            vals = [m[key] for _, m in p.imb_hist if m.get(key) is not None]
            if len(vals) >= self.cfg["imb_min_samples"]:
                med = statistics.median(vals)
                if abs(med) > limit:
                    flagged[key] = med
        return flagged

    def _analyze_staff(self, p, now, frame, box, pts, kconf, head_down, buried,
                       holds_phone):
        prev = p.state
        p.state, p.held, why = p.analyzer.update(
            now, frame, box, pts, kconf, p.posture, head_down, buried)
        label = display_label(p.voter.role, p.voter.name)
        tid = staff_therapist_id(p.voter.name)
        sleep_on = "sleep" not in self.disabled

        if sleep_on and p.state == "sleeping":
            # every NEW sleep episode hits the timeline immediately -- waking
            # up and dozing off again is never suppressed, no matter how soon
            # it repeats. The cooldown only spaces out repeat alerts within
            # ONE continuous sleep.
            if prev != "sleeping":
                p.sleep_started = now - p.held  # evidence began back then
                p.last_alert["SLEEPING"] = now
                emit = True
            else:
                emit = self._cooldown_ok(p, "SLEEPING", now)
            if emit:
                img = (self.logger.save_evidence(frame, box, self.camera_id, label,
                                                 "SLEEPING", duration=p.held,
                                                 started=p.sleep_started)
                       if prev != "sleeping" else None)
                self.logger.log(self.camera_id, label, "SLEEPING",
                                f"{why}, started {_clock(p.sleep_started)} "
                                f"({int(p.held)}s so far)", "alert",
                                therapist_id=tid, image_path=img, duration=p.held)
        elif sleep_on and prev == "sleeping":
            # episode over: log how long it lasted in total
            dur = now - (p.sleep_started or now)
            self.logger.log(self.camera_id, label, "SLEEPING END",
                            f"awake again, slept ~{int(dur)}s total "
                            f"({_clock(p.sleep_started)} - {_clock(now)})", "normal",
                            therapist_id=tid, duration=dur)
            p.sleep_started = None
        elif sleep_on and p.state == "drowsy" and prev == "active":
            if self._cooldown_ok(p, "DROWSY", now):
                self.logger.log(self.camera_id, label, "DROWSY",
                                f"{why}, started {_clock(now - p.held)} "
                                f"({int(p.held)}s so far)", "warning",
                                therapist_id=tid, duration=p.held)

        # phone: timer runs only while THIS person is the one holding a
        # phone (nearest-wrist ownership, resolved in _phone_holders --
        # working right next to a customer's phone no longer counts)
        if "phone" not in self.disabled:
            if holds_phone:
                p.phone_seen = now
            # detection flickers frame to frame; the timer survives short gaps
            near = p.phone_seen is not None and now - p.phone_seen <= self.cfg["phone_grace"]
            if near:
                p.phone_since = p.phone_since or now
            else:
                self._end_phone(p, label)
            if (p.phone_since and now - p.phone_since >= self.cfg["phone_secs"]
                    and self._cooldown_ok(p, "PHONE USE", now)):
                first = not p.phone_announced
                img = (self.logger.save_evidence(frame, box, self.camera_id, label,
                                                 "PHONE USE", duration=now - p.phone_since,
                                                 started=p.phone_since)
                       if first else None)
                self.logger.log(self.camera_id, label, "PHONE USE",
                                f"phone in hand, started {_clock(p.phone_since)} "
                                f"({int(now - p.phone_since)}s so far)", "alert",
                                therapist_id=tid, image_path=img,
                                duration=now - p.phone_since)
                p.phone_announced = True

    def _end_phone(self, p, label):
        """Close an announced phone episode with its total duration. Duration
        runs to the LAST actual phone sighting, not to the end of the grace."""
        if p.phone_announced and p.phone_since:
            dur = (p.phone_seen or p.phone_since) - p.phone_since
            self.logger.log(self.camera_id, label, "PHONE USE END",
                            f"phone put away, used ~{int(dur)}s total "
                            f"({_clock(p.phone_since)} - {_clock(p.phone_seen)})", "normal",
                            therapist_id=staff_therapist_id(p.voter.name), duration=dur)
        p.phone_since, p.phone_announced = None, False

    def _analyze_customer(self, p, now, frame, box, pts, kconf):
        p.state, p.held = "active", 0.0
        metrics = imbalance_metrics(pts, kconf) if p.posture == "standing" else None
        flagged = self._flagged_imbalance(p, now, metrics)
        if flagged:
            p.imb_short, full = describe_causes(flagged)
            if self._cooldown_ok(p, "POSTURE NOTE", now):
                img = self.logger.save_evidence(frame, box, self.camera_id,
                                                "customer", "POSTURE NOTE")
                self.logger.log(self.camera_id, display_label(p.voter.role),
                                "POSTURE NOTE", full, "normal", image_path=img)

    def _log_recognition(self, p, prev_role, role):
        """Timeline correction when face recognition identifies someone AFTER
        their ENTER was logged with another label: a staff member who walked
        back in gets labeled customer (dark shirt) or '?' for a while until
        their face is seen -- this event tells the reader those earlier
        entries belong to a staff member."""
        if (p.announced and role == "staff" and prev_role != "staff"
                and p.voter.name):
            was = display_label(prev_role)
            self.logger.log(self.camera_id, display_label("staff", p.voter.name),
                            "STAFF RECOGNIZED",
                            f"the person in frame since {_clock(p.first_seen)} "
                            f"is staff ({staff_name(p.voter.name)}) -- earlier "
                            f"'{was}' entries in this period were this staff member",
                            "normal", therapist_id=staff_therapist_id(p.voter.name))

    @staticmethod
    def _center_in(box, frame_shape, zones):
        """True when the box CENTER (fraction of frame) sits in any zone."""
        h, w = frame_shape[:2]
        cx = (box[0] + box[2]) / 2 / w
        cy = (box[1] + box[3]) / 2 / h
        return any(zx1 < cx < zx2 and zy1 < cy < zy2
                   for zx1, zy1, zx2, zy2 in zones)

    def _in_service_zone(self, box, frame_shape):
        """True when the box center sits inside a customer-service zone."""
        return self._center_in(box, frame_shape,
                               self.cfg.get("service_zones", {}).get(self.camera_id, []))

    def _in_staff_zone(self, box, frame_shape):
        """True when the box center sits inside a fixed staff position."""
        return bool(self.staff_zones) and self._center_in(
            box, frame_shape, self.staff_zones)

    def _which_room(self, box, frame_shape):
        """Name of the room-zone the box center sits in, or None (corridor)."""
        if not self.room_zones:
            return None
        h, w = frame_shape[:2]
        cx = (box[0] + box[2]) / 2 / w
        cy = (box[1] + box[3]) / 2 / h
        for r in self.room_zones:
            x1, y1, x2, y2 = r["zone"]
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return r["name"]
        return None

    def _in_rest_zone(self, box, frame_shape):
        """True when the box center is in the staff rest zone (break room):
        staff, but resting -- no penalty analysis. Supports rect lists and a
        polygon with inside/outside mode."""
        rz = self.rest_zone
        if not rz:
            return False
        if isinstance(rz, dict):
            h, w = frame_shape[:2]
            cx = (box[0] + box[2]) / 2 / w
            cy = (box[1] + box[3]) / 2 / h
            inside = _point_in_poly(cx, cy, rz["poly"])
            return (not inside) if rz.get("mode") == "outside" else inside
        return self._center_in(box, frame_shape, rz)

    def _drop_ignored(self, detections, frame_shape):
        """Remove detections whose center is in an ignore zone (edge workers
        who flicker in/out and would spam ENTER/LEAVE)."""
        if not self.ignore_zones:
            return detections
        return [d for d in detections
                if not self._center_in(d["box"], frame_shape, self.ignore_zones)]

    def _phone_holders(self, phones, rows, frame_shape):
        """Track ids that are HOLDING a phone right now. Each phone belongs
        to at most one person: the one whose wrist is nearest, and only if
        that wrist is within phone_wrist_frac of their box height. Phones
        inside a service zone (lying customers the detector can't see)
        never count."""
        holders = set()
        for px1, py1, px2, py2 in phones:
            cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
            if self._in_service_zone([px1, py1, px2, py2], frame_shape):
                continue  # a customer's phone in the service area
            best_tid, best_d, best_r = None, float("inf"), 0.0
            for tid, box, p, pose in rows:
                if pose is None:
                    continue
                pts, kconf = pose["pts"], pose["kconf"]
                for j in (L_WRIST, R_WRIST):
                    w = kpt(pts, kconf, j)
                    if not w:
                        continue
                    d = math.hypot(cx - w[0], cy - w[1])
                    if d < best_d:
                        best_d = d
                        best_tid = tid
                        best_r = max(25.0, (box[3] - box[1])
                                     * self.cfg["phone_wrist_frac"])
            if best_tid is not None and best_d <= best_r:
                holders.add(best_tid)
        return holders

    def _update_presence(self, now, frame, detections):
        """Staff-only room: log everyone as STAFF on enter/leave (by name once
        their face is recognized), no penalty analysis. Faces are also
        captured here for auto-enrollment used in the other rooms."""
        # run face capture/recognition FIRST so identities are fresh below
        if self.enroller is not None:
            self.enroller.feed(now, frame, [(d["track_id"], d["box"])
                                            for d in detections])

        def who(tid):
            face_id = self.enroller.identity(tid) if self.enroller else None
            return display_label("staff", face_id), staff_therapist_id(face_id)

        seen = set()
        out = []
        for det in detections:
            tid, box = det["track_id"], det["box"]
            seen.add(tid)
            p = self.people.get(tid)
            if p is None:
                p = self.people[tid] = _Tracked(self.cfg, self.eyes, self.faces,
                                                box, now)
            p.box, p.last_seen = box, now
            label, tid_pos = who(tid)
            if not p.announced and now - p.first_seen >= self.cfg["min_visible"]:
                p.announced = True
                self.logger.log(self.camera_id, label, "ENTER",
                                "staff enters room", "normal", therapist_id=tid_pos)
            out.append({"box": box, "label": label, "role": "staff",
                        "state": "active", "tag": label, "line2": ""})
        for tid in list(self.people):
            p = self.people[tid]
            if tid not in seen and now - p.last_seen > self.cfg["track_grace"]:
                if p.announced:
                    label, tid_pos = who(tid)
                    self.logger.log(self.camera_id, label, "LEAVE",
                                    "staff leaves room", "normal", therapist_id=tid_pos)
                if self.enroller is not None:
                    self.enroller.forget(tid)
                del self.people[tid]
        return out

    # --- main entry -----------------------------------------------------------
    def update(self, now, frame, detections, poses, phones):
        """Feed one analysis pass. Returns display dicts for the overlay:
        [{"box", "label", "role", "state", "tag", "line2"}]."""
        # drop edge-of-frame flicker first, for every camera mode
        detections = self._drop_ignored(detections, frame.shape)
        if self.watch_only:
            # public-space view: boxes only, no tracking state, no analysis,
            # no events, no images
            return [{"box": d["box"], "label": "person", "role": None,
                     "state": "active", "tag": "person", "line2": ""}
                    for d in detections]

        if self.presence:
            return self._update_presence(now, frame, detections)

        seen = set()
        out = []
        # pre-pass: resolve pose matches, then decide who HOLDS each phone --
        # the person whose wrist is nearest wins. A staff member massaging a
        # customer who is scrolling their own phone works inches from it, but
        # the customer's wrist is touching it, so the customer wins.
        rows = []
        for det in detections:
            tid, box = det["track_id"], det["box"]
            seen.add(tid)
            p = self.people.get(tid)
            if p is None:
                p = self.people[tid] = _Tracked(self.cfg, self.eyes, self.faces,
                                                box, now)
            p.box, p.last_seen = box, now
            pose = max(poses, key=lambda q: iou(q["box"], box), default=None)
            if pose is not None and iou(pose["box"], box) <= 0.3:
                pose = None
            rows.append((tid, box, p, pose))
        holders = self._phone_holders(phones, rows, frame.shape)

        for tid, box, p, pose in rows:
            # fixed staff position (reception desk seat) or rest zone (break
            # room): force staff regardless of uniform/face. 'staff' is
            # terminal, so this sticks. resting staff also skip penalties below.
            resting = self._in_rest_zone(box, frame.shape)
            if p.voter.role != "staff" and (resting or self._in_staff_zone(box, frame.shape)):
                p.voter.role = "staff"
            # entry is announced only after the track survives min_visible
            # AND the person has been classified -- detection flickers and
            # far-away unclassifiable blobs never reach the timeline
            if (not p.announced and now - p.first_seen >= self.cfg["min_visible"]
                    and p.voter.role is not None):
                p.announced = True
                late = now - p.first_seen
                desc = ("person enters frame" if late < 5 else
                        f"person enters frame (first seen {_clock(p.first_seen)})")
                self.logger.log(self.camera_id,
                                display_label(p.voter.role, p.voter.name),
                                "ENTER", desc, "normal",
                                therapist_id=staff_therapist_id(p.voter.name))
                # a new customer starts the greeting countdown (one at a
                # time, cooldown keeps re-tracked customers from repeating
                # it). A "new" track INSIDE a service zone is an existing
                # customer being serviced whose track flickered -- not an
                # arrival, so it never starts a check.
                if (self.greet_enabled and p.voter.role == "customer"
                        and self.greet_watch is None
                        and now - self.greet_last >= self.cfg["greeting_cooldown"]
                        and not self._in_service_zone(box, frame.shape)):
                    self.greet_watch = (p, now + self.cfg["greeting_secs"])

            if pose is not None:
                pts, kconf = pose["pts"], pose["kconf"]
                prev_role = p.voter.role
                role = p.voter.update(now, frame, pts, kconf)
                self._log_recognition(p, prev_role, role)
                p.posture, head_down = classify_posture(pts, kconf, box)
                # face fully buried in arms (napping on a table) hides ALL
                # head keypoints; a person merely facing away does too, which
                # is why standing posture cancels it
                buried = face_hidden(kconf) and p.posture != "standing"

                if resting or self.no_penalty:
                    p.state = "active"    # break room / no-penalty cam: no alerts
                elif role == "staff":
                    self._analyze_staff(p, now, frame, box, pts, kconf,
                                        head_down, buried,
                                        (tid in holders) and not self.no_phone)
                elif role == "customer":
                    self._analyze_customer(p, now, frame, box, pts, kconf)
                else:
                    p.state = "active"

            # room occupancy (e.g. the spa corridor with 4 rooms): log who
            # enters which room when their center crosses into a doorway zone.
            # p.room holds the LAST room so corridor<->doorway jitter doesn't
            # re-fire; only a DIFFERENT room emits again.
            if self.room_zones and p.announced:
                room = self._which_room(box, frame.shape)
                if room and room != p.room:
                    self.logger.log(self.camera_id,
                                    display_label(p.voter.role, p.voter.name),
                                    "ROOM ENTER", f"entered {room}", "normal",
                                    therapist_id=staff_therapist_id(p.voter.name),
                                    room=room)
                    p.room = room

            # --- display strings ------------------------------------------
            label = display_label(p.voter.role, p.voter.name)
            bits = []
            if p.phone_since:
                bits.append(f"phone {int(now - p.phone_since)}s")
            if p.state != "active":
                bits.append(f"{p.state} {int(p.held)}s")
            tag = label + " | " + (", ".join(bits) if bits else "ok")
            line2 = p.posture
            if p.voter.role == "customer" and p.imb_short:
                line2 += " | " + p.imb_short
            out.append({"box": box, "label": label, "role": p.voter.role,
                        "state": p.state, "tag": tag, "line2": line2})

        # greeting rule: a staff member standing (or already on their feet)
        # any time before the deadline satisfies the check; the deadline
        # passing without one is a penalty
        if self.greet_watch is not None:
            cust, deadline = self.greet_watch
            staff_standing = any(
                q.voter.role == "staff" and q.posture == "standing"
                and now - q.last_seen < 3 for q in self.people.values())
            if staff_standing:
                self.greet_watch, self.greet_last = None, now
            elif now >= deadline:
                started = deadline - self.cfg["greeting_secs"]
                img = self.logger.save_evidence(frame, cust.box, self.camera_id,
                                                "STAFF", "GREETING MISSED",
                                                duration=self.cfg["greeting_secs"],
                                                started=started)
                self.logger.log(self.camera_id, "STAFF", "GREETING MISSED",
                                f"customer arrived {_clock(started)}, no staff "
                                f"stood up within {int(self.cfg['greeting_secs'])}s",
                                "alert", image_path=img,
                                duration=self.cfg["greeting_secs"])
                self.greet_watch, self.greet_last = None, now

        # leave events: a track unseen for track_grace seconds is gone
        # (grace is long so occluded nappers keep their sleep timers)
        for tid in list(self.people):
            p = self.people[tid]
            if tid not in seen and now - p.last_seen > self.cfg["track_grace"]:
                label = display_label(p.voter.role, p.voter.name)
                p_tid = staff_therapist_id(p.voter.name)
                # close any episode still open when the person disappears
                if p.state == "sleeping" and p.sleep_started:
                    dur = p.last_seen - p.sleep_started
                    self.logger.log(self.camera_id, label, "SLEEPING END",
                                    f"left frame, slept ~{int(dur)}s total "
                                    f"({_clock(p.sleep_started)} - {_clock(p.last_seen)})",
                                    "normal", therapist_id=p_tid, duration=dur)
                self._end_phone(p, label)
                if p.announced:
                    self.logger.log(self.camera_id, label,
                                    "LEAVE", "person leaves frame", "normal",
                                    therapist_id=p_tid)
                del self.people[tid]
        return out
