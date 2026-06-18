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


def _cos(a, b):
    """Cosine similarity of two 1-D vectors (SFace's FR_COSINE metric)."""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def prune_to_cap(feats, cap):
    """Trim a person's embeddings to `cap`, dropping the MOST REDUNDANT ones
    (highest total similarity to the rest) so the kept set stays diverse.
    Model-free (numpy cosine) so merge_faces can reuse it. feats: (n, d)."""
    feats = np.asarray(feats)
    while len(feats) > cap:
        # redundancy score = sum of cosine to every other embedding
        red = [sum(_cos(feats[i], feats[j]) for j in range(len(feats)) if j != i)
               for i in range(len(feats))]
        feats = np.delete(feats, int(np.argmax(red)), axis=0)
    return feats


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
        self.last_cap = {}  # track id -> last capture time
        self.ident = {}     # track id -> matched/enrolled face id (for naming)

    def _next_id(self):
        n = 0
        for name, _ in self.fm.known:
            if name.startswith("staff_") and name[6:].isdigit():
                n = max(n, int(name[6:]))
        return f"staff_{n + 1:02d}"

    def _best_match(self, emb):
        """(best_name, best_sim, second_sim) over enrolled people -- best sim
        per person, then the top two people. For both the dedup decision and
        the gray-zone duplicate suggestion."""
        per = []
        for name, feats in self.fm.known:
            s = max((self.rec.match(emb, f.reshape(1, -1),
                                    cv2.FaceRecognizerSF_FR_COSINE) for f in feats),
                    default=0.0)
            per.append((s, name))
        per.sort(reverse=True)
        best = per[0] if per else (0.0, None)
        second = per[1][0] if len(per) > 1 else 0.0
        return best[1], best[0], second

    def identity(self, tid):
        """Enrolled face id this track has been matched/enrolled to, or None."""
        return self.ident.get(tid)

    def forget(self, tid):
        """Drop per-track state when a person leaves (called on LEAVE)."""
        for d in (self.samples, self.last_cap, self.ident):
            d.pop(tid, None)

    def _enrich(self, name, emb):
        """Add a re-seen owner sample to their profile to cover a new angle.
        Guards: must clearly be the owner AND add diversity; cap kept."""
        if not self.cfg.get("enrich_enabled"):
            return
        for i, (n, feats) in enumerate(self.fm.known):
            if n != name:
                continue
            sims = [_cos(emb.flatten(), f) for f in feats]
            top = max(sims) if sims else 0.0
            if top < self.cfg["enrich_min_sim"]:
                return  # borderline -- don't risk polluting the profile
            if top > self.cfg["enrich_max_sim"]:
                return  # near-identical to an existing angle -- redundant
            feats = np.vstack([feats, emb.flatten()])
            feats = prune_to_cap(feats, self.cfg["face_samples_cap"])
            self.fm.known[i] = (n, feats)
            np.savez(os.path.join(self.cfg["faces_dir"], f"{name}.npz"), feats=feats)
            return

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
            if now - self.last_cap.get(tid, 0) < self.cfg["enroll_check_every"]:
                continue
            self.last_cap[tid] = now
            emb, crop = self._embed(frame, box)
            if emb is None:
                continue
            name, best, second = self._best_match(emb)
            if name and best >= self.cfg["face_match_cosine"]:
                # already enrolled -> don't create a duplicate; enrich the
                # profile with this new angle so future captures keep matching.
                self.samples.pop(tid, None)
                # only attach the displayed name when the match is unambiguous
                if best - second >= self.cfg["face_match_margin"]:
                    self.ident[tid] = name
                self._enrich(name, emb)
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
        # before creating a new id, check whether it's likely a DUPLICATE of
        # an existing person (gray zone below the match threshold) -- we still
        # create the id (auto-merge would risk fusing two people on CCTV) but
        # flag it for one-click human confirmation.
        avg = np.mean(np.stack([e.flatten() for e in embs]), axis=0)
        near_name, near_best, near_second = self._best_match(avg.reshape(1, -1).astype(np.float32))

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
        self.ident[tid] = name
        self.samples.pop(tid, None)
        self.logger.log(self.camera_id, f"STAFF:{name}", "STAFF ENROLLED",
                        f"new staff face auto-enrolled as {name} -- set a name "
                        f"in staff.json", "normal")
        if (near_name and self.cfg["dup_suggest_sim"] <= near_best < self.cfg["face_match_cosine"]
                and near_best - near_second >= self.cfg["dup_margin"]):
            self._suggest_duplicate(name, near_name, near_best)

    def _suggest_duplicate(self, new_id, existing, sim):
        """Flag (do NOT merge) a likely duplicate for human confirmation."""
        self.logger.log(self.camera_id, f"STAFF:{new_id}", "FACE DUP SUSPECT",
                        f"{new_id} may be the same person as {existing} "
                        f"(sim {sim:.2f}) -- review: python merge_faces.py "
                        f"{existing} {new_id}", "normal")
        try:
            line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {new_id} ~ {existing}"
                    f"  sim={sim:.2f}  -> python merge_faces.py {existing} {new_id}\n")
            with open(os.path.join(self.cfg["faces_dir"], "suspected_duplicates.txt"),
                      "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

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
