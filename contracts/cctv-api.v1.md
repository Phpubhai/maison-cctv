# CCTV ↔ POS API — v1

> **Status:** active. **Owner:** maisonPOS (server). **Consumer:** Yolo_Monitor (camera).
> Source design: `docs/superpowers/specs/2026-06-15-cctv-pos-arrival-detection-design.md`

Two HTTP endpoints, both Firebase Cloud Functions (region `asia-southeast1`),
machine-to-machine, guarded by a shared key.

## Authentication

Every request MUST carry the shared key header:

```
x-cctv-key: <secret>
```

- The secret is configured server-side as the `CCTV_API_KEY` Functions secret.
- Wrong/missing key → `401 {"error":"Unauthorized"}`.
- The same secret value is stored on the camera machine in `config.py`
  (`pos_api.api_key`) — move it to an env var for production.

## Base URL

```
https://asia-southeast1-<FIREBASE_PROJECT>.cloudfunctions.net
```

Local emulator form:

```
http://127.0.0.1:5001/<FIREBASE_PROJECT>/asia-southeast1
```

The function name is appended directly: `…/cctvBookings`, `…/cctvArrival`.

## Time & IDs

- All timestamps are ISO-8601 **with offset**. The store runs in Bangkok, a
  fixed `+07:00` (no DST). Example: `2026-06-15T15:00:00+07:00`.
- `arrivalId` is generated **by the camera** and must be globally unique across
  restarts and days. Format: `<YYYYMMDD>_<bootId>_<trackId>`, e.g.
  `20260615_a1b2c3_42`. `bootId` is random per program launch (ByteTrack
  `trackId` resets every restart, so the raw id alone would collide).

---

## 1. `GET /cctvBookings`

Returns today's bookings so the camera knows who is expected (used by the
Phase-2 face-match path; in Phase 1 it is informational).

### Request

| Part | Value |
|---|---|
| Method | `GET` |
| Header | `x-cctv-key: <secret>` |
| Query | `date` — optional, `YYYY-MM-DD` (Asia/Bangkok). Defaults to today. |

```
GET /cctvBookings?date=2026-06-15
x-cctv-key: <secret>
```

### Response `200`

```json
{
  "date": "2026-06-15",
  "generatedAt": "2026-06-15T14:30:00.000Z",
  "bookings": [
    {
      "bookingId": "uuid",
      "customerId": "abc123",
      "customerName": "คุณมาลี",
      "customerPhone": "0812345678",
      "startTime": "2026-06-15T15:00:00+07:00",
      "status": "confirmed",
      "therapistName": "ปาย"
    }
  ]
}
```

Field notes:

| Field | Type | Null? | Notes |
|---|---|---|---|
| `bookingId` | string (uuid) | no | |
| `customerId` | string | **yes** | null for bookings not yet linked to a customer |
| `customerName` | string | yes | snapshot name on the booking |
| `customerPhone` | string | yes | |
| `startTime` | string (ISO+07) | no | appointment time |
| `status` | string | no | `draft`/`pending`/`confirmed`/`checked_in`/`completed`/`cancelled`/`void` |
| `therapistName` | string | yes | assigned therapist nickname; null if unassigned |

### Errors

| Code | Body | When |
|---|---|---|
| `401` | `{"error":"Unauthorized"}` | bad/missing key |
| `405` | `{"error":"Method Not Allowed"}` | not GET |
| `500` | `{"error":"Failed to load bookings"}` | server/Data Connect error |

---

## 2. `POST /cctvArrival`

The camera reports that a customer/unknown person **arrived at** or **left** the
front door.

> **The camera NEVER sends `staff` arrivals.** It filters out people it
> classifies as staff (uniform or recognized face) and people sitting inside a
> service zone, so the waiting widget never fills with cashier staff. Only
> `role: "customer"` or `role: "unknown"` reach this endpoint.

### Request

| Part | Value |
|---|---|
| Method | `POST` |
| Headers | `x-cctv-key: <secret>`, `content-type: application/json` |

Body — **arrived**:

```json
{
  "event": "arrived",
  "arrivalId": "20260615_a1b2c3_42",
  "customerId": null,
  "customerName": null,
  "role": "unknown",
  "matchScore": null,
  "arrivedAt": "2026-06-15T14:38:00+07:00",
  "camera": "front door"
}
```

Body — **left** (only `event` + `arrivalId` are required):

```json
{ "event": "left", "arrivalId": "20260615_a1b2c3_42" }
```

Field notes:

| Field | Type | Required | Notes |
|---|---|---|---|
| `event` | `"arrived"` \| `"left"` | **yes** | |
| `arrivalId` | string | **yes** | unique id (see "Time & IDs"); used as the Firestore doc id → idempotent |
| `customerId` | string \| null | no | Phase 1: always `null`. Phase 2: set when a face matches → triggers auto check-in |
| `customerName` | string \| null | no | |
| `role` | `"customer"` \| `"unknown"` | no | never `"staff"` |
| `matchScore` | number \| null | no | face-match score; null when unmatched |
| `arrivedAt` | string (ISO+07) | no | when the camera first saw the person; defaults to server now |
| `camera` | string | no | defaults to `"front door"` |

### Behaviour (server side)

1. Validate key → else `401`.
2. `event:"arrived"`:
   - If `customerId` is set → find today's `pending`/`confirmed` booking for that
     customer nearest to `arrivedAt` → set its status to `checked_in`.
     *(Phase 1: skipped because `customerId` is null.)*
   - **Upsert** `arrivals/{arrivalId}` with `status:"waiting"` (idempotent —
     repeated fires for the same `arrivalId` overwrite, never duplicate).
3. `event:"left"`: set `arrivals/{arrivalId}` → `status:"left"` + `leftAt`.

### Response `200`

```json
{ "ok": true, "matchedBookingId": "uuid-or-null" }
```

`matchedBookingId` is non-null only when an `arrived` event auto-checked-in a
booking (Phase 2).

### Errors

| Code | Body | When |
|---|---|---|
| `400` | `{"error":"Missing or invalid 'event'/'arrivalId'"}` | bad body |
| `401` | `{"error":"Unauthorized"}` | bad/missing key |
| `405` | `{"error":"Method Not Allowed"}` | not POST |
| `500` | `{"error":"Failed to record arrival"}` | server error |

---

## Side effect: `arrivals/{arrivalId}` (Firestore)

`POST /cctvArrival` writes this doc; the POS UI listens to it live. The camera
does not read it, but it is documented here so both sides agree on the shape.

```
arrivals/{arrivalId}
  arrivalId    string
  event        "arrived"
  status       "waiting" | "served" | "left"
  customerId   string | null
  customerName string | null
  bookingId    string | null      # set when auto-checked-in
  startTime    Timestamp | null    # appointment time, when matched
  therapistName string | null
  camera       string              # "front door"
  role         "customer" | "unknown"
  arrivedAt    Timestamp
  leftAt       Timestamp | null
  matchScore   number | null
  updatedAt    Timestamp
```

- `status:"waiting"` rows appear in the widget. `served` (staff pressed
  "received") and `left` (camera saw them leave) drop out.

---

## Changelog

- **v1 (2026-06-15):** initial — `cctvBookings`, `cctvArrival`, `arrivals/` shape.
