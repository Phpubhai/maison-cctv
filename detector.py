# Person + phone detection with multi-object tracking (yolo11 + ByteTrack).
#
# ONE model.track() call per frame covers both classes -- half the inference
# cost of v2's separate person/phone models -- plus a 2x-zoom second pass on
# each person's box, because a phone IN THE HAND is nearly invisible at full
# frame (fingers wrap it; measured conf ~0.17) but readable at 2x (~0.27-0.76).
# One PersonDetector per camera: persist=True keeps the ByteTrack state inside
# the model object, so sharing an instance across cameras would mix their
# timelines. Track ids are INTERNAL -- downstream code uses them to keep
# per-person state, but they never appear in events or on screen.
import cv2
from ultralytics import YOLO

PERSON_CLS, PHONE_CLS = 0, 67  # COCO: person, cell phone

# the zoom pass runs plain inference, so it must NOT share the per-camera
# tracking model (mixing predict() into track() state is asking for trouble).
# One stateless instance serves every camera.
_zoom_model = None


def _zoom(path):
    global _zoom_model
    if _zoom_model is None:
        _zoom_model = YOLO(path)
    return _zoom_model


class PersonDetector:
    def __init__(self, cfg):
        self.model = YOLO(cfg["det_model"])
        self.conf = cfg["confidence"]
        self.phone_conf = cfg["phone_confidence"]
        self.crop_conf = cfg["phone_crop_confidence"]
        self.zoom = _zoom(cfg["det_model"])
        self.imgsz = cfg["imgsz"]

    def detect(self, frame):
        """Detect + track in one frame.
        Returns (persons, phones):
          persons: [{"track_id": int, "box": [x1, y1, x2, y2], "conf": float}]
          phones:  [[x1, y1, x2, y2], ...] (no ids -- dwell logic lives on the person)

        The call runs at the lower phone threshold; each class is then
        filtered to its own threshold (phones are small and angled on CCTV,
        they never score as high as people)."""
        r = self.model.track(frame, persist=True, classes=[PERSON_CLS, PHONE_CLS],
                             conf=min(self.conf, self.phone_conf), imgsz=self.imgsz,
                             tracker="bytetrack.yaml", verbose=False)[0]
        persons, phones = [], []
        if r.boxes is None:
            return persons, phones
        for b in r.boxes:
            box = [float(v) for v in b.xyxy[0]]
            conf = float(b.conf)
            if int(b.cls) == PHONE_CLS:
                if conf >= self.phone_conf:
                    phones.append(box)
            elif conf >= self.conf and b.id is not None:
                persons.append({"track_id": int(b.id), "box": box, "conf": conf})

        # second pass: re-check each person's box at 2x zoom for in-hand
        # phones, mapping hits back to frame coordinates
        for p in persons:
            x1, y1 = max(0, int(p["box"][0])), max(0, int(p["box"][1]))
            x2, y2 = int(p["box"][2]), int(p["box"][3])
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            big = cv2.resize(crop, None, fx=2, fy=2)
            rc = self.zoom(big, conf=self.crop_conf, classes=[PHONE_CLS],
                           imgsz=640, verbose=False)[0]
            for b in rc.boxes:
                bx = [float(v) / 2 for v in b.xyxy[0]]
                phones.append([x1 + bx[0], y1 + bx[1], x1 + bx[2], y1 + bx[3]])
        return persons, phones
