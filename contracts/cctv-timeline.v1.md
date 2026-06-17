# CCTV → POS Timeline — v1 (work order for the POS team)

> **Status:** ready for POS implementation. **Owner:** maisonPOS (server).
> **Consumer/producer:** Yolo_Monitor (camera) pushes; POS displays.
> Companion: `cctv-api.v1.md` (arrival detection) — same auth, region, patterns.
> Source design: `docs/superpowers/specs/2026-06-15-timeline-store-design.md`.

The camera keeps the full event history locally in SQLite. It pushes the
**penalty (staff) + customer** subset to the POS so reception/managers see a
live **time · who · what** table. Images stay in the shop — the camera serves
them on the LAN; the POS only shows a link.

This doc adds ONE Cloud Function + ONE Firestore collection + ONE table view.
Everything else (auth, key, region) is identical to `cctv-api.v1.md`.

---

## What the camera already does (no POS work needed)

- Writes every event to local `events.db` (full history, queryable).
- Runs a background push worker that POSTs unsent penalty/customer rows to
  `POST /cctvTimeline`, marking them sent only on HTTP 200 (retries on failure
  → survives POS/network outages).
- Runs a LAN-only image server; each pushed row carries an `imageUrl` that
  resolves **only inside the shop network** (no image ever reaches the cloud).
- Push is currently disabled on the camera (`pos_timeline.enabled=false`) until
  this endpoint exists and the shared key is set. Turning it on is a 1-line
  camera config change once you confirm the endpoint is live.

---

## 1. `POST /cctvTimeline` (new Cloud Function)

Mirror `cctvArrival` exactly: `onRequest({region:"asia-southeast1", cors:true})`,
guarded by `x-cctv-key` (same `CCTV_API_KEY` secret), copy the
`functions/src/listServicesPublic.ts` / `cctvArrival.ts` pattern.

### Request

| Part | Value |
|---|---|
| Method | `POST` |
| Headers | `x-cctv-key: <secret>`, `content-type: application/json` |

Body (one event):
```json
{
  "id": 4821,
  "ts": "2026-06-17T14:31:41+07:00",
  "camera": "front door",
  "actorType": "staff",            // "staff" | "customer" | "unknown"
  "actorName": "Phai",             // null when not identified
  "therapistId": null,             // POS join key for named staff, else null
  "event": "PHONE USE",
  "description": "phone in hand, started 14:30:56 (45s so far)",
  "severity": "alert",             // normal | warning | alert
  "imageUrl": "http://192.168.1.50:8088/Penalty/2026..._PHONE_USE_45s.jpg"
}
```

Field notes:

| Field | Type | Null? | Notes |
|---|---|---|---|
| `id` | number | no | camera-local row id; **use as the Firestore doc id** → idempotent (re-push overwrites, never duplicates) |
| `ts` | string ISO+07 | no | when the event happened |
| `camera` | string | no | `front door` / `reception` / `foot spa` / `office` … |
| `actorType` | string | no | who: staff / customer / unknown |
| `actorName` | string | yes | `Phai` / `Nicky` / customer name / null |
| `therapistId` | string | yes | join to POS staff records when known |
| `event` | string | no | `SLEEPING`, `PHONE USE`, `GREETING MISSED`, `ROOM MESSY`, `OBJECT ON FLOOR`, `POSTURE NOTE`, customer `ENTER`/`LEAVE`, … |
| `description` | string | yes | human-readable detail (already Thai/EN-ready text) |
| `severity` | string | no | drives row color: normal/warning/alert |
| `imageUrl` | string | yes | LAN-only evidence link; null when no image. **Opens only in-shop.** |

### Behaviour
1. Validate key → else `401`.
2. **Upsert** `timeline/{id}` in Firestore with the fields above + a server
   `receivedAt` timestamp. (Doc id = `id` → idempotent.)
3. No Data Connect writes — this is display-only signal (unlike `cctvArrival`,
   which also flips booking status). Keep it dumb.

### Response `200`
```json
{ "ok": true }
```

### Errors
| Code | When |
|---|---|
| `400` | missing `id`/`ts`/`event` |
| `401` | bad/missing key |
| `405` | not POST |
| `500` | Firestore write failed |

---

## 2. Firestore `timeline/{id}`

```
timeline/{id}          # id = camera row id (number, as string doc id)
  id           number
  ts           string  (ISO+07)
  camera       string
  actorType    string
  actorName    string | null
  therapistId  string | null
  event        string
  description  string | null
  severity     "normal" | "warning" | "alert"
  imageUrl     string | null      # LAN-only
  receivedAt   Timestamp          # server-set on write
```

**Rules (`firestore.rules`):** staff (authed) may **read** `timeline/`;
**only the Cloud Function (admin)** writes. Same shape as the `arrivals/` rule.

**Retention:** a scheduled function deletes `timeline/` docs older than N days
(e.g. 90) so the collection stays small. The camera keeps the real long-term
history locally; Firestore is just the live window. (MVP: client can filter to
the last N days; add the scheduled cleanup when convenient.)

---

## 3. POS UI — timeline table

A page/panel (e.g. `app/pages/timeline.vue` or a tab on an existing ops page):

```
เวลา        กล้อง        ใคร            เหตุการณ์                          
14:31:41    front door   Phai           PHONE USE · phone in hand 45s   [ดูรูป]
14:28:10    reception    ไม่ทราบชื่อ      ENTER · customer arrived
14:20:03    office       Nicky          ROOM MESSY · ... 5m              [ดูรูป]
```

Behaviour:
- `useTimeline()` composable → `onSnapshot(timeline ORDER BY ts DESC LIMIT n)`.
- Row color by `severity` (alert=red, warning=amber, normal=default).
- `actorName ?? "ไม่ทราบชื่อ"`; show `camera`, `event`, `description`.
- **[ดูรูป] link** = `imageUrl`, shown only when non-null. Opens in a new tab.
  It is a **LAN address** — it works on shop devices, will not load off-site.
  (Optional nicety: detect load failure and show "ดูรูปได้เฉพาะในร้าน".)
- Filters (nice to have): by camera, by actorName, by severity, by date.
  All are plain Firestore queries / client-side filters.

---

## 4. Files to touch (POS repo)

- `functions/src/cctvTimeline.ts` (new) — the endpoint
- `functions/src/index.ts` — export it
- `firestore.rules` — add `timeline/` (read: staff, write: admin)
- `app/composables/useTimeline.ts` (new)
- `app/pages/timeline.vue` or a panel component (new)
- (optional) a scheduled function for `timeline/` retention

## 5. Test independently (no camera needed)
1. `curl` the function with a fake payload (good key → 200 + doc in
   `timeline/{id}`; repeat same `id` → overwrites, not duplicated; bad key →
   401; missing `event` → 400).
2. Drop 3–4 mock docs into the emulator's `timeline/` (one per severity, one
   with `imageUrl`, one without) → verify the table renders, colors, sorting,
   and the [ดูรูป] link appears only when `imageUrl` is set.
3. Hand the camera team the deployed `base_url` + `x-cctv-key`; they flip
   `pos_timeline.enabled=true` and real rows flow in.

## 6. What to send back to the camera team
- Confirmation the function is deployed + its `base_url`.
- The `x-cctv-key` value (same one as `cctvArrival`, if already shared, reuse it).

## Changelog
- **v1 (2026-06-17):** initial — `cctvTimeline`, `timeline/` shape, table view.
