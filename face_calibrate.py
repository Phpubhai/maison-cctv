#!/usr/bin/env python3
"""Calibrate the face-match threshold from the ENROLLED embeddings themselves.

For every enrolled staff (faces/<id>.npz holds several SFace embeddings of one
person) measure:
  - intra: cosine between embeddings of the SAME id   (should be HIGH)
  - inter: cosine between embeddings of DIFFERENT ids  (should be LOW)
The match threshold should sit between the two. High inter pairs also reveal
ENROLLED DUPLICATES -- two ids that are really the same person.

Read-only: prints stats + duplicate suspects, changes nothing.
"""
import glob
import itertools
import os

import numpy as np

from config import CONFIG


def load():
    out = {}
    for f in sorted(glob.glob(os.path.join(CONFIG["faces_dir"], "*.npz"))):
        feats = np.load(f)["feats"].astype(np.float32)
        feats /= (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-9)  # unit
        out[os.path.basename(f)[:-4]] = feats
    return out


def cos_block(a, b):
    return a @ b.T   # both unit-normalized -> cosine


people = load()
print(f"{len(people)} enrolled ids: {', '.join(people)}\n")

# intra: same-id pairwise
intra = []
for fid, fe in people.items():
    if len(fe) >= 2:
        m = cos_block(fe, fe)
        intra.extend(m[np.triu_indices(len(fe), k=1)])
intra = np.array(intra)

# inter: best cosine between each DIFFERENT id pair (how confusable they are)
pairs = []
for (n1, f1), (n2, f2) in itertools.combinations(people.items(), 2):
    pairs.append((n1, n2, float(cos_block(f1, f2).max())))
inter_best = np.array([p[2] for p in pairs])

def stat(a):
    return (f"n={len(a):4}  min={a.min():.2f}  p10={np.percentile(a,10):.2f}  "
            f"med={np.median(a):.2f}  p90={np.percentile(a,90):.2f}  max={a.max():.2f}")

print("INTRA (same person, want HIGH):  ", stat(intra) if len(intra) else "n/a")
print("INTER (diff person, want LOW):   ", stat(inter_best))
print(f"\ncurrent face_match_cosine={CONFIG['face_match_cosine']}  "
      f"margin={CONFIG['face_match_margin']}")

print("\n-- ID pairs with HIGH cross similarity (likely the SAME person / duplicates) --")
for n1, n2, s in sorted(pairs, key=lambda p: -p[2])[:12]:
    flag = "  <-- DUPLICATE?" if s >= CONFIG["face_match_cosine"] else ""
    print(f"   {n1:10} vs {n2:10}  best cos={s:.2f}{flag}")
