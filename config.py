# All tunables in one place. Every module reads from this dict only --
# change behavior here, not inside the modules.
import os

import torch

GPU = torch.cuda.is_available()
_HERE = os.path.dirname(os.path.abspath(__file__))   # this folder -- all
                                                     # model files live here
_ROOT = os.path.dirname(_HERE)                       # the project folder

# Secrets live OUTSIDE git (NVR password, POS api key). Copy
# local_settings.example.py -> local_settings.py and fill in real values;
# local_settings.py is gitignored. Falls back to env vars, then a redacted
# placeholder, so the repo is safe to share.
try:
    from local_settings import NVR_URL, POS_API_KEY
except ImportError:
    NVR_URL = os.environ.get(
        "NVR_URL", "rtsp://USER:PASSWORD@HOST:554/user=USER&password=PASSWORD&channel={ch}")
    POS_API_KEY = os.environ.get("POS_API_KEY", "")

CONFIG = {
    # --- cameras ----------------------------------------------------------
    # All streams come from the NVR server: one box, one credential, one
    # channel per camera. (name, channel) -- add/remove/rename cameras HERE.
    # The name appears on screen, in events.jsonl, and in evidence filenames.
    # Channel map RE-VERIFIED 2026-06-15 (see nvr_probe.py): the NVR had
    # reshuffled every channel. Match by the room shown, not the channel
    # number -- all rules below are keyed by NAME, so only this mapping moves.
    "nvr_url": NVR_URL,   # real value from local_settings.py (gitignored)
    "cameras": [
        ("front door", 3),    # cashier desk + pedicure chairs, 1280x720
        ("reception", 1),     # lounge, 1920x1080
        ("foot spa", 2),      # foot-spa corridor, 2304x1296
        ("office", 4),        # back office / workshop, 1920x1080 (watch-only)
        ("street", 5),        # outdoor, in front of the shop, 1280x720
        ("makeup room", 6),   # NOT on the NVR right now -> shows offline.
                              # Update this channel when the camera returns.
    ],
    # watch-only cameras: live view with plain person boxes, NOTHING else --
    # no role classification, no sleep/phone/posture analysis, no timeline
    # events, no evidence images. For public space (passers-by must not be
    # analyzed or photographed).
    "watch_only": ["street"],
    # presence cameras: a staff-only room. Everyone in frame is logged as
    # STAFF on enter/leave (no uniform/face check needed -- only staff can be
    # here) but NO penalty analysis runs. For back rooms where normal staff
    # behavior (phone, resting) is not a violation.
    "presence_cameras": ["office"],

    # --- detection ------------------------------------------------------
    # same model family/sizes as behavior_monitor_v2: yolo11, big on GPU
    "det_model": os.path.join(_HERE, "yolo11m.pt" if GPU else "yolo11n.pt"),
    "pose_model": os.path.join(_HERE, "yolo11x-pose.pt" if GPU else "yolo11s-pose.pt"),
    "confidence": 0.4,                # detector confidence threshold
    "imgsz": 1280 if GPU else 640,    # inference size for both models
    "sample_fps": 4.0,                # analyses per second, total budget

    # --- sleep / drowsiness ----------------------------------------------
    # evidence = still AND (head down / lying / face buried / eyes closed).
    # Held drowsy_seconds -> DROWSY warning; sleep_seconds -> SLEEPING alert.
    "drowsy_seconds": 15.0,
    "sleep_seconds": 180.0,       # production value from v2 -- naps, not glances
    "movement_window": 30.0,      # rolling seconds for the stillness check
    "movement_tolerance": 0.02,   # max bbox drift over the window
                                  # (fraction of frame width) to count "still"
    "head_drop_frac": 0.08,       # nose below shoulders by this fraction of
                                  # box height -> "head dropped"

    # --- eye state (MediaPipe), feeds the sleep evidence -------------------
    "face_model": os.path.join(_HERE, "face_landmarker.task"),
    "eye_every": 2.0,             # seconds between eye checks per person
    "eye_window": 90.0,           # PERCLOS history window
    "eye_min_samples": 8,         # face sightings needed before trusting PERCLOS
    "eye_closed_thresh": 0.75,    # blink score above this = "closed"
    "pitch_down_deg": -25,        # face pitched below this -> sample unreliable
    "perclos_sleep": 0.7,         # eyes closed >70% of window -> sleep evidence
    "perclos_awake": 0.3,         # eyes closed <30% of window -> vetoes sleeping

    # --- phone use (staff only) --------------------------------------------
    "phone_secs": 45.0,           # phone near a staff member this long -> alert
    "phone_confidence": 0.18,     # phones are small and angled on CCTV.
                                  # Measured 2026-06-11 on the cashier desk:
                                  # phone propped against the monitor 0.27-0.50,
                                  # phone IN THE HAND (fingers wrap it) only
                                  # 0.19-0.23 and seen in ~1 frame out of 12
    "phone_grace": 18.0,          # in-hand phones surface only every ~12s --
                                  # "phone near" stays alive this long between
                                  # sightings so the dwell timer keeps running
    "phone_crop_confidence": 0.25,  # threshold for the 2x-zoom second pass on
                                  # each person's box. Measured 2026-06-11:
                                  # in-hand phone scores med 0.27 / max 0.76
                                  # at 2x zoom vs med 0.17 on the full frame
    "service_zones": {            # customer-service areas (pedicure chairs etc).
                                  # Used TWO ways: phones here belong to the
                                  # customer being serviced (never the staff),
                                  # and a "new customer" track appearing here
                                  # is a re-tracked EXISTING customer -- not an
                                  # arrival, so no greeting check. Fractions.
        "front door": [(0.50, 0.0, 1.0, 0.60)],   # pedicure chairs
    },
    "phone_wrist_frac": 0.20,     # a phone counts only when its center is
                                  # within this fraction of the person's box
                                  # height from one of THEIR wrists ("near the
                                  # body" was too loose: customers scrolling
                                  # their own phone while being served pressed
                                  # penalties on the staff, 2026-06-12)

    # --- posture imbalance (customers only, standing only) ------------------
    "imb_window": 60.0,           # seconds of history for the estimate
    "imb_min_samples": 10,        # upright sightings needed before estimating
    "shoulder_tilt_deg": 7,
    "hip_tilt_deg": 7,
    "head_tilt_deg": 8,
    "lean_deg": 10,

    # --- staff/customer separation by uniform color -------------------------
    # NAMED uniform sets: {set name -> HSV ranges}. A person is staff when
    # any single set passes the thresholds (sets are checked separately so
    # two sets' colors can't combine to fake a pass). Add a future set (e.g.
    # "reception") as one more entry, then calibrate with uniform_calib.py.
    # "therapist beige" covers BOTH the male and female fronts (same tone);
    # calibrated 2026-06-11. Warm hues incl. the red wrap-around; white/gray
    # clothing reads BLUE on these cameras and stays out of range.
    "uniform_sets": {
        "therapist beige": [
            ((0, 8, 50), (40, 120, 200)),
            ((150, 8, 50), (179, 120, 200)),
        ],
    },
    "uniform_match_frac": 0.40,   # region counts as uniform above this
    "uniform_reject_frac": 0.20,  # clearly NOT uniform below this
    "chest_match_frac": 0.50,     # stricter chest-only fallback (seated, hips
    "chest_reject_frac": 0.15,    # hidden behind the desk)
    "role_min_samples": 4,        # votes needed before deciding the role
    "role_majority": 0.7,         # fraction of votes that must agree
    "role_window": 30.0,          # seconds of votes kept while undecided

    # --- known staff faces (enroll with enroll_face.py) ----------------------
    # a face match marks the person STAFF even out of uniform, and overrides
    # an earlier uniform-based "customer" decision
    "face_det_model": os.path.join(_HERE, "face_detection_yunet.onnx"),
    "face_rec_model": os.path.join(_HERE, "face_recognition_sface.onnx"),
    "faces_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "faces"),
    "staff_registry": os.path.join(_HERE, "staff.json"),  # face id -> real
                                  # name + POS therapist_id (hand-editable)
    "face_match_cosine": 0.50,    # SFace cosine similarity for a match.
                                  # The textbook value is 0.363, but at CCTV
                                  # resolution a DIFFERENT person measured
                                  # 0.448 against staff_01 (2026-06-11), while
                                  # true matches measured 0.575+ -- 0.50 keeps
                                  # real matches and blocks the look-alikes.
    "face_check_every": 3.0,      # seconds between face checks per person
    "face_match_margin": 0.10,    # 1:N: the best match must beat the 2nd-best
                                  # person by this much, else "not sure" (None).
                                  # Stops a marginal wrong match from winning.

    # --- auto-enrollment from staff-only (presence) rooms --------------------
    # everyone in a presence room is staff, so their face can be auto-added to
    # the registry for recognition in OTHER rooms. Heavily gated: only sharp
    # frontal faces, several consistent samples, and never a face that already
    # matches an enrolled person.
    "auto_enroll": True,
    "enroll_min_score": 0.85,     # YuNet detection score (frontal-ish) to keep
    "enroll_min_face": 60,        # min face size in px on the 2x crop
    "enroll_samples": 6,          # consistent samples before enrolling a person
    "enroll_check_every": 1.5,    # seconds between face captures per track
    "enroll_consistency": 0.55,   # min cosine among a track's own samples
                                  # (proves they're one stable person, not noise)

    # --- greeting rule (penalty) ---------------------------------------------
    # when a NEW customer enters one of these cameras, at least one STAFF
    # must be STANDING within greeting_secs -- otherwise GREETING MISSED
    # (alert + Penalty image). One check at a time per camera, with a
    # cooldown so a re-tracked customer doesn't trigger a duplicate check.
    "greeting_cameras": ["front door"],
    "greeting_secs": 30.0,
    "greeting_cooldown": 180.0,

    # --- room tidiness (penalty) ---------------------------------------------
    # cameras watched for an untidy room: compared against a reference
    # snapshot of the TIDY room, judged only while the room is EMPTY.
    # Re-capture the reference (save a frame over the ref file + restart)
    # whenever the room's standard arrangement changes.
    "tidy_cameras": {
        "makeup room": {
            "ref": os.path.join(_HERE, "tidy_ref_makeup_room.jpg"),
            "roi": (0.08, 0.05, 0.86, 0.98),  # frame fractions; skips curtains
        },
    },
    "tidy_empty_secs": 180.0,   # room must be empty this long before judging
    "tidy_messy_secs": 300.0,   # off-reference this long while empty -> alert
    "tidy_diff_frac": 0.12,     # fraction of ROI pixels changed = "different".
                                # Measured 2026-06-12: rearranged chairs read
                                # 0.38, identical view 0.00 -- 0.12 leaves room
                                # for day/evening lighting drift
    "tidy_pixel_thresh": 35,    # gray-level change per pixel that counts
    "tidy_check_every": 10.0,   # seconds between comparisons

    # --- floor objects (penalty) -----------------------------------------------
    # cameras watched for cups/glasses/bottles or any foreign object (cloth,
    # towel, ...) left on the FLOOR. NOT BOUND TO ANY CAMERA YET -- when the
    # camera for this exists, add an entry like:
    #   "back corridor": {
    #       "zone": (0.0, 0.45, 1.0, 1.0),   # the floor area, frame fractions
    #       "ref": os.path.join(_HERE, "floor_ref_back_corridor.jpg"),
    #   },
    # "ref" (a clean-floor snapshot) is optional but REQUIRED to catch cloth:
    # the object model has no towel class, fabric is found by reference diff.
    "floor_watch": {},
    "floor_secs": 60.0,         # object on the floor this long -> alert
    "floor_diff_frac": 0.04,    # zone fraction changed vs clean ref = object
    "floor_obj_conf": 0.25,     # cup/glass/bottle detection threshold
    "floor_check_every": 5.0,   # seconds between checks

    # --- tracking / timeline ---------------------------------------------
    "min_visible": 1.0,           # track must live this long before an
                                  # "enters frame" event (kills flicker ghosts)
    "track_grace": 15.0,          # track unseen this long -> "leaves frame"
                                  # (long, so occluded nappers keep their timers)
    "timeline_events": 6,         # events shown in the on-screen strip
    "re_alert_secs": 300.0,       # repeat an ongoing alert at most this often

    # --- output -----------------------------------------------------------
    "events_path": "events.jsonl",
    "timeline_dir": "timelines",  # per-camera human-readable .txt timelines
    "ws_host": "127.0.0.1",
    "ws_port": 8765,
    "evidence_dir": os.path.join(_HERE, "behavior_events"),
    "penalty_dir": os.path.join(_HERE, "Penalty"),  # staff misbehavior images
                                  # (SLEEPING / PHONE USE) go here instead

    # --- GPU survival (this PC's driver crashes under sustained load) -------
    "gpu_temp_pause": 80,         # pause all analysis at this temperature
    "gpu_temp_resume": 70,        # resume when cooled back down to this
    "gpu_poll_every": 30,         # seconds between nvidia-smi checks

    # --- display ------------------------------------------------------------
    "tile_w": 640,                # each camera tile is scaled to this width
}
