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
