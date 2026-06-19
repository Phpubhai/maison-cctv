#!/usr/bin/env python3
"""Measure the staff uniform colour PER CAMERA from saved evidence images.

Staff penalty snapshots (Penalty/, behavior_events/) have the person boxed in
red. We locate that red box, sample a strip over the chest, and report the HSV
distribution -- so we can see how the beige actually reads on each camera and
whether the current "therapist beige" range covers it.

Usage: python calibrate_from_evidence.py [camera_substring]
Read-only: prints stats, changes nothing.
"""
import glob
import os
import sys

import cv2
import numpy as np

from config import CONFIG

CAMS = ["front_door", "reception", "foot_spa", "makeup_room", "office", "street"]
only = sys.argv[1] if len(sys.argv) > 1 else None


def red_box(img):
    """Bounding box of the drawn red annotation rectangle, or None."""
    b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    red = (r > 180) & (g < 80) & (b < 80)
    ys, xs = np.where(red)
    if len(xs) < 50:
        return None
    return xs.min(), ys.min(), xs.max(), ys.max(), red


def chest_pixels(img):
    """HSV pixels over the boxed person's chest (skip the red lines + head)."""
    rb = red_box(img)
    if rb is None:
        return None
    x1, y1, x2, y2, red = rb
    bw, bh = x2 - x1, y2 - y1
    if bw < 30 or bh < 60:
        return None
    # chest band: 25-55% down the box, central 40% across
    cy1, cy2 = int(y1 + 0.25 * bh), int(y1 + 0.55 * bh)
    cx1, cx2 = int(x1 + 0.30 * bw), int(x1 + 0.70 * bw)
    crop = img[cy1:cy2, cx1:cx2]
    notred = ~red[cy1:cy2, cx1:cx2]
    if crop.size == 0 or notred.sum() < 30:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return hsv[notred]


def in_range(hsv_px, ranges):
    """Fraction of HSV pixels inside any (lo, hi) range."""
    m = np.zeros(len(hsv_px), bool)
    for lo, hi in ranges:
        lo, hi = np.array(lo), np.array(hi)
        m |= np.all((hsv_px >= lo) & (hsv_px <= hi), axis=1)
    return m.mean()


ranges = CONFIG["uniform_sets"]["therapist beige"]
# candidate: same warm hue bands, but S and V widened to the measured spread
CANDIDATE = [((0, 8, 35), (45, 175, 230)), ((150, 8, 35), (179, 175, 230))]
for cam in CAMS:
    if only and only not in cam:
        continue
    files = (glob.glob(f"Penalty/*{cam}*.jpg") + glob.glob(f"behavior_events/*{cam}*.jpg"))
    files = [f for f in files if "customer" not in f.lower() and "posture" not in f.lower()]
    allpx, hits = [], []
    for f in files[:200]:
        img = cv2.imread(f)
        if img is None:
            continue
        px = chest_pixels(img)
        if px is None or len(px) < 30:
            continue
        allpx.append(px)
        hits.append(in_range(px, ranges))
    if not allpx:
        print(f"{cam:12} no usable staff images")
        continue
    px = np.concatenate(allpx)
    cand_hit = np.mean([in_range(p, CANDIDATE) for p in allpx])
    h, s, v = px[:, 0], px[:, 1], px[:, 2]
    pct = lambda a: f"{np.percentile(a,10):.0f}-{np.percentile(a,90):.0f}"
    print(f"{cam:12} imgs={len(allpx):3}  current={np.mean(hits)*100:3.0f}%  "
          f"candidate={cand_hit*100:3.0f}%   H={pct(h):>7} S={pct(s):>7} V={pct(v):>9}")
