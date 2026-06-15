# Drawing only -- no analysis, no state. Boxes colored by role and sleep
# state, phone boxes, and a black timeline strip with the camera's recent
# events under each view.
import cv2
import numpy as np

STATE_COLORS = {            # staff boxes, BGR
    "active": (0, 200, 0),
    "drowsy": (0, 220, 255),
    "sleeping": (0, 0, 255),
}
ROLE_COLORS = {
    "customer": (170, 170, 170),
    None: (130, 130, 130),  # undecided "?"
}
SEVERITY_COLORS = {
    "normal": (190, 190, 190),
    "warning": (0, 220, 255),
    "alert": (0, 0, 255),
}
PHONE_COLOR = (255, 0, 255)
LINE_H = 22


def draw_people(frame, people):
    """people: display dicts from TrackManager.update()."""
    for p in people:
        color = (STATE_COLORS.get(p["state"], (255, 255, 255))
                 if p["role"] == "staff" else ROLE_COLORS.get(p["role"]))
        x1, y1 = int(p["box"][0]), int(p["box"][1])
        x2, y2 = int(p["box"][2]), int(p["box"][3])
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        ty = y1 - 32 if y1 > 56 else y1 + 20
        for text, dy, scale in ((p["tag"], 0, 0.6), (p["line2"], 22, 0.55)):
            cv2.putText(frame, text, (x1, ty + dy), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, text, (x1, ty + dy), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, color if dy == 0 else (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def draw_phones(frame, phones):
    for px1, py1, px2, py2 in phones:
        cv2.rectangle(frame, (int(px1), int(py1)), (int(px2), int(py2)), PHONE_COLOR, 2)
        cv2.putText(frame, "phone", (int(px1), int(py1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, PHONE_COLOR, 2, cv2.LINE_AA)
    return frame


def timeline_strip(events, width, max_events):
    """Black strip listing the last events (newest at the bottom),
    color-coded by severity."""
    strip = np.zeros((LINE_H * max_events + 8, width, 3), np.uint8)
    for i, e in enumerate(events[-max_events:]):
        color = SEVERITY_COLORS.get(e["severity"], (190, 190, 190))
        text = f"{e['timestamp'][11:]}  {e['label']}  {e['event']}  {e['description']}"
        cv2.putText(strip, text, (8, LINE_H * (i + 1)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return strip


def compose(frame, camera_id, events, max_events):
    """Camera name on the frame + timeline strip below it."""
    cv2.putText(frame, camera_id, (12, 32), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, camera_id, (12, 32), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([frame, timeline_strip(events, frame.shape[1], max_events)])
