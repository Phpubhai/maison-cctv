# CCTV → POS Presence — v1 (work order for the POS team)

> **Status:** ready for POS implementation. **Owner:** maisonPOS (server).
> **Producer:** Yolo_Monitor (camera) pushes; POS displays.
> Companion: `cctv-timeline.v1.md` / `cctv-api.v1.md` — same auth, region,
> idempotency pattern. Source design:
> `docs/superpowers/specs/2026-06-22-presence-timeline-design.md`.

The camera tracks which therapist is in which room and pushes **presence
intervals** (room + status over time). The POS shows them as a live "now" board
and a room×time timeline. One Cloud Function + one Firestore collection.

## 1. `POST /cctvPresence` (new Cloud Function)

Mirror `cctvTimeline` exactly: `onRequest({region:"asia-southeast1", cors:true})`,
guarded by `x-cctv-key` (same `CCTV_API_KEY` secret).

### Request

| Part | Value |
|---|---|
| Method | `POST` |
| Headers | `x-cctv-key: <secret>`, `content-type: application/json` |

Body (one interval):
```json
{
  "id": 5,
  "therapist": "Phai",
  "therapistId": "t1",
  "room": "MAISON 2",
  "status": "ทำงาน",
  "startedAt": "2026-06-22T13:40:00+07:00",
  "endedAt": "2026-06-22T15:00:00+07:00",
  "confidence": 0.9,
  "camera": "spa room"
}
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `id` | number | no | camera-local interval id; **use as Firestore doc id** → idempotent |
| `therapist` | string | yes | name when known, else null (anonymous) |
| `therapistId` | string | yes | POS join key when known |
| `room` | string | no | logical room name (NOT camera) |
| `status` | string | no | `ทำงาน`/`ว่าง`/`ต้อนรับ`/`งานหลังบ้าน`/`พัก` |
| `startedAt` | string ISO+07 | no | interval start |
| `endedAt` | string ISO+07 | yes | null = still open (live "now") |
| `confidence` | number | yes | 0–1; low → show as tentative |
| `camera` | string | yes | source camera (debug) |

### Behaviour
1. Validate key → else `401`.
2. **Upsert** `presence/{id}` with the fields above + server `receivedAt`.
   (Doc id = `id`, so re-pushing the same interval — e.g. when it closes and
   `endedAt` is filled — overwrites, never duplicates.)
3. No Data Connect writes — display-only signal.

### Response `200`
```json
{ "ok": true }
```

### Errors
| Code | When |
|---|---|
| `400` | missing `id`/`room`/`status`/`startedAt` |
| `401` | bad/missing key |
| `405` | not POST |
| `500` | Firestore write failed |

## 2. Firestore `presence/{id}`
Same fields as the body + `receivedAt: Timestamp`. **Rules:** staff (authed)
read; only the Cloud Function (admin) writes. **Retention:** scheduled delete of
docs whose `endedAt` is older than N days (e.g. 90); the camera keeps full
history locally.

## 3. POS UI (Plan 3 — Nuxt.js + Pinia + DaisyUI; see design §8)
- Pinia store `usePresenceStore` subscribes `onSnapshot(presence where
  endedAt == null)` once; both pages read from it.
- **หน้า "ตอนนี้":** room cards (DaisyUI avatar group + status badge), grouped
  by room. Tap avatar → correction (needs the `corrections/` channel, Plan 2).
- **หน้า "ไทม์ไลน์":** store action queries a day range → Gantt (rooms × time)
  with status colors + avatar chips.

## 4. Test independently (no camera)
1. `curl` with a fake interval (good key → 200 + doc; same `id` again with
   `endedAt` set → overwrites; bad key → 401; missing `room` → 400).
2. Drop a few mock docs (one open `endedAt:null`, one closed) → verify the
   "now" board shows only open ones and the timeline renders both.

## Changelog
- **v1 (2026-06-22):** initial — `cctvPresence`, `presence/` shape.
