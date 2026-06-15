# Yolo_Monitor — เอกสารส่งต่อ (Handoff) สำหรับ Agent ตัวถัดไป

> เอกสารนี้เขียนให้ agent อีกตัว (หรือคนใหม่) อ่านแล้วเข้าใจระบบทั้งหมดและทำงานต่อได้ทันที
> สแกนโค้ดเมื่อ 2026-06-15 จากโฟลเดอร์ `C:\Users\bluepepperMaison\Desktop\phai\Yolo_Monitor`

---

## 1. ระบบนี้คืออะไร (1 ย่อหน้า)

ระบบ **CCTV staff-behavior monitor** สำหรับร้านสปา/นวด ("Maison") — ดึงภาพจาก NVR หลายกล้องพร้อมกัน, ใช้ YOLO ตรวจจับคน + โทรศัพท์, แยก **พนักงาน (staff) vs ลูกค้า (customer)** ด้วยสีชุดยูนิฟอร์ม + จดจำใบหน้า, แล้ววิเคราะห์พฤติกรรมตามบทบาท:
- **พนักงาน** → จับ "หลับ/ง่วง" (sleep), "เล่นโทรศัพท์" (phone use)
- **ลูกค้า** → ประเมิน "ความไม่สมดุลของร่างกาย" (posture imbalance) เพื่อเป็นบทสนทนาเปิดการขายนวด
- **กฎเพิ่มเติม**: การทักทายลูกค้า (greeting), ความเรียบร้อยของห้อง (room tidy), ของวางบนพื้น (floor object)

ผลลัพธ์ออกเป็น event log (`events.jsonl` + ไฟล์ timeline ต่อกล้อง), ภาพหลักฐาน (evidence/penalty JPG), และ broadcast สดผ่าน WebSocket

นี่คือ **v3** — เป็นการ refactor `behavior_monitor_v2.py` (ไฟล์เดี่ยว 37KB อยู่ใน root โปรเจกต์) ให้เป็น package แบบโมดูล + เพิ่ม ByteTrack tracking, enter/leave detection, timeline ต่อกล้อง, และ WebSocket

---

## 2. วิธีรัน

```powershell
cd C:\Users\bluepepperMaison\Desktop\phai\Yolo_Monitor
python main.py                      # ใช้กล้อง RTSP จาก NVR (default_sources)
python main.py <rtsp/file/webcam>   # หรือป้อน source เอง (auto-name CAM_01..)
```

- หน้าต่างแสดง **ทีละกล้อง**; คลิกซ้าย / ลูกศรขวา = กล้องถัดไป, ลูกศรซ้าย = ก่อนหน้า, `q` = ออก
- **ทุกกล้องถูกวิเคราะห์ตลอด** แม้ไม่ได้แสดงบนจอ (round-robin)
- ต้องมี: `ultralytics` (YOLO), `opencv-python`, `torch`, `mediapipe` (สำหรับตา), `websockets` (optional — ไม่มีก็ยัง log ลงไฟล์ได้)
- โมเดลทั้งหมดอยู่ในโฟลเดอร์นี้: `yolo11m.pt`/`yolo11n.pt` (detect), `yolo11x-pose.pt`/`yolo11s-pose.pt` (pose), `face_landmarker.task` (MediaPipe ตา), `face_detection_yunet.onnx` + `face_recognition_sface.onnx` (จดจำใบหน้า)
- เลือกโมเดลใหญ่/เล็กอัตโนมัติตาม `torch.cuda.is_available()` (GPU → ใหญ่ + imgsz 1280; CPU → เล็ก + imgsz 640)

---

## 3. สถาปัตยกรรม — flow หนึ่งเฟรม

```
NVR (RTSP) ──Grabber(thread/กล้อง, เก็บเฟรมล่าสุด)──┐
                                                    ▼
main.py loop (round-robin ทีละกล้อง ที่ sample_fps/จำนวนกล้อง)
   │
   ├─ PersonDetector.detect()      detector.py   → คน(track_id,box) + โทรศัพท์(box)
   │        └ YOLO track() 1 ครั้ง (person+phone) + zoom-pass 2x หาโทรศัพท์ในมือ
   ├─ PoseEstimator.estimate()     sleep_analyzer.py → keypoints (ข้าม ถ้า watch/presence)
   ├─ TrackManager.update()        tracker.py    ← หัวใจ orchestration
   │     ├ จับคู่ pose↔คน ด้วย IoU
   │     ├ _phone_holders(): โทรศัพท์เป็นของใคร (ข้อมือใกล้สุด)
   │     ├ RoleVoter.update()  person_labeler.py → staff/customer/None (ยูนิฟอร์ม+หน้า)
   │     ├ staff   → SleepAnalyzer + phone dwell timer
   │     ├ customer→ posture imbalance (ตอนยืน)
   │     └ greeting rule (กล้อง front door)
   ├─ TidyMonitor.update()         room_tidy.py  → ROOM MESSY (ตอนห้องว่าง)
   ├─ FloorWatch.update()          floor_watch.py→ OBJECT ON FLOOR (ยังไม่ผูกกล้อง)
   └─ overlay.draw_* + compose()   overlay.py    → วาดกล่อง + timeline strip
            │
            └ ทุก event → TimelineLogger  timeline_logger.py
                          → events.jsonl + timelines/<cam>.txt + WebSocket + evidence JPG
```

**หลักการออกแบบสำคัญ:** ทุกค่าปรับได้อยู่ใน `config.py` (`CONFIG` dict) ที่เดียว — โมดูลอ่านจากตรงนี้เท่านั้น ห้าม hard-code ค่าในโมดูล

---

## 4. ไฟล์แต่ละตัวทำอะไร

### โค้ดหลัก (production package)
| ไฟล์ | หน้าที่ |
|---|---|
| `config.py` | **ค่าปรับทั้งหมด** (`CONFIG`) + map กล้อง/ช่อง NVR + เลือกโมเดลตาม GPU |
| `main.py` | orchestration loop, Grabber thread/กล้อง, GPU temp watchdog, UI สลับกล้อง, auto-restart เมื่อ CUDA crash |
| `detector.py` | `PersonDetector` — YOLO track คน+โทรศัพท์ + zoom-pass 2x หาโทรศัพท์ในมือ |
| `sleep_analyzer.py` | `PoseEstimator` (pose), `EyeScorer` (MediaPipe PERCLOS), `SleepAnalyzer` (state machine active→drowsy→sleeping) |
| `posture.py` | เรขาคณิต keypoint: `classify_posture` (ยืน/นั่ง/นอน/ก้ม...), `imbalance_metrics`, `describe_causes` |
| `person_labeler.py` | **จุดเดียวที่ระบบรู้ว่าใครเป็นใคร**: `uniform_verdict` (สีชุด HSV), `FaceMatcher` (YuNet+SFace), `RoleVoter` (โหวต sticky), staff registry |
| `tracker.py` | `TrackManager` — registry คนต่อกล้อง, route วิเคราะห์ตามบทบาท, enter/leave, greeting, phone ownership |
| `timeline_logger.py` | `TimelineLogger` — เขียน events.jsonl + .txt ต่อกล้อง + WebSocket + บันทึกภาพหลักฐาน |
| `face_enroller.py` | `AutoEnroller` — เก็บใบหน้าพนักงานอัตโนมัติจากห้อง office (presence room) |
| `room_tidy.py` | `TidyMonitor` — เทียบห้องว่างกับภาพอ้างอิง → ROOM MESSY |
| `floor_watch.py` | `FloorWatch` — หาวัตถุบนพื้น (YOLO + diff ภาพอ้างอิง) → OBJECT ON FLOOR |
| `overlay.py` | วาดอย่างเดียว: กล่องคน/โทรศัพท์ + timeline strip |
| `staff.json` | registry: face_id → {name, therapist_id, source} (แก้มือได้) |

### เครื่องมือ/สคริปต์ช่วย (ไม่ใช่ production loop)
| ไฟล์ | ใช้เมื่อ |
|---|---|
| `nvr_probe.py` | จับ snapshot ทุกช่อง NVR เพื่อ map channel→ชื่อกล้อง (ทำใหม่เมื่อ NVR สลับช่อง) |
| `uniform_calib.py` | calibrate ช่วงสี HSV ของยูนิฟอร์มจากเฟรมจริง (ใช้เมื่อเพิ่ม uniform set ใหม่) |
| `enroll_face.py` | enroll หน้าพนักงานแบบ manual (`scan` → ดูไฟล์ → `save <name> <ids>`) |
| `merge_faces.py` | รวม face id ซ้ำของคนเดียวกัน (`python merge_faces.py <keep> <dup..>`) |
| `face_crosscheck.mjs` | (รันบนเครื่องที่มี repo maisonPOS) เทียบหน้า CCTV กับ face profile ของ POS |
| `phone_probe.py` | วินิจฉัยการตรวจจับโทรศัพท์ในมือ (เทียบ full-frame vs 2x crop) |
| `smoke_test.py` / `episode_test.py` / `greeting_test.py` / `tidy_test.py` / `floor_test.py` | unit/offline tests (ไม่ต้องมีกล้อง) |
| `phone_live_test.py` / `face_live_test.py` / `fps_bench.py` | live tests / benchmark (ต้องมีกล้อง) |

---

## 5. กล้อง & การจัดประเภทกล้อง (config.py)

```python
"cameras": [
    ("front door", 3),   # เคาน์เตอร์แคชเชียร์ + เก้าอี้ทำเล็บ  1280x720
    ("reception", 1),    # เลานจ์  1920x1080
    ("foot spa", 2),     # ทางเดินสปาเท้า  2304x1296
    ("office", 4),       # หลังร้าน/เวิร์กชอป  (watch-only ในแง่ penalty แต่เป็น presence)
    ("street", 5),       # นอกร้าน  1280x720
    ("makeup room", 6),  # ⚠ ยังไม่อยู่บน NVR → แสดง offline; อัปเดต channel เมื่อกล้องกลับมา
],
```
⚠ **สำคัญ:** ช่อง NVR ถูก verify ใหม่ 2026-06-15 (NVR สลับช่องทั้งหมด) — กฎทั้งหมด key ด้วย **ชื่อกล้อง** ไม่ใช่เลขช่อง ดังนั้นถ้า NVR สลับอีก แก้แค่ตรง map นี้

**ประเภทกล้องพิเศษ:**
- `watch_only: ["street"]` — พื้นที่สาธารณะ: แสดงกล่องคนเฉยๆ ไม่วิเคราะห์/ไม่ถ่ายภาพ/ไม่ลง event (กันการวิเคราะห์คนเดินผ่าน)
- `presence_cameras: ["office"]` — ห้องพนักงานล้วน: ทุกคน = STAFF, log enter/leave เฉยๆ ไม่มี penalty + เป็นแหล่ง **auto-enroll ใบหน้า**

---

## 6. Logic สำคัญที่ต้องเข้าใจก่อนแก้

### 6.1 การแยก staff/customer (`person_labeler.py`)
- **2 สัญญาณ**: (1) สีชุดยูนิฟอร์ม HSV "therapist beige" — ครอบทั้งชุดชาย/หญิง; (2) ใบหน้าที่ enroll แล้ว
- ตรวจ **torso + ขาท่อนบน** ต้องเข้าทั้งคู่; ถ้าสะโพก/เข่าถูกบัง (นั่งหลังเคาน์เตอร์) ใช้ fallback เฉพาะหน้าอก (เข้มกว่า)
- **เสื้อขาว/เทา อ่านเป็นสีฟ้า** บนกล้องพวกนี้ → อยู่นอก range = customer
- `RoleVoter` โหวตแบบ **sticky majority** (ตัดสินแล้วไม่เปลี่ยน) — กันคนที่นั่งสลัมพ์จนไม่เห็นสะโพกแล้วหลุด label
- **ใบหน้าชนะทุกอย่าง**: match หน้า → STAFF ทันที และ override "customer" จากสีชุดได้ (พนักงานใส่ชุดนอกเครื่องแบบ)
- `face_match_cosine: 0.50` (ค่าตำรา 0.363 แต่ที่ความละเอียด CCTV คนละคนวัดได้ 0.448 → ตั้ง 0.50 + margin 0.10 เหนืออันดับ 2)

### 6.2 การจับ "หลับ" (`sleep_analyzer.py`) — วัดจากเวลา ไม่ใช่เฟรมเดียว
หลักฐาน = **นิ่ง (over rolling window)** AND อย่างน้อยหนึ่งใน: หัวตก / นอน / หน้าซุกแขน / ตาปิด (PERCLOS) — **ตาเปิดชัด = veto**
- หลักฐานต่อเนื่อง ≥ `drowsy_seconds`(15s) → DROWSY; ≥ `sleep_seconds`(180s) → SLEEPING
- การมองโต๊ะแวบเดียวไม่ผ่านขั้น "active"
- PERCLOS ใช้ MediaPipe FaceLandmarker; เฟรมที่หน้าก้มลงมาก (`pitch_down_deg -25`) ถูกทิ้งเพราะ blink score เพี้ยน

### 6.3 การจับ "เล่นโทรศัพท์" (staff เท่านั้น) — มีรายละเอียดเยอะที่สุด
- โทรศัพท์ในมือ **เกือบมองไม่เห็นบน full-frame** (conf ~0.17) → จึงทำ **zoom-pass 2x** บนกล่องแต่ละคน (`detector.py`) ได้ conf ~0.27-0.76
- **เจ้าของโทรศัพท์ = ข้อมือใกล้สุด** (`_phone_holders` ใน tracker.py) ภายใน `phone_wrist_frac`(0.20)×ความสูงกล่อง — กันกรณีพนักงานนวดลูกค้าที่เล่นมือถือตัวเอง (ข้อมือลูกค้าติดมือถือ → ลูกค้าเป็นเจ้าของ ไม่ใช่พนักงาน)
- โทรศัพท์ใน **service_zones** (เก้าอี้ทำเล็บ) = ของลูกค้าเสมอ ไม่นับ
- detection กระพริบ → `phone_grace`(18s) ทำให้ timer ไม่ตัดทันทีระหว่างเฟรมที่หาไม่เจอ
- ค้างนานถึง `phone_secs`(45s) → PHONE USE alert

### 6.4 Posture imbalance (customer เท่านั้น, ตอนยืน) — `posture.py`
- เก็บ median ของมุมเอียง (ไหล่/สะโพก/หัว/เอน) ใน `imb_window`(60s) → แปลเป็น "ข้อความบทสนทนานวด" (`describe_causes`)
- **เน้น: เป็นการคาดเดา ไม่ใช่การวินิจฉัย** — ข้อความเขียนเป็น "possible causes... (estimate only, not a diagnosis)"

### 6.5 กฎเสริม
- **Greeting** (`front door`): ลูกค้าใหม่เข้า → ต้องมี staff **ยืน** ภายใน `greeting_secs`(30s) ไม่งั้น GREETING MISSED; ลูกค้าใหม่ที่ปรากฏใน service zone = ลูกค้าเดิมที่ track หลุดแล้ว track ใหม่ → ไม่นับเป็นการมาถึง
- **Room tidy** (`makeup room`): เทียบกับ `tidy_ref_makeup_room.jpg` เฉพาะตอนห้องว่าง ≥180s, ต่างจากอ้างอิง ≥300s → ROOM MESSY
- **Floor watch**: ⚠ **ยังไม่ผูกกล้อง** (`floor_watch: {}`) — โค้ดพร้อม แต่ต้องเพิ่ม entry + (optional) ภาพพื้นสะอาดอ้างอิง

### 6.6 Auto-enrollment (`face_enroller.py`)
- ในห้อง office (presence) ทุกคน = staff → เก็บหน้าหน้าตรงคมชัดอัตโนมัติเป็น `staff_NN` (name ว่าง) ใน `staff.json` แล้วใช้จดจำในห้องอื่นทันที (ไม่ต้อง restart)
- gate เข้ม: ต้องคะแนนสูง + หลาย sample สม่ำเสมอ + ไม่ match คนที่ enroll แล้ว
- ⚠ ถ้ามี 2 หน้าในกล่องคนเดียว → ไม่ enroll (เคยทำ staff_03 ปนเปื้อนเมื่อ 2026-06-15)

---

## 7. Output formats

### events.jsonl (1 JSON/บรรทัด, append-only)
```json
{"timestamp":"2026-06-12 16:57:16","camera_id":"reception","label":"Phai",
 "event":"PHONE USE","description":"phone in hand, started 16:56:31 (45s so far)",
 "severity":"alert","therapist_id":null}
```
- `severity`: `normal` | `warning` | `alert`
- `therapist_id`: คีย์ join กับระบบ POS (null เมื่อยังไม่รู้) — มาจาก `staff.json`
- `event` ที่เป็น **penalty** (`SLEEPING, PHONE USE, GREETING MISSED, ROOM MESSY, OBJECT ON FLOOR`) → ภาพไปโฟลเดอร์ `Penalty/`; event อื่นไปโฟลเดอร์ `behavior_events/`
- มี event คู่ start/end เสมอ: `SLEEPING`/`SLEEPING END`, `PHONE USE`/`PHONE USE END`, `ROOM MESSY`/`ROOM TIDY`, `OBJECT ON FLOOR`/`FLOOR CLEAR`
- `STAFF RECOGNIZED` / `STAFF ENROLLED` = แก้ไขย้อนหลังเมื่อจำหน้าได้หลังจาก ENTER ไปแล้ว

### ไฟล์อื่น
- `timelines/<camera>_timeline.txt` — อ่านง่ายต่อกล้อง (เปิดด้วย Notepad)
- WebSocket `ws://127.0.0.1:8765` — broadcast แต่ละ event สด (เชื่อมด้วย `websocat`)
- ภาพหลักฐาน: ชื่อ `<timestamp>_<cam>_<label>_<event>[_<dur>s].jpg` กล่องสีแดง + ข้อความระยะเวลา

---

## 8. ลักษณะเฉพาะของเครื่อง/สภาพแวดล้อม (gotchas)

1. **GPU ของเครื่องนี้ crash ภายใต้โหลดนาน** → มี watchdog ใน `main.py`:
   - `temp_watch()` poll `nvidia-smi` ทุก 30s, หยุดวิเคราะห์ที่ ≥80°C, ทำต่อที่ ≤70°C
   - ถ้า CUDA crash จริง (`nvlddmkm reset`) → process จะ `os.execv` รีสตาร์ทตัวเองหลังรอ 20s (เพราะ GPU context พังถาวร แก้ได้ด้วย process ใหม่เท่านั้น)
2. **RTSP backs up** → ใช้ `Grabber` thread/กล้อง เก็บเฉพาะเฟรมล่าสุด; เฟรมเก่ากว่า 15s = ถือว่า offline
3. **Windows**: `:` ใช้ในชื่อไฟล์ไม่ได้ → แทนด้วย `_`; waitKeyEx codes ของลูกศรเป็นเลข Windows-specific (`2424832/2555904`)
4. ทุก source ดึงจาก **NVR เดียว** (`rtsp://192.168.1.70:554/...&channel={ch}`) ไม่ต่อกล้องตรงแล้ว

---

## 9. การเชื่อมกับ POS (สำคัญต่อทิศทางอนาคต)

ดูไฟล์ `../2026-06-12-cctv-export-spec.md` (อยู่ใน root โปรเจกต์):
- ระบบ **maisonPOS** มีข้อมูล face embedding + uniform model ของพนักงานอยู่แล้ว (export ด้วย `export-cctv-validation-data.mjs`)
- ⚠ **embeddings ผูกกับโมเดล**: face vectors ของ POS สร้างด้วย `@vladmandic/human` (รันบน Node เท่านั้น ไม่รันบน Python) — **ใช้กับ pipeline Python นี้ตรงๆ ไม่ได้** ต้อง re-enroll ด้วยโมเดลของเราเอง (ซึ่งระบบนี้ทำอยู่แล้วด้วย YuNet+SFace)
- uniform vectors ของ POS ถ่ายจาก webcam ระยะใกล้ → domain ต่างจาก CCTV → ใช้เป็น bootstrap ได้ ควร retrain จากเฟรม CCTV จริง
- `therapist_id` ใน `staff.json` คือคีย์ join กลับไป POS — ตอนนี้ยังว่างทุกคน (ต้องเติมมือ หรือใช้ `face_crosscheck.mjs` จับคู่)
- ⚠ **PDPA**: face embedding เป็นข้อมูลชีวมาตร — ตรวจ consent ก่อนใช้ข้ามระบบ, อย่า commit bundle ลง repo

---

## 10. งานที่ค้าง / จุดที่น่าทำต่อ (สำหรับ agent ตัวถัดไป)

- [ ] **`makeup room` (ch 6) ยังไม่อยู่บน NVR** → อัปเดต channel ใน `config.py` เมื่อกล้องกลับมา (ตอนนี้ขึ้น offline)
- [ ] **Floor watch ยังไม่ผูกกล้อง** (`floor_watch: {}`) — เพิ่ม entry + ภาพพื้นสะอาดอ้างอิงเมื่อมีกล้องที่เหมาะ
- [ ] **`therapist_id` ใน `staff.json` ว่างหมด** — เติมเพื่อ join กับ POS (staff_06 ยังไม่มี name ด้วย)
- [ ] **staff_03 เคยปนเปื้อน** จาก 2 หน้าในกล่องเดียว (2026-06-15) — มี guard แล้วแต่ตรวจสอบ faces/ ว่าสะอาด
- [ ] เพิ่ม uniform set ใหม่ (เช่น "reception") → ใช้ `uniform_calib.py` calibrate แล้วเพิ่มใน `CONFIG["uniform_sets"]`
- [ ] ค่า threshold ทุกตัวมี comment บอกว่าวัดมาจากวันไหน/อย่างไร — ถ้า re-tune ให้ update comment ด้วย

---

## 11. แผนที่ config (อ้างอิงเร็ว)

กลุ่มค่าใน `CONFIG` (config.py): `cameras/watch_only/presence` · `detection` (model, conf 0.4, imgsz, sample_fps 4) · `sleep` (drowsy 15s, sleep 180s, movement window/tolerance) · `eye` (PERCLOS) · `phone` (secs 45, conf 0.18, grace 18, crop_conf 0.25, wrist_frac 0.20, service_zones) · `posture imbalance` (customers) · `uniform_sets` (HSV) + role voting · `faces` (cosine 0.50, margin 0.10) · `auto_enroll` · `greeting` (30s) · `tidy` · `floor` · `tracking` (min_visible 1s, track_grace 15s, re_alert 300s) · `output paths` · `gpu temp` (80/70°C) · `display`

---

*ปรับแก้พฤติกรรมที่ `config.py` ก่อนเสมอ — โมดูลถูกออกแบบให้อ่านค่าจากที่นั่นที่เดียว*
