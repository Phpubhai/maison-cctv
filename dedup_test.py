# Unit tests for face dedup: profile enrichment + duplicate suggestion +
# cap pruning. No camera/model -- _embed is patched to return chosen vectors.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from config import CONFIG
from face_enroller import AutoEnroller, prune_to_cap, _cos


def vec(*active, d=128):
    """A unit-ish embedding with given dims set (controls cosine similarity)."""
    v = np.zeros(d, np.float32)
    for i in active:
        v[i] = 1.0
    return v


class FakeFM:
    def __init__(self):
        self.known = []  # (name, feats ndarray)
        import cv2
        self.rec = cv2.FaceRecognizerSF.create(CONFIG["face_rec_model"], "")


class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, cam, label, event, desc, sev, **kw):
        self.events.append((event, desc))


def make(tmp):
    cfg = dict(CONFIG, faces_dir=tmp, staff_registry=os.path.join(tmp, "staff.json"),
               enroll_samples=3, enroll_check_every=0.0, enroll_consistency=0.0)
    fm = FakeFM()
    enr = AutoEnroller(cfg, fm, FakeLogger(), "office")
    return cfg, fm, enr


# ---- prune_to_cap keeps diverse, drops redundant -------------------------
feats = np.stack([vec(0), vec(0), vec(0), vec(1), vec(2)]).astype(np.float32)  # 3 identical + 2 unique
kept = prune_to_cap(feats, 3)
assert len(kept) == 3
# the two unique ones must survive; only one of the identical trio remains
uniq = sum(1 for r in kept if r[1] == 1 or r[2] == 1)
assert uniq == 2, kept
print("1) prune_to_cap drops redundant, keeps diverse  OK")

# ---- enrichment: new angle appended, redundant/borderline skipped --------
tmp = tempfile.mkdtemp()
cfg, fm, enr = make(tmp)
# Phai profile: one embedding
base = vec(0, 1, 2, 3)              # 4 dims on
fm.known.append(("staff_05", np.stack([base])))
# a NEW-ANGLE owner sample: overlaps a lot but not identical -> should append
newang = vec(0, 1, 2, 9)           # cos vs base = 3/4 = 0.75 (in [0.55,0.92])
enr._enrich("staff_05", newang.reshape(1, -1).astype(np.float32))
assert len(fm.known[0][1]) == 2, len(fm.known[0][1])
# near-identical sample -> skipped (> enrich_max_sim)
enr._enrich("staff_05", base.reshape(1, -1).astype(np.float32))
assert len(fm.known[0][1]) == 2, "near-identical should be skipped"
# borderline sample (low sim) -> skipped (< enrich_min_sim)
enr._enrich("staff_05", vec(20, 21).reshape(1, -1).astype(np.float32))
assert len(fm.known[0][1]) == 2, "borderline should be skipped"
print("2) enrich: appends new angle, skips redundant + borderline  OK")

# ---- feed: a re-seen known person does NOT create a new id, enriches ------
cfg, fm, enr = make(tmp)
fm.known.append(("staff_05", np.stack([vec(0, 1, 2, 3)])))
seen = vec(0, 1, 2, 8)             # cos 0.75 vs profile -> known + new angle
enr._embed = lambda frame, box: (seen.reshape(1, -1).astype(np.float32), None)
for _ in range(5):
    enr.feed(0.0, None, [(1, [0, 0, 1, 1])])
names = [n for n, _ in fm.known]
assert names == ["staff_05"], names                    # no duplicate created
assert len(fm.known[0][1]) >= 2                         # enriched
print("3) re-seen known person -> no new id, profile enriched  OK")

# ---- dup suggest: a near-but-below-threshold new person is flagged --------
cfg, fm, enr = make(tmp)
open(cfg["staff_registry"], "w").write("{}")
# staff_05 = 9 dims; new person shares 4 -> cos = 4/sqrt(9*8) = 0.471, which
# lands in the gray zone [0.42, 0.50) -> should suggest (not auto-merge).
fm.known.append(("staff_05", np.stack([vec(0, 1, 2, 3, 4, 5, 6, 7, 8)])))
dupish = vec(0, 1, 2, 3, 50, 51, 52, 53)   # shares {0,1,2,3} with staff_05
dummy_crop = np.zeros((20, 20, 3), np.uint8)   # stand-in for the aligned face
enr._embed = lambda frame, box: (dupish.reshape(1, -1).astype(np.float32), dummy_crop)
import cv2  # patch imwrite to avoid writing a real preview file
cv2.imwrite = lambda *a, **k: True
for _ in range(4):
    enr.feed(0.0, None, [(7, [0, 0, 1, 1])])
ev = [e for e, _ in enr.logger.events]
assert "STAFF ENROLLED" in ev, ev                       # new id created
assert "FACE DUP SUSPECT" in ev, ev                     # ...and flagged
assert [n for n, _ in fm.known] == ["staff_05", "staff_06"], fm.known  # NOT merged
assert os.path.exists(os.path.join(tmp, "suspected_duplicates.txt"))
print("4) gray-zone new id created (never merged) + dup flagged + logged  OK")

print("all dedup tests pass")
