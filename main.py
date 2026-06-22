# Spa monitor v3: everything behavior_monitor_v2 does (staff/customer by
# uniform, sleep via posture+stillness+eyes, phone use, posture-imbalance
# notes, GPU survival) inside the modular package, plus ByteTrack tracking,
# enter/leave detection, and a per-camera event timeline written to
# events.jsonl and broadcast over a WebSocket.
#
# Run:   python spa_monitor/main.py            (uses the spa's RTSP cameras)
# or import and call main([...]) with any list of cv2-compatible sources
# (RTSP URLs, video files, or webcam indices).
# The window shows ONE camera; left click / right arrow = next camera,
# left arrow = previous. ALL cameras keep being analyzed off-screen.
# Press 'q' to quit.
import os
import subprocess
import sys
import threading
import time

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CONFIG, GPU
from detector import PersonDetector
from face_enroller import AutoEnroller
from floor_watch import FloorWatch
from overlay import compose, draw_people, draw_phones
from person_labeler import FaceMatcher
from pos_timeline import PushWorker, start_image_server
from room_tidy import TidyMonitor
from sleep_analyzer import EyeScorer, PoseEstimator
from timeline_logger import TimelineLogger
from tracker import TrackManager

WINDOW = "Spa monitor v3 (click / arrows = switch camera, q = quit)"
KEY_LEFT, KEY_RIGHT = 2424832, 2555904  # waitKeyEx codes on Windows

switch_request = 0  # +1 = next camera, -1 = previous; set by mouse/keyboard


def on_mouse(event, x, y, flags, param):
    global switch_request
    if event == cv2.EVENT_LBUTTONDOWN:
        switch_request = 1


class Grabber(threading.Thread):
    """Reads one stream non-stop, keeping only the freshest frame (RTSP backs
    up and stalls if frames aren't consumed at stream speed)."""

    def __init__(self, source):
        super().__init__(daemon=True)
        self.source = source
        self.lock = threading.Lock()
        self.frame = None
        self.ts = 0.0

    def run(self):
        while True:
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                cap.release()
                time.sleep(5)
                continue
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                with self.lock:
                    self.frame, self.ts = f, time.time()
            cap.release()
            time.sleep(2)

    def latest(self):
        with self.lock:
            return (self.frame.copy() if self.frame is not None else None), self.ts


gpu_hot = threading.Event()


def temp_watch():
    """Poll the GPU temperature; pause analysis while it's running hot.
    The driver on this PC crashes under sustained load -- backing off
    before it overheats is cheaper than a BSOD mid-shift."""
    while True:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=10)
            t = int(out.decode().strip().splitlines()[0])
            if t >= CONFIG["gpu_temp_pause"] and not gpu_hot.is_set():
                print(f"GPU {t}C >= {CONFIG['gpu_temp_pause']}C -> pausing analysis", flush=True)
                gpu_hot.set()
            elif t <= CONFIG["gpu_temp_resume"] and gpu_hot.is_set():
                print(f"GPU cooled to {t}C -> resuming analysis", flush=True)
                gpu_hot.clear()
        except Exception:
            pass  # nvidia-smi hiccup -- keep the last known state
        time.sleep(CONFIG["gpu_poll_every"])


def offline_tile(camera_id, w):
    tile = np.full((int(w * 9 / 16), w, 3), 40, np.uint8)
    cv2.putText(tile, f"{camera_id}: offline", (20, tile.shape[0] // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 230), 2, cv2.LINE_AA)
    return tile


def _camera_url(cam):
    """RTSP URL for one camera-table row; substream rows get the NVR's
    low-res second stream (&stream=1)."""
    url = CONFIG["nvr_url"].format(ch=cam["ch"])
    if cam.get("stream") == "sub":
        url += "&stream=1"
    return url


def default_sources():
    """(name, RTSP URL) for the ACTIVE cameras only -- inactive rows are not
    pulled or analysed. Streams come from the NVR (one box, one channel each)."""
    return [(c["name"], _camera_url(c))
            for c in CONFIG["cameras"] if c.get("active", True)]


def main(sources):
    """sources: list of (name, source) pairs, or plain sources (RTSP URL /
    file / webcam index) which get auto names CAM_01, CAM_02, ..."""
    cams = [s if isinstance(s, (list, tuple)) else (f"CAM_{i + 1:02d}", s)
            for i, s in enumerate(sources)]
    cam_ids = [name for name, _ in cams]
    logger = TimelineLogger(CONFIG)
    # timeline history server + POS push (no-ops if disabled / no store)
    if logger.store is not None:
        image_base = start_image_server(CONFIG)
        PushWorker(logger.store, CONFIG, image_base).start()
    pose = PoseEstimator(CONFIG)   # stateless -> shared
    eyes = EyeScorer(CONFIG)       # stateless -> shared
    faces = FaceMatcher(CONFIG)    # enrolled staff faces -> shared
    detectors = {cid: PersonDetector(CONFIG) for cid in cam_ids}  # per-camera
    # auto-enroller for each presence (staff-only) room, sharing the live
    # face matcher so new faces are recognized everywhere at once
    enrollers = {cid: AutoEnroller(CONFIG, faces, logger, cid)
                 for cid in cam_ids if cid in CONFIG.get("presence_cameras", [])}
    trackers = {cid: TrackManager(cid, CONFIG, logger, eyes, faces,
                                  enrollers.get(cid)) for cid in cam_ids}
    tidies = {cid: TidyMonitor(cid, CONFIG, logger) for cid in cam_ids}
    floors = {cid: FloorWatch(cid, CONFIG, logger) for cid in cam_ids}
    grabbers = {cid: Grabber(src) for cid, src in cams}
    for g in grabbers.values():
        g.start()
    if GPU:
        threading.Thread(target=temp_watch, daemon=True).start()

    global switch_request
    views = {cid: offline_tile(cid, CONFIG["tile_w"]) for cid in cam_ids}
    interval = 1.0 / CONFIG["sample_fps"] / max(1, len(cam_ids))
    next_t = 0.0
    cam_i = 0      # analysis round-robin index (all cameras, even off-screen)
    view_i = 0     # which camera is shown on screen (user-controlled)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    cv2.resizeWindow(WINDOW, 1024, 768)
    cv2.setMouseCallback(WINDOW, on_mouse)
    print(f"watching {len(cam_ids)} cameras (alerts fire for all of them, even "
          f"off-screen). Sleep/phone alerts: STAFF only (beige uniform). "
          f"Posture notes: customers only. Events -> {CONFIG['events_path']} + "
          f"ws://{CONFIG['ws_host']}:{CONFIG['ws_port']}. "
          f"Evidence -> {CONFIG['evidence_dir']}", flush=True)

    while True:
        key = cv2.waitKeyEx(15)
        if key in (ord("q"), ord("Q")):
            return
        elif key == KEY_RIGHT:
            switch_request = 1
        elif key == KEY_LEFT:
            switch_request = -1
        if switch_request:
            view_i = (view_i + switch_request) % len(cam_ids)
            switch_request = 0
        now = time.time()

        if gpu_hot.is_set():
            # too hot: show the last view with a notice, analyze nothing
            view = views[cam_ids[view_i]].copy()
            cv2.putText(view, "GPU hot - analysis paused", (12, 64),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(view, "GPU hot - analysis paused", (12, 64),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 80, 255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, view)
            continue

        if now >= next_t:
            next_t = now + interval
            cid = cam_ids[cam_i]
            cam_i = (cam_i + 1) % len(cam_ids)
            frame, ts = grabbers[cid].latest()
            if frame is None or now - ts > 15:
                views[cid] = offline_tile(cid, CONFIG["tile_w"])
            else:
                detections, phones = detectors[cid].detect(frame)
                # watch-only and presence cameras never use keypoints --
                # skip the (heavy) pose model entirely for them
                tm = trackers[cid]
                poses = (pose.estimate(frame) if detections
                         and not tm.watch_only and not tm.presence else [])
                people = trackers[cid].update(now, frame, detections, poses, phones)
                tidies[cid].update(now, frame, len(detections))
                floors[cid].update(now, frame, len(detections))
                draw_people(frame, people)
                draw_phones(frame, phones)
                views[cid] = compose(frame, cid, logger.tail(cid),
                                     CONFIG["timeline_events"])

        cv2.imshow(WINDOW, views[cam_ids[view_i]])


if __name__ == "__main__":
    try:
        main(sys.argv[1:] or default_sources())
    except Exception as e:
        # a CUDA/driver crash (nvlddmkm reset) poisons this process's GPU
        # context for good -- the only recovery is a fresh process. Anything
        # else is a real bug and should still crash loudly.
        if "cuda" not in repr(e).lower():
            raise
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] GPU/CUDA error: {e}", flush=True)
        print("restarting in 20s (waiting out the driver reset)...", flush=True)
        cv2.destroyAllWindows()
        time.sleep(20)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    cv2.destroyAllWindows()
