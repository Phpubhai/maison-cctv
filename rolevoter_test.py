# Unit test for RoleVoter asymmetric stickiness:
# - customer is provisional and upgradable to staff
# - staff is terminal (never downgraded)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from config import CONFIG
import person_labeler
from person_labeler import RoleVoter

# patch uniform_verdict to a value we control per call (no real frame needed)
_verdict = [None]
person_labeler.uniform_verdict = lambda cfg, frame, pts, kconf: _verdict[0]

cfg = dict(CONFIG, role_min_samples=4, role_majority=0.7, role_window=30.0)


def feed(v, voter, n, t0):
    _verdict[0] = v
    for i in range(n):
        voter.update(t0 + i, None, None, None)
        t0 += 1
    return t0


# 1) wrongly locked as customer, then sustained beige -> upgrades to staff
rv = RoleVoter(cfg, faces=None)
t = feed(False, rv, 5, 0)          # early bad frames -> customer
assert rv.role == "customer", rv.role
t = feed(True, rv, 6, 100)         # later: clear beige -> should upgrade
assert rv.role == "staff", rv.role
print("1) customer wrongly locked -> upgraded to staff on sustained beige  OK")

# 2) staff is terminal: once staff, a run of non-beige does NOT downgrade
rv = RoleVoter(cfg, faces=None)
feed(True, rv, 5, 0)
assert rv.role == "staff"
feed(False, rv, 10, 100)
assert rv.role == "staff", "staff must never be downgraded"
print("2) staff is terminal -> never downgraded to customer  OK")

# 3) a genuine customer (never beige) stays customer
rv = RoleVoter(cfg, faces=None)
feed(False, rv, 8, 0)
assert rv.role == "customer", rv.role
print("3) genuine non-uniform person stays customer  OK")

# 4) undecided stays None until enough agreeing votes
rv = RoleVoter(cfg, faces=None)
_verdict[0] = None
for i in range(5):
    rv.update(i, None, None, None)
assert rv.role is None
print("4) no usable votes -> stays undecided (?)  OK")

print("all rolevoter tests pass")
