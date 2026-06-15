# Yolo_Monitor — Maison CCTV behavior monitor

Camera-side of the Maison CCTV system (Python, Windows). Reads the shop's NVR
over RTSP, detects people per camera, and runs staff/customer behavior rules
(sleeping, phone use, posture notes, greeting, room tidiness, floor objects)
plus face-based staff identity. Integrates with the maisonPOS system for
customer arrival detection (see `contracts/cctv-api.v1.md`).

> **Companion repo:** `maisonPOS` owns the POS + Cloud Functions. The API
> contract between the two lives in `contracts/cctv-api.v1.md` (vendored copy;
> the source of truth is the POS repo — never edit the camera copy alone).

---

## What is NOT in this repo (must be added per machine)

Three things are deliberately git-ignored and must be supplied out-of-band:

| What | Why excluded | How to get it |
|------|--------------|---------------|
| **Models** (`*.pt`, `*.onnx`, `*.task`) | hundreds of MB | shared drive / release asset (see list below) |
| **`local_settings.py`** | NVR password + POS api key | copy `local_settings.example.py` → `local_settings.py`, fill in |
| **`faces/`, `staff.json`, `customers.json`, `*.npz`** | biometric (PDPA) — must never reach a cloud repo | enroll on the machine, or copy via a secure channel (not git) |

Required model files (place in the repo root):
```
yolo11m.pt          yolo11x-pose.pt     yolo11s-pose.pt
face_detection_yunet.onnx               face_recognition_sface.onnx
face_landmarker.task
```

---

## First-time setup (new machine)

```powershell
# 1. clone
git clone https://github.com/bluequantum/maison-cctv.git
cd maison-cctv

# 2. Python deps
pip install ultralytics opencv-python mediapipe torch numpy websockets

# 3. secrets — copy the template and fill in the real NVR URL
copy local_settings.example.py local_settings.py
notepad local_settings.py        # set NVR_URL (and POS_API_KEY later)

# 4. drop the model files (from shared drive) into the repo root

# 5. run
python main.py
```

In the window: left click / arrow keys switch camera, `q` quits.

---

## Configuration

Everything tunable is in `config.py` (one `CONFIG` dict; modules read from it,
never hard-code). Highlights:

- **`cameras`** — `(name, NVR channel)`. The NVR reshuffles channels on
  occasion; if room names stop matching, run `python nvr_probe.py` to snapshot
  each channel and re-map. Rules below are keyed by camera **name**, so only
  this list moves.
- **`watch_only`** — cameras shown but never analyzed (e.g. `street`).
- **`presence_cameras`** — staff-only rooms (e.g. `office`): everyone logged as
  STAFF on enter/leave, no penalties; faces auto-enrolled here for use elsewhere.
- **`service_zones`**, **`greeting_cameras`**, **`tidy_cameras`**,
  **`floor_watch`** — per-camera rule areas.
- Thresholds for sleep / phone / posture / face matching — all documented inline.

Secrets (`NVR_URL`, `POS_API_KEY`) come from `local_settings.py` (or env vars),
never from `config.py` itself.

---

## Face enrollment tools

- `enroll_face.py` — enroll a staff face from a camera or saved images.
- `merge_faces.py <keep_id> <dup_id>...` — merge duplicate ids of one person
  (CCTV faces of the same person sometimes enroll twice; merging pools angles
  and improves recognition).
- `staff.json` maps face id → real name + POS therapist id (hand-editable).
- Office (presence) cameras auto-enroll staff faces; set names afterward.

> **PDPA:** face embeddings are biometric personal data. Get consent before
> enrolling; never commit `faces/`, `*.npz`, or the registry JSONs.

---

## POS integration (arrival detection)

See `contracts/cctv-api.v1.md`. The camera will `GET /cctvBookings` (who is
expected) and `POST /cctvArrival` (someone arrived/left the front door), only
for `customer`/`unknown` people — never staff. Wiring (`booking_sync.py`,
arrival POST in `tracker.py`, `pos_api` config) is added once the POS Cloud
Functions are deployed and the team shares the base URL + `x-cctv-key`.

---

## Module map

| File | Role |
|------|------|
| `main.py` | entry point: grabbers, per-camera loop, window, GPU-temp guard, restart-on-CUDA-crash |
| `detector.py` | YOLO person + phone detection, ByteTrack, in-hand-phone zoom pass |
| `sleep_analyzer.py` | pose + eyes (PERCLOS) → active/drowsy/sleeping |
| `posture.py` | keypoint geometry: posture class, imbalance metrics |
| `person_labeler.py` | staff/customer by uniform color + enrolled-face match |
| `face_enroller.py` | auto-enroll staff faces in presence rooms |
| `tracker.py` | per-camera orchestration: roles, rules, events, evidence |
| `room_tidy.py`, `floor_watch.py` | room-state penalty rules |
| `overlay.py` | drawing + timeline strip |
| `timeline_logger.py` | events.jsonl + per-camera .txt + WebSocket + evidence images |
| `config.py` | all tunables |
| `nvr_probe.py` | snapshot each NVR channel to re-map cameras |
