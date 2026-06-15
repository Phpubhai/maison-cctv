# Sleep / drowsiness analysis from posture + movement + eye state over TIME,
# never one frame (ported from behavior_monitor_v2, reshaped into the
# active -> drowsy -> sleeping state machine).
#
# Evidence for sleep = barely moving over a rolling window AND at least one
# of: head dropped / lying / face buried in arms / eyes closed most of the
# time (PERCLOS). Clearly-open eyes VETO the verdict. Evidence must hold
# continuously for drowsy_seconds -> "drowsy", then sleep_seconds ->
# "sleeping"; a glance down at the desk never gets past "active".
import math
import os
from collections import deque

import cv2
import numpy as np
from ultralytics import YOLO

from posture import KP_CONF, L_EAR, L_EYE, NOSE, R_EAR, R_EYE, kpt

os.environ.setdefault("GLOG_minloglevel", "2")  # hide mediapipe chatter


class PoseEstimator:
    """Runs the pose model on a frame; results are matched to tracked people
    by box overlap. Stateless, so one instance is shared by all cameras."""

    def __init__(self, cfg):
        self.model = YOLO(cfg["pose_model"])
        self.conf = cfg["confidence"]
        self.imgsz = cfg["imgsz"]

    def estimate(self, frame):
        """Returns [{"box": [...], "pts": keypoints xy, "kconf": confs}]."""
        r = self.model(frame, conf=self.conf, imgsz=self.imgsz, verbose=False)[0]
        out = []
        for i, b in enumerate(r.boxes):
            out.append({
                "box": [float(v) for v in b.xyxy[0]],
                "pts": r.keypoints.xy[i],
                "kconf": r.keypoints.conf[i],
            })
        return out


class EyeScorer:
    """MediaPipe FaceLandmarker on a head crop -> blink score (0=open,
    1=closed) or None when no usable face. Shared by all cameras."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.landmarker = None
        if os.path.exists(cfg["face_model"]):
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            self.mp = mp
            self.landmarker = vision.FaceLandmarker.create_from_options(
                vision.FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=cfg["face_model"]),
                    running_mode=vision.RunningMode.IMAGE,
                    num_faces=1,
                    output_face_blendshapes=True,
                    output_facial_transformation_matrixes=True,
                    min_face_detection_confidence=0.3,
                ))
        else:
            print("face_landmarker.task not found -> eye detection disabled, "
                  "sleep detection falls back to posture + stillness only", flush=True)

    def score(self, frame, pts, kconf):
        if self.landmarker is None:
            return None
        head = [kpt(pts, kconf, j) for j in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)]
        head = [p for p in head if p]
        if not head:
            return None
        hx, hy = [p[0] for p in head], [p[1] for p in head]
        pad = max(40, int((max(hx) - min(hx)) * 1.5))
        x1, x2 = max(0, int(min(hx)) - pad), min(frame.shape[1], int(max(hx)) + pad)
        y1, y2 = max(0, int(min(hy)) - pad), min(frame.shape[0], int(max(hy)) + pad)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        scale = max(1, int(256 / max(crop.shape[:2])))
        if scale > 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale)
        img = self.mp.Image(
            image_format=self.mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
        res = self.landmarker.detect(img)
        if not res.face_blendshapes:
            return None
        # a face pitched DOWN (looking at a desk/phone) shows mostly eyelid
        # and fools the blink score -- skip those samples as unknown
        if res.facial_transformation_matrixes:
            R = np.array(res.facial_transformation_matrixes[0])[:3, :3]
            fwd_y = float((R @ np.array([0.0, 0.0, 1.0]))[1])
            pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd_y))))
            if pitch < self.cfg["pitch_down_deg"]:
                return None
        scores = {b.category_name: b.score for b in res.face_blendshapes[0]}
        return (scores.get("eyeBlinkLeft", 0) + scores.get("eyeBlinkRight", 0)) / 2


class SleepAnalyzer:
    """Per-person state machine: active -> drowsy -> sleeping.
    Feed it one observation per analysis pass via update()."""

    def __init__(self, cfg, eyes):
        self.cfg = cfg
        self.eyes = eyes
        self.hist = deque()          # (t, center x, center y) for stillness
        self.eye_hist = deque()      # (t, closed bool) for PERCLOS
        self.last_eye_t = 0.0
        self.evidence_since = None
        self.state = "active"
        self.why = ""

    def _still(self, now, box, frame_w):
        """True when total drift over the whole window is tiny. Window-based,
        so the verdict doesn't depend on the analysis rate."""
        self.hist.append((now, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
        while self.hist and now - self.hist[0][0] > self.cfg["movement_window"]:
            self.hist.popleft()
        if now - self.hist[0][0] < self.cfg["movement_window"] * 0.8:
            return False  # not enough history to judge yet
        xs = [x for _, x, _ in self.hist]
        ys = [y for _, _, y in self.hist]
        drift = (max(xs) - min(xs)) + (max(ys) - min(ys))
        return drift / frame_w < self.cfg["movement_tolerance"]

    def _perclos(self, now, frame, pts, kconf):
        """Fraction of recent face sightings with eyes closed, or None."""
        if now - self.last_eye_t >= self.cfg["eye_every"]:
            self.last_eye_t = now
            closed = self.eyes.score(frame, pts, kconf)
            if closed is not None:
                self.eye_hist.append((now, closed > self.cfg["eye_closed_thresh"]))
        while self.eye_hist and now - self.eye_hist[0][0] > self.cfg["eye_window"]:
            self.eye_hist.popleft()
        if len(self.eye_hist) < self.cfg["eye_min_samples"]:
            return None
        return sum(1 for _, c in self.eye_hist if c) / len(self.eye_hist)

    def update(self, now, frame, box, pts, kconf, posture, head_down, buried):
        """One observation. Returns (state, evidence_seconds, why)."""
        still = self._still(now, box, frame.shape[1])
        perclos = self._perclos(now, frame, pts, kconf)
        eyes_closed = perclos is not None and perclos >= self.cfg["perclos_sleep"]
        eyes_open = perclos is not None and perclos <= self.cfg["perclos_awake"]

        why = []
        if still:
            why.append("still")
        if head_down or posture == "lying":
            why.append(posture if posture == "lying" else "head-down")
        if buried:
            why.append("face hidden")
        if eyes_closed:
            why.append(f"eyes closed {int(perclos * 100)}%")

        evidence = (still and (head_down or posture == "lying" or buried
                               or eyes_closed) and not eyes_open)
        if evidence:
            self.evidence_since = self.evidence_since or now
            self.why = "+".join(why)
        else:
            self.evidence_since = None
            self.why = ""

        held = (now - self.evidence_since) if self.evidence_since else 0.0
        if held >= self.cfg["sleep_seconds"]:
            self.state = "sleeping"
        elif held >= self.cfg["drowsy_seconds"]:
            self.state = "drowsy"
        else:
            self.state = "active"
        return self.state, held, self.why
