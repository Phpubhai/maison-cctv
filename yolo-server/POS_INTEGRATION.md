# POS integration — CCTV realtime timeline

How the POS app consumes detection events from the CCTV event server
(`yolo-server`). **The POS only READS** — it never POSTs (the cameras post).
This document is self-contained: an engineer/agent can build the timeline UI
from it alone.

---

## 1. Connection

| | Value |
|--|--|
| Base URL (same LAN) | `http://192.168.1.67:8080` |
| Base URL (later, via hub) | `https://<hub-domain>` (TBD if multi-branch) |
| Auth header | `X-API-Key: <API_KEY>` |
| Auth for browser `EventSource` | `?key=<API_KEY>` query param (EventSource can't set headers) |
| CORS | server returns `Access-Control-Allow-Origin: *` (browser fetch OK) |

> **Get `<API_KEY>` from the shop operator** (it lives in `cctv-env.bat`, not in
> git). Do not hardcode it in committed source — read it from POS config/env.

Health check (no key): `GET /health` → `200 {"ok": true, "time": "..."}`.

---

## 2. Endpoints the POS uses

### `GET /events` — history / backfill
Query: `?limit=<n>` (default 100, max 500), `?since=<id>` (only events with
`id > since`, oldest-first; used to resume after a reconnect).

```
GET /events?limit=50            -> newest 50, NEWEST first
GET /events?since=1234          -> everything after id 1234, OLDEST first
```
Response `200`:
```json
{ "count": 2, "events": [ <event>, <event> ] }
```

### `GET /stream` — realtime (Server-Sent Events)
Long-lived `text/event-stream`. Each new event arrives as one SSE message:
```
data: {"id":1240,"ts":"2026-06-18 14:32:10","camera_id":"front door", ...}

: ping          <- heartbeat every ~15s, ignore lines starting with ":"
```
Open it with the key in the query string (see code below).

---

## 3. Event schema

```jsonc
{
  "id": 1240,                       // server sequence id (monotonic; use for ?since=)
  "received": "2026-06-18T07:32:10Z", // server receive time (UTC)
  "ts": "2026-06-18 14:32:10",      // event time, SHOP LOCAL time  -> column "เวลา"
  "camera_id": "front door",        // which camera / room
  "label": "PHONE USE",             // WHAT happened              -> column "ทำอะไร"
  "actor": "Phai",                  // WHO: staff name / "ลูกค้า" / "STAFF" / null -> "ใคร"
  "duration": 42.0,                 // HOW LONG, seconds (may be null) -> "นานแค่ไหน"
  "confidence": null,               // present for raw-detection events, else null
  "count": null,                    // object count for raw-detection events
  "meta": {
    "severity": "alert",            // "alert" | "warning" | "normal"
    "description": "phone in hand, started 14:31:28 (42s so far)",
    "therapist_id": null,           // POS staff join key when known, else null
    "image_url": "/snapshot/Penalty/20260618_..._Phai_SLEEPING.jpg" // optional, see §3a
  }
}
```

The 4 timeline columns map directly: **`ts` / `actor` / `label` / `duration`**.

### 3a. Snapshot images (`meta.image_url`)
Important events (penalties) include `meta.image_url` — a snapshot of the
moment, with the person boxed in red. The image **stays on the camera machine**;
the server streams it over the LAN. Build the full URL and append the key:

```
<img src="{BASE}{meta.image_url}?key={API_KEY}">
```
e.g. `http://192.168.1.67:8080/snapshot/Penalty/20260618_..._SLEEPING.jpg?key=...`

- `meta.image_url` is **absent** for events with no snapshot (ENTER/LEAVE, *END
  events) — show the row without an image then.
- The endpoint is key-protected and serves images only; it returns `404` for
  anything outside the evidence folders (no config/secrets/faces leak).
- Images are large full frames — use a thumbnail (`height ~40px`) linking to the
  full image, and `loading="lazy"`.
- **Caveat (multi-branch later):** this works because the event server runs on
  the same machine as the cameras. If you move to a remote hub, snapshots need a
  different delivery (upload, or per-branch image URL) — TBD.

### Event vocabulary (`label`) currently pushed to the POS

| `label` | meaning | severity | `duration`? | `actor` |
|--|--|--|--|--|
| `SLEEPING` | staff asleep on duty | alert | yes | staff name |
| `DROWSY` | staff dozing off | warning | yes | staff name |
| `PHONE USE` | staff on phone | alert | yes | staff name |
| `GREETING MISSED` | customer arrived, no staff greeted | alert | yes (~30s) | `STAFF` |
| `ROOM MESSY` | room differs from tidy reference | alert | yes | `STAFF` |
| `UNCLEARED TABLE` | glass left on reception table | alert | yes | `STAFF` |
| `OBJECT ON FLOOR` | object left on the floor | alert | yes | `STAFF` |
| `ENTER` | customer entered the shop (front door only) | normal | no | `ลูกค้า` |
| `LEAVE` | customer left the shop (front door only) | normal | no | `ลูกค้า` |
| `POSTURE NOTE` | customer posture note | normal | no | `ลูกค้า` |

Notes for the UI:
- Color/priority by `meta.severity` (`alert` = red, `warning` = amber, `normal` = info).
- `actor` may be `null` (unknown person) → show "—".
- `duration` may be `null` → show "—". It's seconds; format to วิ/นาที/ชม.
- New `label` values may be added later — render unknown labels generically,
  don't hard-fail.

---

## 4. Recommended client flow

1. On load: `GET /events?limit=50` → render (reverse to oldest-first), remember
   the largest `id` seen.
2. Open `GET /stream` → prepend each incoming event live; keep updating max `id`.
3. On stream error/disconnect: reconnect, then `GET /events?since=<maxId>` to
   fill any gap, then resume streaming. (`EventSource` auto-reconnects; just
   re-run the catch-up GET in `onopen`.)

### Browser (vanilla)
```js
const BASE = "http://192.168.1.67:8080";
const KEY  = posConfig.cctvApiKey;           // from POS config, NOT hardcoded
let maxId = 0;

async function backfill() {
  const r = await fetch(`${BASE}/events?since=${maxId}&key=${encodeURIComponent(KEY)}`,
                        { headers: { "X-API-Key": KEY } });
  const { events } = await r.json();         // oldest-first when using ?since=
  for (const ev of events) { render(ev); maxId = Math.max(maxId, ev.id); }
}

function connect() {
  const es = new EventSource(`${BASE}/stream?key=${encodeURIComponent(KEY)}`);
  es.onopen = backfill;                       // catch up after every (re)connect
  es.onmessage = (m) => { const ev = JSON.parse(m.data); render(ev); maxId = Math.max(maxId, ev.id); };
  // EventSource reconnects automatically on error
}
connect();
```

### Node backend (relay into your own POS realtime layer)
```js
import EventSource from "eventsource";        // npm i eventsource
const es = new EventSource(`${BASE}/stream?key=${KEY}`);
es.onmessage = (m) => pushToPosClients(JSON.parse(m.data));
```

---

## 5. What the POS does NOT need to do
- No POST / no write access — events are produced by the cameras only.
- No polling loop needed (use SSE); `GET /events` is just for backfill/history.
- No database — the server keeps the SQLite history; query it via `?since=`.

## 6. Open questions to confirm with the CCTV side
- Final hosting: same-LAN now (`192.168.1.67:8080`); if multi-branch, a shared
  hub URL + per-branch `camera_id` prefix will be provided.
- Whether to also push behavior **END** events (final total duration) — current
  build pushes the START + periodic re-alerts with "so far" duration.
- Thai vs English `label` text — currently English codes (see table); can be
  localized server-side or by the POS.
