# Presence Timeline — ดีไซน์ปฏิรูป Yolo_Monitor เป็นเครื่องมือสื่อสารตำแหน่ง therapist

> **Status:** approved design (brainstorm), ready for implementation plan.
> **Date:** 2026-06-22 · **Owner:** Yolo_Monitor (camera) + maisonPOS (UI/cloud).
> **เกี่ยวข้อง:** `contracts/cctv-api.v1.md`, `contracts/cctv-timeline.v1.md`,
> `HANDOFF.md`. spec นี้ต่อยอด ไม่รื้อ pipeline เดิม.

---

## 1. เป้าหมาย & การเปลี่ยนทิศ

ระบบเดิมเป็น **CCTV behavior monitor** เน้นจับ "พฤติกรรมผิด" (หลับ/เล่นมือถือ/
ท่าทาง/การทักทาย/ห้องรก/ของบนพื้น) เป็น penalty แล้ว push ขึ้น POS เป็นตาราง
เหตุการณ์แบน ๆ (`cctvTimeline`).

ทิศใหม่: ระบบนี้คือ **เครื่องมือสื่อสารระหว่าง reception กับ therapist** เพื่อดูว่า
**ใครอยู่ห้องไหน กำลังทำอะไร** — ไม่ใช่ระบบ security. ผลลัพธ์หลักคือ
**presence/location** แสดงเป็น 2 หน้าใน POS: บอร์ดสด "ตอนนี้" (กล่องห้อง) และ
"ไทม์ไลน์" (Gantt ห้อง×เวลา ปักหมุด therapist).

**การตัดสินใจหลัก (จาก brainstorm):**
- penalty เดิม **เก็บไว้รันเบื้องหลัง** ให้ manager ใช้ต่อ — ไม่ทิ้ง แต่ไม่ใช่
  product หลักอีกต่อไป.
- มุมมองหลัก = **แท่งเวลา (Gantt)**; therapist แสดงเป็น **DaisyUI avatar group**.
- ต้องการ **"ชื่อห้อง" ไม่ใช่ "ชื่อกล้อง"** — 1 กล้องจับได้หลายห้อง.
- ห้องไม่มีกล้อง (ห้องน้ำ/ซักผ้า) → **เดาจากโซนประตู (threshold)**.
- **ชื่อรายคนคือหัวใจ** — ยอมลงทุนเรื่อง identity (กล้อง anchor + สอนหน้าเอง +
  ตาราง POS + reception แตะแก้).
- ต้อง **เชื่อมกับ POS** (DaisyUI/Vue + Firestore Cloud Functions) เต็มตัว.

---

## 2. หลักการออกแบบ

1. **ต่อชั้น ไม่รื้อ** — detection/track/แยก staff-customer, `room_zones`/ROOM
   ENTER, `event_store` (SQLite), `PushWorker`, image server, contract POS เดิม
   ใช้ต่อได้เกือบหมด.
2. **แยก "กล้อง" (อุปกรณ์) ออกจาก "ห้อง" (สิ่งที่ reception สนใจ)** ด้วย rooms
   registry.
3. **named-person เป็นหัวใจ แต่ degrade graceful** — ยอมโชว์ "ไม่ทราบชื่อ" ดีกว่า
   เดาผิดแบบมั่นใจ. ความแม่นค่อย ๆ ดีขึ้นเมื่อสอนมากขึ้น.
4. **ข้อจำกัดที่ยอมรับ:** therapist ใส่ยูนิฟอร์มเหมือนกันหมด + กล้อง CCTV แยกหน้า
   แทบไม่ออก (same-person median 0.21 / different 0.44 ใน `config.py`) → re-ID
   จากภาพล้วนทำไม่ได้ดี ต้อง anchor identity จากที่อื่น.
5. **ค่าปรับทั้งหมดอยู่ใน `config.py`** ตามแบบเดิม.

---

## 3. สถาปัตยกรรมภาพรวม

```
กล้อง NVR ทุกตัว + ⭐ กล้อง anchor ห้องพนักงาน
        │
ตรวจจับคน + ByteTrack + แยก staff/customer        (pipeline เดิม)
        ├──────────────► Penalty rules (เบื้องหลัง · manager)  ──┐
        ▼                                                        │
Identity Resolver ⭐  ◄── ตาราง/booking POS                      │
   (5 ชั้นความเชื่อมั่น) ◄── เครื่องมือสอนหน้า + face registry      │
                       ◄── reception แตะแก้ (corrections)        │
        ▼                                                        │
Presence Engine: ROOM ENTER/LEAVE → ช่วงเวลาในห้อง + สถานะ        │
        ▼                                                        ▼
SQLite events.db  +  ตาราง presence_intervals (source of truth)
        ▼
Push workers → POS Cloud Functions (Firestore)
        ▼
POS (DaisyUI): หน้า "ตอนนี้" (กล่องห้อง) + หน้า "ไทม์ไลน์" (Gantt)
```

**ของใหม่ที่ต้องสร้าง:** Identity Resolver, Presence Engine, ตาราง
`presence_intervals`, เครื่องมือสอนหน้า (supervised enroll), endpoint
`/cctvPresence` + collection `presence/`, corrections loop, 2 หน้า POS.

---

## 4. Room/Zone model

เพิ่ม **rooms registry** ใน `config.py` (ต่อยอดจาก `room_zones` ที่มีอยู่). ทุก
ห้องมี **ชื่อ (ไทย) + ประเภท + วิธีตรวจจับ + ตำแหน่ง**.

```python
"rooms": {
  "Foot Spa":  {"type": "service", "via": "camera", "camera": "foot spa"},
  "MAISON 1":  {"type": "service", "via": "zone", "camera": "spa room", "zone": (x1,y1,x2,y2)},
  "MAISON 2":  {"type": "service", "via": "zone", "camera": "spa room", "zone": (...)},
  "Reception": {"type": "front",   "via": "camera", "camera": "reception"},
  "เคาน์เตอร์":  {"type": "front",   "via": "zone", "camera": "front door", "zone": (...)},
  "ห้องพัก":     {"type": "rest",    "via": "camera", "camera": "<anchor cam>", "anchor": True},
  "ห้องน้ำ":     {"type": "facility","via": "threshold", "camera": "<cam ที่เห็นประตู>", "door": (...)},
  "ห้องซักผ้า":  {"type": "back",    "via": "threshold", "camera": "<cam>", "door": (...)},
}
```

### 4.1 วิธีตรวจจับ 3 แบบ
- **`via: camera`** — ทั้งเฟรม = 1 ห้อง (กรณีง่าย).
- **`via: zone`** — โซน (rect/poly) ในเฟรม; ใช้ center-in-zone (`_which_room`,
  `_center_in` ที่มีอยู่). 1 กล้อง → หลายห้อง. โซนห้ามทับกัน; ถ้าจำเป็นกำหนด
  priority.
- **`via: threshold`** — โซนประตูสู่ห้องที่ไม่มีกล้อง. เห็น track เข้าโซนประตูแล้ว
  track หายเกิน `track_grace` → ตั้งสถานะ "อยู่ใน \<ห้อง\>" จนกว่าจะโผล่ที่กล้อง
  ใดก็ได้. มี **timeout** (`threshold_timeout`, เช่น 1800s): ถ้าไม่โผล่เกินนี้ →
  เปลี่ยนเป็น "ไม่เห็น/ออกจากร้าน" (เผื่อออกประตูอื่น).

### 4.2 ผลต่อ output
- ทุก event/interval ติด **`room`** (ชื่อห้อง) ไม่ใช่แค่ `camera`.
- Gantt lane = ห้อง; กล้องหนึ่งให้ได้หลาย lane; (เผื่ออนาคต) หลายกล้องชี้ห้อง
  เดียวกันได้.

### 4.3 ต้องการจากผู้ใช้ (calibrate)
- ลิสต์ห้องจริงทั้งหมด + ประเภท.
- กล้องไหนเห็นประตูของห้องที่ไม่มีกล้อง + พิกัดโซนประตู.
- ยืนยันกล้อง/ตำแหน่ง anchor (ห้องพัก ประตูเดียวอยู่หน้าร้าน).

---

## 5. Identity Resolver (หัวใจ)

รับ track ที่ถูก label เป็น staff แล้วตัดสินว่าเป็น therapist คนไหน + ระดับความ
เชื่อมั่น (confidence).

### 5.1 Loop สอนหน้า (แทน `auto_enroll` เดิมที่ปิดไป)
กล้อง anchor (ห้องพัก, ประตูเดียวอยู่หน้าร้าน, therapist ส่วนใหญ่เข้าก่อนเริ่ม
งาน) เก็บ snapshot หน้าต่อเนื่อง → เข้าคิว → **เครื่องมือ label โดยมนุษย์** (ผู้ใช้
บอกว่าใครเป็นใคร) → ลง `face registry` (`staff.json` + embeddings).

> แก้ปัญหาเดิมที่ auto_enroll สร้าง id ซ้ำ 19 อันจากคนจริง ~5 คน เพราะคราวนี้มี
> "คนตัดสิน" (human-in-the-loop). เครื่องมืออาจต่อยอดจาก `enroll_face.py` /
> `merge_faces.py`.

### 5.2 ลำดับความเชื่อมั่น (สูง → ต่ำ)
1. **reception แตะแก้/ยืนยัน** — เด็ดขาด (sticky) + ป้อนกลับไปสอน registry.
2. **anchor face-tag** — จับหน้าตอนเดินออกประตูห้องพัก (ระยะใกล้ ชัดสุด) → ติดป้าย
   ชื่อให้ track นั้น. = สูง.
3. **ตาราง/booking POS** (`cctvBookings`) — ใครจองห้องบริการไหนเวลาไหน; คนเข้า
   ห้องนั้นตรงเวลา = therapist ที่จองคิว. = กลาง.
4. **ตามรอย track** — เมื่อติดป้ายแล้ว ตาม track id เดิม + ส่งต่อข้ามกล้องด้วย
   ความใกล้เวลา/พื้นที่ (hand-off). สืบทอด confidence ลดลงทุก hop.
5. **ไม่เข้าเงื่อนไข** → "พนักงาน (ไม่ทราบชื่อ)" — avatar นิรนาม (stable anon id)
   + ชวน reception ยืนยัน.

### 5.3 หลักการ
- **roster วันนี้ (~5-6 คน)** แคบ candidate ให้ชั้น 2-4 (มาจาก POS shift หรือคน
  ที่ถูก tag ที่ anchor วันนี้).
- **confidence decay:** ชื่อมีค่า confidence ลดลงตามอายุ track/จำนวน hop; ต่ำกว่า
  threshold → ถอยเป็นนิรนามทันที (ไม่เดาผิดแบบมั่นใจ).

---

## 6. Presence Engine

แปลง ROOM ENTER/LEAVE (ราย therapist ที่ resolver ตัดสินแล้ว) เป็น **interval** +
คำนวณ **สถานะ**.

### 6.1 ตารางคำนวณสถานะ (ประเภทห้อง × มีลูกค้าในห้อง)

| ประเภทห้อง | มีลูกค้า | ไม่มีลูกค้า |
|---|---|---|
| service (MAISON, foot spa, ไดรผม) | **ทำงาน** (นวด/ไดรผม) | ว่าง / เตรียมห้อง |
| front (reception, เคาน์เตอร์) | ต้อนรับ / บริการเคาน์เตอร์ | ว่าง (เคาน์เตอร์) |
| back (ซักผ้า) | งานหลังบ้าน | งานหลังบ้าน |
| rest (ห้องพัก) | — | พัก |
| facility (ห้องน้ำ, เดา) | — | พัก (ส่วนตัว) |
| ไม่เห็นในกล้องใดเลย | — | ออกจากร้าน / ไม่เห็น |

- **ลูกค้าในห้อง** = นับ track ที่ถูก label customer ในห้อง/โซนเดียวกัน (ใช้ตัวแยก
  staff/customer เดิม).

### 6.2 Data model — ตาราง `presence_intervals` (SQLite ใหม่)
```
id · therapist · therapist_id(POS) · room · status ·
started_at · ended_at (null = ยังอยู่) · confidence · source · pushed
```
- interval = ช่วงที่ **ห้อง + สถานะ** คงที่; เปลี่ยนห้อง **หรือ** สถานะ (ลูกค้า
  เข้า/ออก) = ปิดอันเก่า เปิดอันใหม่.
- **การ์ด "now"** = interval ที่ `ended_at IS NULL` ของแต่ละคน.
- ใช้ flag `pushed` แบบเดียวกับตาราง `events` เดิม.

### 6.3 กันกระพริบ
- ต้องอยู่ห้องเกิน `min_dwell` (เช่น 10-15s) ก่อนนับเป็น interval จริง (เดิน
  corridor ผ่าน ๆ ไม่สร้างแท่ง). ROOM ENTER ปัจจุบันกัน corridor jitter อยู่แล้ว
  (re-fire เฉพาะห้องที่ต่างจากเดิม).

---

## 7. การเชื่อม POS (cloud)

ต่อยอด pattern เดิม (`cctvArrival` / `cctvTimeline`) — region `asia-southeast1`,
guard ด้วย `x-cctv-key`. รายละเอียด field จะเขียนเป็น `contracts/cctv-presence.v1.md`
ตอนทำ.

### 7.1 `POST /cctvPresence` (Cloud Function ใหม่)
- upsert `presence/{id}` (doc id = interval id → idempotent; interval ปิดก็ส่ง
  update `endedAt`). เลียนแบบ `cctvTimeline` (dumb, display-only).

### 7.2 Collection `presence/{id}`
```
id · therapist · therapistId(null ได้) · room · status ·
startedAt(ISO+07) · endedAt(null) · confidence · camera · receivedAt
```
- **บอร์ดสด** = `onSnapshot(presence where endedAt == null)`.
- **Gantt** = query ตามช่วงวัน. ใช้ collection เดียวพอ (ไม่ต้องมี doc "now" แยก).
- retention scheduled cleanup เหมือน `timeline/`.

### 7.3 `GET /cctvBookings` (มีอยู่แล้ว) — ใช้ 2 ทาง
- camera อ่านเป็น identity prior (ชั้น 3).
- UI เอามาทาบ "ตารางที่จอง vs ที่เกิดจริง".

### 7.4 Loop แตะแก้ (corrections) — ปิดวงจร
- reception แตะ avatar ใน POS → เขียน doc `corrections/`.
- camera **poll** ดึง (เหมือน poll bookings) → resolver ใช้ทันที + ป้อนกลับสอน
  registry. (`GET /cctvCorrections` หรือ Firestore read; เลือกตอนทำ.)

### 7.5 ฝั่ง camera
- ขยาย `PushWorker` ให้ push `presence_intervals` ด้วย (reuse flag `pushed`).
- image server (LAN-only) เดิมใช้ต่อสำหรับลิงก์รูปหลักฐานบน Gantt.

---

## 8. หน้าจอ POS (Nuxt.js + Pinia + DaisyUI)

ใช้ข้อมูลชุดเดียวกัน (`presence`) ต่างกันที่มุมมอง. แท็บ `ตอนนี้` / `ไทม์ไลน์`.

**State/data layer (Nuxt 3 + Pinia):** Pinia store `usePresenceStore` ถือ state
สด — subscribe Firestore `onSnapshot(presence where endedAt == null)` ครั้งเดียว
แล้ว derive ทั้ง "ตอนนี้" (group by room) และ feed "ไทม์ไลน์" (query ช่วงวัน
แยก action). ทั้ง 2 หน้าเป็น component ที่อ่านจาก store เดียวกัน (ไม่ subscribe ซ้ำ).
DaisyUI ใช้ทำ avatar group / badge / tabs.

### 8.1 หน้า "ตอนนี้" (บอร์ดสด)
- grid การ์ดต่อห้อง (`auto-fit`). แต่ละการ์ด: ชื่อห้อง + badge สถานะ + **DaisyUI
  avatar group** (ซ้อน + "+N") ของคนที่อยู่ในห้องตอนนี้ + footer (เริ่มเวลา/
  จำนวนลูกค้า).
- การ์ดพิเศษ: "ห้องน้ำ/เดา" (เส้นประ), "ไม่เห็น/ออกจากร้าน" (bucket).
- **แตะ avatar → แก้/ยืนยันชื่อ** → corrections loop (7.4).
- source: `presence where endedAt == null`.

### 8.2 หน้า "ไทม์ไลน์" (Gantt)
- แกนตั้ง = ห้อง (lane), แกนนอน = เวลา. แท่ง = interval ระบายสี **ตามสถานะ**
  (ทำงาน=teal, ว่าง=blue, พัก/หลังบ้าน=gray, เดา=เส้นประ) มี avatar (group) บน
  แท่ง.
- date picker, เลื่อน/ซูมเวลา, เส้น "now" บนวันนี้, คลิกแท่ง → รายละเอียด + รูป
  หลักฐาน (LAN).
- source: query ช่วงวัน.

### 8.3 คงไว้
- หน้า penalty timeline เดิมของ manager (`cctvTimeline`) — ไม่แตะ.

---

## 9. สิ่งที่คงไว้ / ลดบทบาท

- **คงไว้ (ใช้ต่อ):** detection/track/role, `room_zones`/ROOM ENTER, `event_store`,
  `PushWorker`, image server, contracts, penalty rules (รันเบื้องหลัง ส่ง manager).
- **ลดบทบาท:** penalty ไม่ใช่ product หลัก; ไม่โชว์เป็นหน้าหลักของ reception.
- **เปลี่ยน:** auto_enroll (ปิดอยู่) → แทนด้วยเครื่องมือสอนหน้า supervised.

---

## 10. แผนที่โค้ดที่ต้องแก้/เพิ่ม (ฝั่ง camera)

| ไฟล์ | งาน |
|---|---|
| `config.py` | เพิ่ม `rooms` registry, `threshold_timeout`, `min_dwell`, anchor cam, roster source |
| `tracker.py` | ขยาย `_which_room` รองรับ `via` 3 แบบ + threshold inference; emit ROOM LEAVE; ส่ง identity จาก resolver |
| `identity_resolver.py` *(ใหม่)* | logic 5 ชั้น + confidence/decay + roster + hand-off |
| `face_enroller.py` / เครื่องมือใหม่ | supervised teach tool + คิว snapshot จาก anchor cam |
| `presence_engine.py` *(ใหม่)* | interval builder + status derivation + customer-in-room |
| `event_store.py` | เพิ่มตาราง `presence_intervals` + fetch/mark สำหรับ push |
| `pos_timeline.py` (`PushWorker`) | push presence intervals → `/cctvPresence`; poll corrections |
| `person_labeler.py` | reuse; resolver เรียกใช้ |
| `contracts/cctv-presence.v1.md` *(ใหม่)* | สัญญา endpoint/collection |

**ฝั่ง POS (maisonPOS — Nuxt.js + Pinia):** `functions/src/cctvPresence.ts`,
`presence/` rules + retention, `corrections/` (read by camera), Pinia store
`stores/presence.ts` (`usePresenceStore`), หน้า `pages/presence/index.vue`
(ตอนนี้) + `pages/presence/timeline.vue` (ไทม์ไลน์).

---

## 11. ความเสี่ยง & ข้อจำกัด

1. **cross-camera identity ยังพลาดได้** (บัง/คนสองคนเดินสวน) → degrade เป็นนิรนาม +
   ให้ reception แตะแก้. ยอมรับว่าแม่นไม่ 100%.
2. **ยูนิฟอร์มเหมือนกัน** → re-ID จากรูปร่าง/เสื้อช่วยน้อย; พึ่ง anchor + schedule +
   continuity + แตะแก้.
3. **threshold เดาผิดได้** (ออกประตูอื่น) → timeout เปลี่ยนเป็น "ไม่เห็น".
4. **booking ไม่ตรง** (walk-in/สลับห้อง) → ชั้น 3 เป็นแค่ prior ไม่ใช่คำตอบสุดท้าย;
   anchor + แตะแก้ override ได้.
5. **PDPA:** face embedding เป็นข้อมูลชีวมาตร — เก็บ/สอนในเครื่อง, ไม่ commit
   `faces/`/`*.npz`, รูปหลักฐานอยู่ LAN เท่านั้น (เหมือนนโยบายเดิม).

---

## 12. คำถามค้าง / ต้องการจากผู้ใช้ก่อน implement

- [ ] ลิสต์ห้องจริงทั้งหมด + ประเภท (service/front/back/rest/facility).
- [ ] กล้องไหนเห็นประตูห้องไม่มีกล้อง + พิกัดโซนประตู (ห้องน้ำ/ซักผ้า/ไดรผม?).
- [ ] กล้อง anchor: ใช้ตัวไหน/ติดตรงประตูห้องพักจริงไหม.
- [ ] roster "ใครเข้าเวรวันนี้" มาจาก POS shift หรือ derive จาก anchor.
- [ ] ค่าเริ่มต้น: `min_dwell`, `threshold_timeout`, confidence threshold/decay.

---

## 13. Out of scope (YAGNI ตอนนี้)

- Storyline/dot-path view (แบบ B) — ทำทีหลังได้ถ้าต้องการ; เริ่มที่ Gantt + บอร์ดสด.
- Floor-map ผังร้านจริงพร้อมพิกัด.
- Appearance re-ID model เต็มรูปแบบ (พึ่ง anchor + schedule ก่อน).
- รายงาน/วิเคราะห์ utilization เชิงสถิติ (มีข้อมูลใน `presence_intervals` ทำต่อได้).
