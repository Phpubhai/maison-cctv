# Enroll a staff face for spa_monitor from a live camera.
#
#   python enroll_face.py scan <ip>            grab a few frames, save every
#                                              detected face as face_cand_*.jpg
#   python enroll_face.py save <name> <ids..>  store the chosen candidates'
#                                              embeddings as spa_monitor/faces/<name>.npz
#
# Workflow: scan -> look at the face_cand_*.jpg files -> save the ids that
# show the right person (several angles = more robust matching).
import json
import os
import sys
import time
import urllib.parse

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;8000000"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DET_MODEL = os.path.join(ROOT, "face_detection_yunet.onnx")
REC_MODEL = os.path.join(ROOT, "face_recognition_sface.onnx")
FACES_DIR = os.path.join(ROOT, "faces")
SCAN_FILE = os.path.join(ROOT, "face_scan.npz")
FRAMES = 5          # frames to grab in scan mode
FRAME_GAP = 1.2     # seconds between them
MIN_FACE = 24       # ignore faces smaller than this many pixels


def grab_frames(ip):
    cfg = r"C:\Program Files\Agent\Media\XML\objects.json"
    with open(cfg, encoding="utf-8-sig") as f:
        cams = json.load(f)["cameras"]
    creds = next(c["settings"] for c in cams if c["settings"].get("login"))
    pw = urllib.parse.quote(creds["password"], safe="")
    host = ip if ":" in ip else f"{ip}:554"
    cap = cv2.VideoCapture(f"rtsp://{creds['login']}:{pw}@{host}/stream2", cv2.CAP_FFMPEG)
    frames, last = [], 0.0
    t_end = time.time() + FRAMES * FRAME_GAP + 5
    while len(frames) < FRAMES and time.time() < t_end:
        ok, f = cap.read()
        if ok and time.time() - last >= FRAME_GAP:
            frames.append(f)
            last = time.time()
    cap.release()
    return frames


def scan(ip):
    det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), 0.5)
    rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    frames = grab_frames(ip)
    if not frames:
        sys.exit("camera offline / no frames")
    crops, n = [], 0
    for fi, frame in enumerate(frames):
        # CCTV faces are small -- detect on a 2x upscale, then map back
        big2 = cv2.resize(frame, None, fx=2, fy=2)
        det.setInputSize((big2.shape[1], big2.shape[0]))
        ok, faces = det.detect(big2)
        for face in (faces if faces is not None else []):
            face = face.copy()
            face[:14] /= 2  # box + landmarks back to original scale
            x, y, w, h = face[:4]
            if w < MIN_FACE or h < MIN_FACE:
                continue
            aligned = rec.alignCrop(frame, face)
            crops.append(aligned)
            big = cv2.resize(aligned, (224, 224), interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(ROOT, f"face_cand_{n}.jpg"), big)
            print(f"face_cand_{n}.jpg  frame {fi}, at ({int(x)},{int(y)}) size {int(w)}x{int(h)}")
            n += 1
    if not crops:
        sys.exit("no faces detected -- try when the person faces the camera")
    np.savez(SCAN_FILE, crops=np.stack(crops))
    print(f"{n} face(s) saved. Review the jpgs, then run:\n"
          f"  python enroll_face.py save <name> <id> [<id> ...]")


def save(name, ids):
    rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    crops = np.load(SCAN_FILE)["crops"]
    feats = [rec.feature(crops[i]).flatten() for i in ids]
    os.makedirs(FACES_DIR, exist_ok=True)
    out = os.path.join(FACES_DIR, f"{name}.npz")
    if os.path.exists(out):  # extend an existing enrollment
        feats = list(np.load(out)["feats"]) + feats
    np.savez(out, feats=np.stack(feats))
    cv2.imwrite(os.path.join(FACES_DIR, f"{name}.jpg"),
                cv2.resize(crops[ids[0]], (224, 224), interpolation=cv2.INTER_NEAREST))
    print(f"saved {len(feats)} embedding(s) -> {out}")


def watch(ip, secs=180):
    """Watch the stream and keep only GOOD faces (high detection score =
    reasonably frontal). Down-looking heads score low and are skipped."""
    det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), 0.7)
    rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    cfg = r"C:\Program Files\Agent\Media\XML\objects.json"
    with open(cfg, encoding="utf-8-sig") as f:
        cams = json.load(f)["cameras"]
    creds = next(c["settings"] for c in cams if c["settings"].get("login"))
    pw = urllib.parse.quote(creds["password"], safe="")
    host = ip if ":" in ip else f"{ip}:554"
    cap = cv2.VideoCapture(f"rtsp://{creds['login']}:{pw}@{host}/stream2", cv2.CAP_FFMPEG)

    crops, n, last = [], 0, 0.0
    t_end = time.time() + secs
    while time.time() < t_end and n < 8:
        ok, frame = cap.read()
        if not ok or time.time() - last < 1.5:
            continue
        last = time.time()
        big2 = cv2.resize(frame, None, fx=2, fy=2)
        det.setInputSize((big2.shape[1], big2.shape[0]))
        ok, faces = det.detect(big2)
        for face in (faces if faces is not None else []):
            face = face.copy()
            face[:14] /= 2
            x, y, w, h = face[:4]
            if w < MIN_FACE or h < MIN_FACE:
                continue
            aligned = rec.alignCrop(frame, face)
            crops.append(aligned)
            big = cv2.resize(aligned, (224, 224), interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(ROOT, f"face_cand_{n}.jpg"), big)
            print(f"face_cand_{n}.jpg  score {face[14]:.2f}, at ({int(x)},{int(y)}) "
                  f"size {int(w)}x{int(h)}", flush=True)
            n += 1
    cap.release()
    if not crops:
        sys.exit("no good frontal face seen -- ask the person to glance at the camera")
    np.savez(SCAN_FILE, crops=np.stack(crops))
    print(f"{n} face(s) saved. Review the jpgs, then run:\n"
          f"  python enroll_face.py save <name> <id> [<id> ...]")


def scan_images(paths):
    """Detect faces in saved image files (old screenshots) instead of a
    live camera. Same output/workflow as scan."""
    det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), 0.5)
    rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    crops, n = [], 0
    for path in paths:
        frame = cv2.imread(path)
        if frame is None:
            print(f"cannot read {path}")
            continue
        big2 = cv2.resize(frame, None, fx=2, fy=2)
        det.setInputSize((big2.shape[1], big2.shape[0]))
        ok, faces = det.detect(big2)
        for face in (faces if faces is not None else []):
            face = face.copy()
            face[:14] /= 2
            x, y, w, h = face[:4]
            if w < MIN_FACE or h < MIN_FACE:
                continue
            aligned = rec.alignCrop(frame, face)
            crops.append(aligned)
            big = cv2.resize(aligned, (224, 224), interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(ROOT, f"face_cand_{n}.jpg"), big)
            print(f"face_cand_{n}.jpg  {os.path.basename(path)}  score {face[14]:.2f}, "
                  f"at ({int(x)},{int(y)}) size {int(w)}x{int(h)}")
            n += 1
    if not crops:
        sys.exit("no faces found in the given images")
    np.savez(SCAN_FILE, crops=np.stack(crops))
    print(f"{n} face(s) saved. Review the jpgs, then run:\n"
          f"  python enroll_face.py save <name> <id> [<id> ...]")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "scan":
        scan(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "watch":
        watch(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 180)
    elif len(sys.argv) >= 3 and sys.argv[1] == "scanimg":
        scan_images(sys.argv[2:])
    elif len(sys.argv) >= 4 and sys.argv[1] == "save":
        save(sys.argv[2], [int(v) for v in sys.argv[3:]])
    else:
        sys.exit(__doc__)
