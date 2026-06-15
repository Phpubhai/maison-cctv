# Auto-enrollment of staff faces from a presence (staff-only) room.
#
# Everyone in the office is staff, so any sharp frontal face there can be
# added to the registry and recognized in the OTHER rooms. Gating keeps junk
# out: a face must score high (frontal), reach enroll_samples consistent
# captures on one track, and NOT already match an enrolled person. New people
# become staff_NN with an empty name in staff.json (fill it in later).
import json
import os
import time

import cv2
import numpy as np

import person_labeler


class AutoEnroller:
    """Feeds off the office camera. Shares the live FaceMatcher so a newly
    enrolled face is recognized everywhere immediately, no restart."""

    def __init__(self, cfg, face_matcher, logger, camera_id):
        self.cfg = cfg
        self.fm = face_matcher
        self.logger = logger
        self.camera_id = camera_id
        self.det = cv2.FaceDetectorYN.create(cfg["face_det_model"], "", (320, 320),
                                             cfg["enroll_min_score"])
        self.rec = cv2.FaceRecognizerSF.create(cfg["face_rec_model"], "")
        self.samples = {}   # track id -> [(embedding, crop), ...] not-yet-known
        self.resolved = {}  # track id -> "known" / "enrolled" (stop sampling)
        self.last_cap = {}  # track id -> last capture time
        self.ident = {}     # track id -> matched/enrolled face id (for naming)

    def _next_id(self):
        n = 0
        for name, _ in self.fm.known:
            if name.startswith("staff_") and name[6:].isdigit():
                n = max(n, int(name[6:]))
        return f"staff_{n + 1:02d}"

    def _matches_known(self, emb):
        """Face id of the best enrolled match (>= threshold), or None."""
        best_name, best = None, 0.0
        for name, feats in self.fm.known:
            for f in feats:
                s = self.rec.match(emb, f.reshape(1, -1), cv2.FaceRecognizerSF_FR_COSINE)
                if s >= self.cfg["face_match_cosine"] and s > best:
                    best, best_name = s, name
        return best_name

    def identity(self, tid):
        """Enrolled face id this track has been matched/enrolled to, or None."""
        return self.ident.get(tid)

    def forget(self, tid):
        """Drop per-track state when a person leaves (called on LEAVE)."""
        for d in (self.samples, self.resolved, self.last_cap, self.ident):
            d.pop(tid, None)

    def _embed(self, frame, box):
        """(embedding, aligned 112x112 face crop) for the best frontal face
        inside a person's box, or (None, None)."""
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = int(box[2]), int(box[3])
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None
        big = cv2.resize(crop, None, fx=2, fy=2)  # CCTV faces are small
        self.det.setInputSize((big.shape[1], big.shape[0]))
        ok, faces = self.det.detect(big)
        if faces is None or len(faces) == 0:
            return None, None
        # two faces in one person box = a neighbor leaning in -> ambiguous,
        # never enroll (this is how staff_03 got contaminated, 2026-06-15)
        if sum(1 for f in faces if f[14] >= 0.6) > 1:
            return None, None
        face = max(faces, key=lambda f: f[14])
        if face[14] < self.cfg["enroll_min_score"] or min(face[2], face[3]) < self.cfg["enroll_min_face"]:
            return None, None
        aligned = self.rec.alignCrop(big, face)
        return self.rec.feature(aligned), aligned

    def feed(self, now, frame, rows):
        """rows: [(track_id, box), ...] of people currently in the room."""
        if not self.cfg.get("auto_enroll"):
            return
        for tid, box in rows:
            if self.resolved.get(tid):
                continue
            if now - self.last_cap.get(tid, 0) < self.cfg["enroll_check_every"]:
                continue
            self.last_cap[tid] = now
            emb, crop = self._embed(frame, box)
            if emb is None:
                continue
            match = self._matches_known(emb)
            if match:
                self.resolved[tid] = "known"   # already enrolled -- leave alone
                self.ident[tid] = match
                self.samples.pop(tid, None)
                continue
            self.samples.setdefault(tid, []).append((emb, crop))
            if len(self.samples[tid]) >= self.cfg["enroll_samples"]:
                self._try_enroll(tid)

    def _try_enroll(self, tid):
        samples = self.samples[tid]
        embs = [s[0] for s in samples]
        crops = [s[1] for s in samples]
        # consistency: every sample must look like the same person
        sims = [self.rec.match(embs[i], embs[j].reshape(1, -1),
                               cv2.FaceRecognizerSF_FR_COSINE)
                for i in range(len(embs)) for j in range(i + 1, len(embs))]
        if not sims or (sum(sims) / len(sims)) < self.cfg["enroll_consistency"]:
            self.samples[tid] = samples[-self.cfg["enroll_samples"]:]  # keep sampling
            return
        name = self._next_id()
        feats = np.stack([e.flatten() for e in embs])
        np.savez(os.path.join(self.cfg["faces_dir"], f"{name}.npz"), feats=feats)
        # save a preview crop so a human can put a name to the id later
        preview = cv2.resize(crops[len(crops) // 2], (224, 224),
                             interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(os.path.join(self.cfg["faces_dir"], f"{name}.jpg"), preview)
        # register with the live matcher so every camera knows them now
        self.fm.known.append((name, feats))
        self._add_to_registry(name)
        self.resolved[tid] = "enrolled"
        self.ident[tid] = name
        self.samples.pop(tid, None)
        self.logger.log(self.camera_id, f"STAFF:{name}", "STAFF ENROLLED",
                        f"new staff face auto-enrolled as {name} -- set a name "
                        f"in staff.json", "normal")

    def _add_to_registry(self, name):
        path = self.cfg["staff_registry"]
        reg = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                reg = json.load(f)
        reg.setdefault(name, {"name": "", "therapist_id": "", "source": "office-auto"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        person_labeler.load_registry(self.cfg)  # refresh name lookups live
