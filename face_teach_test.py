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
