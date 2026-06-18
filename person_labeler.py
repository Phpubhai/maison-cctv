# Person labeling -- deliberately isolated.
#
# This module is the ONLY place the rest of the system learns who somebody
# is. Two signals, both local to this file:
#   1. staff uniform color (beige top + pants, ported from behavior_monitor_v2)
#   2. enrolled staff faces (YuNet + SFace; enroll with enroll_face.py) --
#      a face match marks the person STAFF even out of uniform and overrides
#      an earlier uniform-based "customer" decision
# Tracking, sleep analysis and logging all consume the returned label as an
# opaque string, so changing how identity works never touches them.
#
# Labels: "staff", "customer", or None (undecided -> shown as "?";
# undecided people get NO alerts of either kind).
import json
import os
from collections import deque

import cv2
import numpy as np

from posture import (L_EAR, L_EYE, L_HIP, L_KNEE, L_SHOULDER, NOSE, R_EAR,
                     R_EYE, R_HIP, R_KNEE, R_SHOULDER, kpt, midpoint)

# --- staff registry: face id -> {"name", "therapist_id"} -------------------
# Loaded from staff.json (hand-editable). Maps enrolled face ids to the real
# person: display name + the POS therapistId for cross-system joins.
_registry = {}


def load_registry(cfg):
    global _registry
    path = cfg.get("staff_registry", "")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _registry = json.load(f)
        named = sum(1 for e in _registry.values() if e.get("name"))
        print(f"staff registry: {len(_registry)} entries, {named} with names", flush=True)


def staff_name(face_id):
    """Real name for an enrolled face id, falling back to the id itself."""
    if not face_id:
        return None
    return (_registry.get(face_id) or {}).get("name") or face_id


def staff_therapist_id(face_id):
    """POS therapistId for an enrolled face id, or None."""
    if not face_id:
        return None
    return (_registry.get(face_id) or {}).get("therapist_id") or None


def _hsv_match_frac(frame_hsv, ranges):
    """Fraction of pixels in a pre-cropped HSV image that fall in the ranges."""
    mask = None
    for lo, hi in ranges:
        m = cv2.inRange(frame_hsv, np.array(lo), np.array(hi))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return float(np.count_nonzero(mask)) / mask.size


def _crop_hsv(frame, x1, y1, x2, y2):
    """HSV crop of the rect, or None when degenerate/too small to judge."""
    xa, xb = int(max(0, min(x1, x2))), int(min(frame.shape[1], max(x1, x2)))
    ya, yb = int(max(0, min(y1, y2))), int(min(frame.shape[0], max(y1, y2)))
    if xb - xa < 3 or yb - ya < 3:
        return None
    return cv2.cvtColor(frame[ya:yb, xa:xb], cv2.COLOR_BGR2HSV)


def uniform_verdict(cfg, frame, pts, kconf):
    """One-frame staff/customer vote from clothing color.
    True = matches ANY named uniform set, False = clearly none of them,
    None = can't tell. Sets are evaluated SEPARATELY so two sets' colors
    can't combine to fake a pass. Torso AND upper legs must both match;
    when hips/knees are hidden behind the desk (the receptionist's normal
    pose) a stricter chest-only check applies."""
    sh_l, sh_r = kpt(pts, kconf, L_SHOULDER), kpt(pts, kconf, R_SHOULDER)
    hip_l, hip_r = kpt(pts, kconf, L_HIP), kpt(pts, kconf, R_HIP)
    knee = midpoint(kpt(pts, kconf, L_KNEE), kpt(pts, kconf, R_KNEE))
    if not (sh_l and sh_r):
        return None
    sh_m = midpoint(sh_l, sh_r)
    sw = abs(sh_r[0] - sh_l[0])
    # narrow strips down the body's center axis, well inside the silhouette,
    # so the beige sofa/walls around the person never leak into the sample
    half_t = max(3, sw * 0.25)

    if hip_l and hip_r and knee:
        hip_m = midpoint(hip_l, hip_r)
        torso = _crop_hsv(frame, sh_m[0] - half_t, sh_m[1],
                          sh_m[0] + half_t, hip_m[1])
        half_l = max(3, abs(hip_r[0] - hip_l[0]) * 0.30)
        legs = _crop_hsv(frame, hip_m[0] - half_l, hip_m[1],
                         hip_m[0] + half_l, knee[1])
        if torso is None or legs is None:
            return None
        borderline = False
        for ranges in cfg["uniform_sets"].values():
            t, l = _hsv_match_frac(torso, ranges), _hsv_match_frac(legs, ranges)
            if t >= cfg["uniform_match_frac"] and l >= cfg["uniform_match_frac"]:
                return True
            if min(t, l) >= cfg["uniform_reject_frac"]:
                borderline = True  # not a clear no for this set
        return None if borderline else False

    chest = _crop_hsv(frame, sh_m[0] - half_t, sh_m[1],
                      sh_m[0] + half_t, sh_m[1] + 1.2 * sw)
    if chest is None:
        return None
    borderline = False
    for ranges in cfg["uniform_sets"].values():
        c = _hsv_match_frac(chest, ranges)
        if c >= cfg["chest_match_frac"]:
            return True
        if c >= cfg["chest_reject_frac"]:
            borderline = True
    return None if borderline else False


class FaceMatcher:
    """Matches head crops against enrolled staff faces. One shared instance.
    Enroll faces with enroll_face.py; each spa_monitor/faces/<name>.npz holds
    several embeddings of one person."""

    def __init__(self, cfg):
        self.cfg = cfg
        load_registry(cfg)
        self.known = []  # (name, feats ndarray)
        d = cfg["faces_dir"]
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".npz"):
                    self.known.append((f[:-4], np.load(os.path.join(d, f))["feats"]))
        self.det = self.rec = None
        if self.known:
            self.det = cv2.FaceDetectorYN.create(cfg["face_det_model"], "", (320, 320), 0.7)
            self.rec = cv2.FaceRecognizerSF.create(cfg["face_rec_model"], "")
            print(f"face matcher: {len(self.known)} enrolled staff "
                  f"({', '.join(n for n, _ in self.known)})", flush=True)
        else:
            print("no enrolled faces -> staff detection by uniform only", flush=True)

    def match(self, frame, pts, kconf):
        """Returns the enrolled name when this person's face matches, else None."""
        if not self.known:
            return None
        head = [kpt(pts, kconf, j) for j in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)]
        head = [p for p in head if p]
        if not head:
            return None
        hx, hy = [p[0] for p in head], [p[1] for p in head]
        pad = max(40, int((max(hx) - min(hx)) * 1.5))
        x1, x2 = max(0, int(min(hx)) - pad), min(frame.shape[1], int(max(hx)) + pad)
        y1, y2 = max(0, int(min(hy)) - pad), min(frame.shape[0], int(max(hy)) + pad)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, None, fx=2, fy=2)  # CCTV faces are small
        self.det.setInputSize((crop.shape[1], crop.shape[0]))
        ok, faces = self.det.detect(crop)
        if faces is None or len(faces) == 0:
            return None
        # reject ambiguous crops with two faces (a neighbor's head leaking in)
        good = [f for f in faces if f[14] >= 0.6]
        if len(good) > 1:
            return None
        face = max(faces, key=lambda f: f[14])  # best-scoring face in the crop
        feat = self.rec.feature(self.rec.alignCrop(crop, face))
        # 1:N -- pick the BEST person, and only trust it if it clearly beats
        # the runner-up (order-independent, no marginal wrong matches)
        best_name, best, second = None, 0.0, 0.0
        for name, feats in self.known:
            s = max((self.rec.match(feat, f.reshape(1, -1),
                                    cv2.FaceRecognizerSF_FR_COSINE) for f in feats),
                    default=0.0)
            if s > best:
                best, second, best_name = s, best, name
            elif s > second:
                second = s
        if best >= self.cfg["face_match_cosine"] and best - second >= self.cfg["face_match_margin"]:
            return best_name
        return None


class RoleVoter:
    """Accumulates one-frame uniform votes into a sticky majority decision,
    plus enrolled-face checks. Sticky matters: a staff member who later
    slumps over the desk (hips invisible, no more votes possible) must keep
    the staff label. A face match wins over everything -- it can flip an
    out-of-uniform "customer" back to staff."""

    def __init__(self, cfg, faces=None):
        self.cfg = cfg
        self.faces = faces
        self.role = None          # "staff" / "customer"
        self.name = None          # enrolled face name, when matched
        self.votes = deque()      # (time, is_staff bool) while undecided
        self.last_face_t = 0.0

    def update(self, now, frame, pts, kconf):
        """Feed one sighting; returns the current role (or None).

        Asymmetric stickiness: 'staff' is terminal (never downgraded), but
        'customer' stays PROVISIONAL -- a customer (or undecided) keeps being
        re-voted on the uniform, and is upgraded to staff if a sustained beige
        majority later appears. This rescues staff who were locked as customer
        from a few bad early frames (pose/lighting/occlusion). A face match
        always wins -> staff."""
        # face match overrides everything (incl. a wrong 'customer' lock)
        if (self.faces is not None and self.role != "staff"
                and now - self.last_face_t >= self.cfg["face_check_every"]):
            self.last_face_t = now
            name = self.faces.match(frame, pts, kconf)
            if name:
                self.role, self.name = "staff", name
                return self.role

        if self.role == "staff":
            return self.role          # staff is terminal -- never re-evaluate

        # undecided OR provisionally-customer: keep voting on the uniform
        verdict = uniform_verdict(self.cfg, frame, pts, kconf)
        if verdict is not None:
            self.votes.append((now, verdict))
        while self.votes and now - self.votes[0][0] > self.cfg["role_window"]:
            self.votes.popleft()
        if len(self.votes) >= self.cfg["role_min_samples"]:
            staff_frac = sum(1 for _, v in self.votes if v) / len(self.votes)
            if staff_frac >= self.cfg["role_majority"]:
                self.role = "staff"   # decide, or UPGRADE customer -> staff
            elif self.role is None and staff_frac <= 1 - self.cfg["role_majority"]:
                self.role = "customer"   # only set customer when still undecided
        return self.role


def display_label(role, face_id=None):
    """Label string used in events and on screen.
      - a NAMED person shows just their name (e.g. "Phai")
      - an enrolled-but-unnamed face shows "STAFF:staff_04" so unnamed
        individuals stay distinguishable until a name is filled in
      - staff known only by uniform shows "STAFF"; customer / unknown as before
    """
    if role == "staff" and face_id:
        name = staff_name(face_id)
        return name if name != face_id else f"STAFF:{face_id}"
    return {"staff": "STAFF", "customer": "customer"}.get(role, "?")
