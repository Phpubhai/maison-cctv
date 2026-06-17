# Floor-object watch: alert when a cup/glass/bottle or any foreign object
# (dropped cloth, towel, ...) sits in a defined FLOOR zone.
#
# Two signals, combined:
#   1. YOLO classes cup / wine glass / bottle inside the zone -> named object
#   2. difference vs a reference snapshot of the clean floor -> unknown
#      object (COCO has no "towel/cloth" class; the reference diff is what
#      catches fabric). The reference is optional -- without it only signal 1
#      runs.
# Judged only while NO person is in the frame (someone standing there may be
# about to pick it up, and their body would pollute the diff). The object
# must persist floor_secs -> OBJECT ON FLOOR (alert + Penalty image); the
# floor clearing again is logged as FLOOR CLEAR.
#
# NOT BOUND TO ANY CAMERA YET: bind one in CONFIG["floor_watch"] (see
# config.py for the template) and optionally capture a clean-floor reference.
import time

import cv2
import numpy as np

from detector import _zoom

FLOOR_CLASSES = {39: "bottle", 40: "wine glass", 41: "cup"}
_W, _H = 640, 360


def _clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"


class FloorWatch:
    def __init__(self, camera_id, cfg, logger):
        spec = cfg.get("floor_watch", {}).get(camera_id)
        self.enabled = spec is not None
        if not self.enabled:
            return
        self.camera_id = camera_id
        self.cfg = cfg
        self.logger = logger
        self.zone = spec["zone"]  # (x1, y1, x2, y2) fractions of the frame
        self.ref = None
        ref_path = spec.get("ref")
        if ref_path:
            img = cv2.imread(ref_path)
            if img is None:
                print(f"floor watch {camera_id}: reference {ref_path} missing "
                      f"-> object classes only, no cloth detection", flush=True)
            else:
                self.ref = self._prep(img)
        self.model = _zoom(cfg["det_model"])  # shared stateless instance
        self.pending_since = None
        self.alerted = False
        self.last_check = 0.0
        self.last_alert = float("-inf")
        print(f"floor watch on {camera_id}: zone {self.zone}, "
              f"reference {'loaded' if self.ref is not None else 'none'}", flush=True)

    @staticmethod
    def _prep(frame):
        small = cv2.resize(frame, (_W, _H))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (21, 21), 0)

    def _zone_px(self, w, h):
        return (int(self.zone[0] * w), int(self.zone[1] * h),
                int(self.zone[2] * w), int(self.zone[3] * h))

    def scan(self, frame):
        """Returns (found list of names, evidence box). Combines YOLO object
        classes in the zone with the clean-floor reference diff."""
        h, w = frame.shape[:2]
        zx1, zy1, zx2, zy2 = self._zone_px(w, h)
        found, box = [], None

        # 1) named objects inside the zone
        r = self.model(frame, conf=self.cfg["floor_obj_conf"],
                       classes=list(FLOOR_CLASSES), imgsz=self.cfg["imgsz"],
                       verbose=False)[0]
        for b in r.boxes:
            bx = [float(v) for v in b.xyxy[0]]
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            if zx1 < cx < zx2 and zy1 < cy < zy2:
                found.append(FLOOR_CLASSES[int(b.cls)])
                box = box or bx

        # 2) anything else on the floor (cloth, towel, ...) via reference diff
        if self.ref is not None:
            sx1, sy1 = int(self.zone[0] * _W), int(self.zone[1] * _H)
            sx2, sy2 = int(self.zone[2] * _W), int(self.zone[3] * _H)
            d = cv2.absdiff(self._prep(frame), self.ref)[sy1:sy2, sx1:sx2]
            mask = d > self.cfg["tidy_pixel_thresh"]
            if float(mask.mean()) >= self.cfg["floor_diff_frac"]:
                if not found:
                    found.append("unknown object (cloth?)")
                if box is None:
                    ys, xs = np.nonzero(mask)
                    box = [(sx1 + xs.min()) / _W * w, (sy1 + ys.min()) / _H * h,
                           (sx1 + xs.max()) / _W * w, (sy1 + ys.max()) / _H * h]
        return found, box

    def update(self, now, frame, person_count):
        if not self.enabled:
            return
        if person_count > 0:
            return  # someone there -- may be picking it up; bodies ruin the diff
        if now - self.last_check < self.cfg["floor_check_every"]:
            return
        self.last_check = now

        found, box = self.scan(frame)
        if found:
            self.pending_since = self.pending_since or now
            held = now - self.pending_since
            if (held >= self.cfg["floor_secs"]
                    and now - self.last_alert >= self.cfg["re_alert_secs"]):
                self.last_alert = now
                self.alerted = True
                what = ", ".join(sorted(set(found)))
                img = self.logger.save_evidence(frame, box, self.camera_id, "STAFF",
                                                "OBJECT ON FLOOR", duration=held,
                                                started=self.pending_since)
                self.logger.log(self.camera_id, "STAFF", "OBJECT ON FLOOR",
                                f"{what} on the floor since "
                                f"{_clock(self.pending_since)}", "alert", image_path=img)
        else:
            if self.alerted:
                self.logger.log(self.camera_id, "STAFF", "FLOOR CLEAR",
                                f"floor cleared, object was there "
                                f"~{int(now - self.pending_since)}s", "normal")
            self.pending_since, self.alerted = None, False
