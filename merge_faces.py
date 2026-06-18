# Merge duplicate face ids into one person (CCTV faces of the same person
# sometimes score below the match threshold and get enrolled twice). Merging
# pools their embeddings, so the kept id recognizes that person from more
# angles -- which also makes future duplicates less likely.
#
# Usage:  python merge_faces.py <keep_id> <dup_id> [<dup_id> ...]
#   e.g.  python merge_faces.py staff_04 staff_06
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from config import CONFIG

keep = sys.argv[1]
dups = sys.argv[2:]
if not dups:
    sys.exit("usage: python merge_faces.py <keep_id> <dup_id> [<dup_id> ...]")

fd = CONFIG["faces_dir"]
keep_path = os.path.join(fd, f"{keep}.npz")
feats = [np.load(keep_path)["feats"]]
for d in dups:
    p = os.path.join(fd, f"{d}.npz")
    feats.append(np.load(p)["feats"])
merged = np.concatenate(feats, axis=0)
# keep the profile diverse and bounded (same cap as auto-enrollment)
from face_enroller import prune_to_cap
before = merged.shape[0]
merged = prune_to_cap(merged, CONFIG.get("face_samples_cap", 30))
np.savez(keep_path, feats=merged)
print(f"{keep}: {merged.shape[0]} embeddings after merge"
      + (f" (pruned from {before} to cap)" if before > merged.shape[0] else ""))

# drop the duplicate files + registry entries
for d in dups:
    for ext in (".npz", ".jpg"):
        f = os.path.join(fd, f"{d}{ext}")
        if os.path.exists(f):
            os.remove(f)
reg_path = CONFIG["staff_registry"]
with open(reg_path, encoding="utf-8") as f:
    reg = json.load(f)
for d in dups:
    reg.pop(d, None)
with open(reg_path, "w", encoding="utf-8") as f:
    json.dump(reg, f, ensure_ascii=False, indent=2)
print(f"removed {', '.join(dups)} -> merged into {keep}")
