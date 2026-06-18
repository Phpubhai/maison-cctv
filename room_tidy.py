# Room-tidiness watch: compare the live view of an EMPTY room against a
# reference snapshot of the room in its tidy state (chairs in place, table
# clear). While someone is in the room nothing is judged -- a customer using
# the chairs is normal. Once the room has been empty for tidy_empty_secs and
# still differs from the reference for tidy_messy_secs, staff failed to reset
# the room -> ROOM MESSY (alert + Penalty image). Recovery is logged too.
#
# Capture/refresh a reference: save a frame of the tidy room as the file
# named in CONFIG["tidy_cameras"][camera]["ref"], then restart the monitor.
import time

import cv2
import numpy as np

_W, _H = 640, 360  # comparison resolution


def _clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"


class TidyMonitor:
    def __init__(self, camera_id, cfg, logger):
        spec = cfg.get("tidy_cameras", {}).get(camera_id)
        self.enabled = spec is not None
        if not self.enabled:
            return
        self.camera_id = camera_id
        self.cfg = cfg
        self.logger = logger
        ref = cv2.imread(spec["ref"])
        if ref is None:
            print(f"tidy watch {camera_id}: reference image missing "
                  f"({spec['ref']}) -> disabled", flush=True)
            self.enabled = False
            return
        self.roi = spec["roi"]  # (x1, y1, x2, y2) as fractions of the frame
        self.ref = self._prep(ref)
        self.empty_since = None   # room continuously empty since
        self.messy_since = None   # view continuously off-reference since
        self.alerted = False
        self.last_check = 0.0
        self.last_alert = float("-inf")
        print(f"tidy watch on {camera_id}: reference loaded", flush=True)

    @staticmethod
    def _prep(frame):
        small = cv2.resize(frame, (_W, _H))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (21, 21), 0)  # blur kills sensor noise

    def diff(self, frame):
        """(changed fraction inside the ROI, bbox of the changed area in
        full-frame coordinates)."""
        x1, y1 = int(self.roi[0] * _W), int(self.roi[1] * _H)
        x2, y2 = int(self.roi[2] * _W), int(self.roi[3] * _H)
        d = cv2.absdiff(self._prep(frame), self.ref)[y1:y2, x1:x2]
        mask = d > self.cfg["tidy_pixel_thresh"]
        frac = float(mask.mean())
        h, w = frame.shape[:2]
        ys, xs = np.nonzero(mask)
        if len(xs):
            box = [(x1 + xs.min()) / _W * w, (y1 + ys.min()) / _H * h,
                   (x1 + xs.max()) / _W * w, (y1 + ys.max()) / _H * h]
        else:
            box = [0, 0, w, h]
        return frac, box

    def update(self, now, frame, person_count):
        if not self.enabled:
            return
        if person_count > 0:
            self.empty_since = None  # room in use -> never judged
            return
        self.empty_since = self.empty_since or now
        if now - self.empty_since < self.cfg["tidy_empty_secs"]:
            return
        if now - self.last_check < self.cfg["tidy_check_every"]:
            return
        self.last_check = now

        frac, box = self.diff(frame)
        if frac >= self.cfg["tidy_diff_frac"]:
            self.messy_since = self.messy_since or now
            held = now - self.messy_since
            if (held >= self.cfg["tidy_messy_secs"]
                    and now - self.last_alert >= self.cfg["re_alert_secs"]):
                self.last_alert = now
                self.alerted = True
                img = self.logger.save_evidence(frame, box, self.camera_id, "STAFF",
                                                "ROOM MESSY", duration=held,
                                                started=self.messy_since)
                self.logger.log(self.camera_id, "STAFF", "ROOM MESSY",
                                f"room differs from the tidy reference "
                                f"({frac:.0%} of view) since {_clock(self.messy_since)} "
                                f"while empty", "alert", image_path=img, duration=held)
        else:
            if self.alerted:
                self.logger.log(self.camera_id, "STAFF", "ROOM TIDY",
                                f"room restored, was messy ~{int(now - self.messy_since)}s "
                                f"({_clock(self.messy_since)} - {_clock(now)})", "normal")
            self.messy_since, self.alerted = None, False
