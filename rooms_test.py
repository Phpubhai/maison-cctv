# Unit test for the logical room layer (rooms != cameras).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rooms import which_room, which_threshold, room_type, rooms_for_camera

cfg = {"rooms": {
    "Foot Spa": {"type": "service", "via": "camera", "camera": "foot spa"},
    "MAISON 1": {"type": "service", "via": "zone", "camera": "spa room",
                 "zone": (0.60, 0.0, 0.85, 0.90)},
    "MAISON 3": {"type": "service", "via": "zone", "camera": "spa room",
                 "zone": (0.20, 0.0, 0.40, 0.95)},
    "ห้องน้ำ": {"type": "facility", "via": "threshold", "camera": "back hall",
               "door": (0.40, 0.30, 0.60, 0.80)},
}}
frame = (1000, 1000, 3)  # h, w, c

assert which_room("spa room", (700, 400, 740, 500), frame, cfg) == "MAISON 1"
assert which_room("spa room", (290, 400, 310, 500), frame, cfg) == "MAISON 3"
assert which_room("spa room", (490, 400, 510, 500), frame, cfg) is None
assert which_room("foot spa", (10, 10, 50, 50), frame, cfg) == "Foot Spa"
print("1) which_room: zone match + whole-camera default  OK")

assert which_threshold("back hall", (480, 520, 520, 580), frame, cfg) == "ห้องน้ำ"
assert which_threshold("back hall", (10, 10, 30, 30), frame, cfg) is None
assert which_threshold("spa room", (480, 520, 520, 580), frame, cfg) is None
print("2) which_threshold: doorway zone only  OK")

assert room_type("Foot Spa", cfg) == "service"
assert room_type("ห้องน้ำ", cfg) == "facility"
assert room_type("nope", cfg) is None
print("3) room_type  OK")

assert set(rooms_for_camera("spa room", cfg)) == {"MAISON 1", "MAISON 3"}
print("4) rooms_for_camera  OK")
print("all rooms tests pass")
