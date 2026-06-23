# Unit test for IdentityResolver: correction > face(+roster) > sticky-decay >
# anonymous. No models/network -- pure logic with a manual clock (the `now`
# argument). Runs without a staff.json (registry empty -> staff_name returns
# the face id verbatim, which is what these asserts expect).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from identity_resolver import IdentityResolver

cfg = {"id_face_conf": 0.9, "id_conf_decay": 0.1, "id_min_conf": 0.3}
r = IdentityResolver(cfg)

res = r.resolve(0, "spa room:1", face_id="Phai")
assert res["name"] == "Phai" and res["key"] == "Phai" and res["source"] == "face"
assert res["confidence"] == 0.9, res
print("1) face match -> named, key = name (merges across cameras)  OK")

res = r.resolve(1, "spa room:1", face_id=None)
assert res["name"] == "Phai" and res["source"] == "sticky", res
assert res["confidence"] == 0.8, res
print("2) sticky keeps the name through a faceless frame (decays)  OK")

res = r.resolve(10, "spa room:1", face_id=None)  # big gap -> conf < min
assert res["name"] is None and res["source"] == "anonymous", res
assert res["key"] == "spa room:1", res
print("3) confidence below floor -> anonymous (never a wrong name)  OK")

r.apply_correction("foot spa:7", "Nicky")
res = r.resolve(20, "foot spa:7", face_id=None)
assert res["name"] == "Nicky" and res["source"] == "correction", res
assert res["confidence"] == 1.0, res
print("4) reception correction overrides everything  OK")

r.set_roster({"Phai", "Nicky"})
res = r.resolve(30, "reception:3", face_id="Bua")     # Bua not on shift
assert res["name"] is None and res["source"] == "anonymous", res
res = r.resolve(31, "reception:4", face_id="Phai")    # Phai on shift
assert res["name"] == "Phai" and res["source"] == "face", res
print("5) roster filter ignores off-shift face names  OK")

r.depart("foot spa:7")
res = r.resolve(40, "foot spa:7", face_id=None)
assert res["name"] is None, res                       # correction forgotten
print("6) depart() clears per-track state  OK")
print("all identity_resolver tests pass")
