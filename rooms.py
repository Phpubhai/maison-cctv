# rooms.py -- logical "room" layer on top of cameras.
#
# A camera is a device; a room is what reception cares about. One camera can
# hold several rooms (zones), and a room can be camera-less (presence inferred
# from a doorway/threshold zone). Everything is keyed off CONFIG["rooms"]:
#   {name: {"type","via","camera", "zone"?, "door"?, "anchor"?}}
#   type: "service" | "front" | "back" | "rest" | "facility"
#   via:  "camera" (whole frame = this room)
#       | "zone"   (box center inside "zone" (x1,y1,x2,y2) fractions)
#       | "threshold" (camera-less; "door" rect is its doorway on `camera`)


def _center_frac(box, frame_shape):
    h, w = frame_shape[:2]
    cx = ((box[0] + box[2]) / 2.0) / w
    cy = ((box[1] + box[3]) / 2.0) / h
    return cx, cy


def _in_rect(cx, cy, rect):
    x1, y1, x2, y2 = rect
    return x1 <= cx <= x2 and y1 <= cy <= y2


def which_room(camera_id, box, frame_shape, cfg):
    """Logical room for a box on a camera, or None. Priority: a matching
    via:"zone" room (center inside its zone), else the camera's via:"camera"
    room, else None. Threshold rooms are NOT returned here (camera-less)."""
    cx, cy = _center_frac(box, frame_shape)
    default = None
    for name, spec in cfg.get("rooms", {}).items():
        if spec.get("camera") != camera_id:
            continue
        via = spec.get("via")
        if via == "zone" and spec.get("zone") and _in_rect(cx, cy, spec["zone"]):
            return name
        if via == "camera":
            default = name
    return default


def which_threshold(camera_id, box, frame_shape, cfg):
    """Camera-less room whose doorway zone the box center sits in, or None.
    Used to infer presence once the person then disappears (see PresenceEngine)."""
    cx, cy = _center_frac(box, frame_shape)
    for name, spec in cfg.get("rooms", {}).items():
        if (spec.get("camera") == camera_id and spec.get("via") == "threshold"
                and spec.get("door") and _in_rect(cx, cy, spec["door"])):
            return name
    return None


def room_type(room_name, cfg):
    """'service' | 'front' | 'back' | 'rest' | 'facility' | None."""
    spec = cfg.get("rooms", {}).get(room_name)
    return spec.get("type") if spec else None


def rooms_for_camera(camera_id, cfg):
    return [n for n, s in cfg.get("rooms", {}).items()
            if s.get("camera") == camera_id]
