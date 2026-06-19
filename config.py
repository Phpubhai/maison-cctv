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

# Realtime push to the event server (yolo-server). Reads the SAME env vars the
# launcher (cctv-env.bat) exports, so the monitor and the standalone client
# share one key. Auto-disabled when not set -> no behavior change.
EVENT_PUSH_URL = os.environ.get("SERVER_URL", "")
EVENT_PUSH_KEY = os.environ.get("API_KEY", "")

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
    # ignore zones: people whose box CENTER falls inside are dropped before
    # tracking/analysis -- for someone working at the very edge of a frame who
    # keeps flickering in/out (spamming ENTER/LEAVE). Checking the CENTER means
    # a person merely walking through the area isn't dropped (their center sits
    # higher); only edge-clipped sitters land here. (x1,y1,x2,y2) frame fractions.
    "ignore_zones": {
        "office": [(0.0, 0.72, 0.68, 1.0)],   # desks along the bottom edge
    },

    # staff zones: a fixed staff position WITHIN a camera. A person whose box
    # center sits here is forced to STAFF regardless of uniform/face -- rescues
    # seated staff the beige check keeps missing (the receptionist slumped at
    # the desk reads as "customer"). Keep the box TIGHT around the seat so a
    # customer standing nearby never falls in. (x1,y1,x2,y2) frame fractions.
    # front door: the reception desk seat at the far LEFT, where Tan sits.
    # CALIBRATE against a real frame if the seat moves (customers stand more to
    # the center, by the pedicure chairs on the right).
    "staff_zones": {
        "front door": [(0.0, 0.45, 0.18, 1.0)],
    },

    # rest zones: like staff_zones, but ALSO skip penalty analysis -- the staff
    # break room, where resting / phone / napping is allowed (force staff + no
    # sleeping/phone/etc. alerts). Value is a list of (x1,y1,x2,y2) rects, OR a
    # dict {"poly": [(x,y)...], "mode": "inside"|"outside"} for a polygon.
    # reception IS the staff room, filmed through a glass window into the
    # customer lounge -> everyone OUTSIDE the lounge polygon is resting staff.
    # Polygon traces the lounge through the glass; CALIBRATE if the view shifts.
    "rest_zones": {
        "reception": {"mode": "outside", "poly": [
            (0.05, 0.13), (0.32, 0.07), (0.62, 0.05), (0.80, 0.14), (0.83, 0.34),
            (0.80, 0.54), (0.60, 0.60), (0.40, 0.74), (0.16, 0.79), (0.05, 0.70)]},
    },

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
    # 2026-06-19: tightened to cut false positives -- massage staff hold oil
    # bottles/tools that YOLO sparsely mis-tags as "cell phone". Trades some
    # real-phone recall for far fewer false alerts (user's call). Levers below:
    # higher detection thresholds + shorter grace (sparse mis-hits can no longer
    # bridge a long gap to sustain the timer) + longer required dwell.
    "phone_secs": 60.0,           # phone near a staff member this long -> alert
    "phone_confidence": 0.22,     # phones are small and angled on CCTV.
                                  # Measured 2026-06-11 on the cashier desk:
                                  # phone propped against the monitor 0.27-0.50,
                                  # phone IN THE HAND (fingers wrap it) only
                                  # 0.19-0.23 and seen in ~1 frame out of 12
    "phone_grace": 12.0,          # in-hand phones surface only every ~12s --
                                  # "phone near" stays alive this long between
                                  # sightings so the dwell timer keeps running.
                                  # Tightened 18->12 (2026-06-19): a sparse
                                  # bottle mis-hit can't bridge a >12s gap now,
                                  # so it no longer sustains a false alert.
    "phone_crop_confidence": 0.30,  # threshold for the 2x-zoom second pass on
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
    # Re-calibrated 2026-06-19 from saved staff evidence across all cameras
    # (calibrate_from_evidence.py): the beige reads with higher saturation
    # (up to ~175, not 120) and a wider brightness span (~35-230, not 50-200)
    # than first measured -> staff chest pixels in-range jumped from 35-47% to
    # 57-68%, so seated/oddly-lit staff stop being misread as customer. Warm
    # hue bands kept (incl. the red wrap-around); white/gray clothing reads as
    # blue (H~100-130) and still falls outside.
    "uniform_sets": {
        "therapist beige": [
            ((0, 8, 35), (45, 175, 230)),
            ((150, 8, 35), (179, 175, 230)),
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

    # --- duplicate reduction (2026-06-17) ------------------------------------
    # CCTV faces of the SAME person across sessions sometimes score below the
    # match threshold -> a duplicate id is created. We fight this at the root
    # by ENRICHING a person's profile with new angles every time they're
    # re-recognized (so future captures match), and SUGGEST (never auto-merge)
    # likely duplicates for one-click human confirmation.
    "enrich_enabled": True,
    "enrich_min_sim": 0.55,       # a re-seen sample must clearly be the owner
                                  # (> match threshold) before joining the profile
    "enrich_max_sim": 0.92,       # ...but different enough to add a NEW angle
                                  # (near-identical frames are skipped)
    "face_samples_cap": 30,       # max embeddings per person; when full, drop
                                  # the most-redundant one to keep angles diverse
    "dup_suggest_sim": 0.42,      # new id whose best existing match is in
    "dup_margin": 0.05,           # [dup_suggest_sim, match) by this margin ->
                                  # logged as a suspected duplicate (not merged)

    # --- greeting rule (penalty) ---------------------------------------------
    # when a NEW customer enters one of these cameras, at least one STAFF
    # must be STANDING within greeting_secs -- otherwise GREETING MISSED
    # (alert + Penalty image). One check at a time per camera, with a
    # cooldown so a re-tracked customer doesn't trigger a duplicate check.
    "greeting_cameras": ["front door"],
    "greeting_secs": 30.0,
    "greeting_cooldown": 180.0,

    # customer ENTER/LEAVE = "entered/left the SHOP" -> only the entrance
    # camera(s) count. A customer walking into foot spa / reception is not an
    # arrival, so those don't reach the POS timeline (still recorded locally).
    "customer_flow_cameras": ["front door"],

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
    "floor_watch": {
        # reception: tea glasses left on the white tables in front of the sofa.
        # Clear-glass + glass-partition reflections make YOLO detection weak,
        # so this leans on the reference diff -> needs a CLEAN-table snapshot
        # at the ref path (capture once the table is tidy). Judged only when
        # the lounge is empty; must persist `secs` to ride out reflections.
        "reception": {
            "zone": (0.12, 0.44, 0.40, 0.78),     # the round white tables
            "ref": os.path.join(_HERE, "table_ref_reception.jpg"),
            "event": "UNCLEARED TABLE",
            "clear_event": "TABLE CLEARED",
            "where": "the reception table",
            "secs": 300.0,                         # left 5 min while empty -> alert
            "diff_frac": 0.06,                     # higher than floor (reflections)
        },
    },
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

    # --- timeline history (SQLite) -------------------------------------------
    # full local source of truth; queryable time/who/what table. A subset
    # (penalty + customer) is pushed to the POS. Evidence images stay local.
    "timeline_db": os.path.join(_HERE, "events.db"),
    "timeline_retention_days": 365,   # purge rows + orphan images older than this

    # local LAN-only image server so the POS "view image" link resolves
    # in-shop (images NEVER go to cloud). 0.0.0.0 = reachable on the LAN.
    "image_server": {"host": "0.0.0.0", "port": 8088, "enabled": True},

    # push the penalty+customer subset to the POS Cloud Function (Firestore).
    # base_url/api_key come from local_settings.py (POS_API_KEY) when ready.
    "pos_timeline": {"enabled": False, "poll_secs": 5, "batch": 25},

    # realtime push of the SAME penalty+customer subset to the LAN/VPS event
    # server (yolo-server) -> the POS timeline page. Driven by SERVER_URL +
    # API_KEY env (set by run-monitor.bat / cctv-env.bat). Off when unset.
    "event_push": {
        "enabled": bool(EVENT_PUSH_URL and EVENT_PUSH_KEY),
        "server_url": EVENT_PUSH_URL,
        "api_key": EVENT_PUSH_KEY,
        # also UPLOAD the snapshot image to the server (so it has its own copy
        # and can serve it even on a different machine). Set False to keep
        # images local-only and let the POS pull them from this machine.
        "push_snapshots": True,
    },

    # --- GPU survival (this PC's driver crashes under sustained load) -------
    "gpu_temp_pause": 80,         # pause all analysis at this temperature
    "gpu_temp_resume": 70,        # resume when cooled back down to this
    "gpu_poll_every": 30,         # seconds between nvidia-smi checks

    # --- display ------------------------------------------------------------
    "tile_w": 640,                # each camera tile is scaled to this width
}
