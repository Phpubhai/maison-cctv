# yolo-client — push detections to the VPS

Outbound-only client: runs YOLO on a source and POSTs detection events to the
VPS at `{SERVER_URL}/events`. The camera machine **never opens an inbound
port** — it only makes outbound HTTPS calls, so it can sit behind NAT/CGNAT.

- `detect_and_push.py` — detection loop + per-label debounce + background
  sender thread (retry queue, exponential backoff). The detection loop never
  blocks on the network.
- `send_test_event.py` — POST one fake event (connectivity smoke test).
- `mock_server.py` — a local stand-in for the VPS `/events` endpoint (testing).
- `retry_test.py` — proves queued events survive an outage (offline → online).
- `requirements.txt` — `ultralytics`, `requests`.

## Config (environment variables)

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `SERVER_URL` | yes | — | e.g. `https://api.yourdomain.com` |
| `API_KEY` | yes | — | same value as the server (`X-API-Key`) |
| `CAMERA_ID` | yes | — | e.g. `cam-01` |
| `SOURCE` | no | `0` | `0`=webcam, `rtsp://...`, or a file path |
| `MODEL` | no | `yolov8n.pt` | any ultralytics weights (e.g. `yolo11m.pt`) |
| `CONF` | no | `0.4` | detection confidence threshold |
| `COOLDOWN_S` | no | `10` | min seconds between repeat events per label |
| `CLASSES` | no | (all) | comma-separated class names, e.g. `person,cup` |

## Contract (do not change shape)

`POST {SERVER_URL}/events`, headers `X-API-Key`, `Content-Type: application/json`.
Body is one event object **or** an array of them:

```json
{
  "ts": "2026-06-18T10:00:00Z",
  "camera_id": "cam-01",
  "label": "person",
  "confidence": 0.91,
  "count": 2,
  "meta": { "bbox": [10, 20, 120, 240] }
}
```

Success: `201 {"stored": N, "events": [...]}`. `401` = bad key, `400` = missing
`camera_id`/`label`.

**Debounce:** an event is sent when a label first appears in the frame, or when
it has persisted past `COOLDOWN_S` — never every frame.

**Resilience:** events are queued in memory and retried with exponential
backoff (capped 60s); a network/5xx/auth failure never drops an event or blocks
detection. A wrong `API_KEY` (`401`) is held and retried, so events flush once
the key is fixed — nothing lost. Only a malformed event (`400/404/413/422`) is
logged and dropped, so one bad event can't wedge the queue.

## Quick test (no VPS needed)

```bash
cd yolo-client
pip install -r requirements.txt

# terminal A — fake VPS
API_KEY=testkey python mock_server.py            # http://127.0.0.1:8099

# terminal B
export SERVER_URL=http://127.0.0.1:8099 API_KEY=testkey CAMERA_ID=cam-01
python send_test_event.py        # POST 201 + GET echoes the event
python retry_test.py             # outage -> reconnect -> backlog flushes, no loss
```

## Run for real

```bash
export SERVER_URL="https://api.yourdomain.com"
export API_KEY="<same as server>"
export CAMERA_ID="cam-01"
export SOURCE="rtsp://user:pass@host:554/...channel=3"   # or 0 for webcam
export MODEL="yolo11m.pt"     # optional; bigger = more accurate, more GPU
export CLASSES="person"       # optional filter
python detect_and_push.py
```

> Note: this client runs its **own** YOLO inference, separate from the main
> behavior monitor (`main.py`). Running both on one GPU competes for it — use a
> light `MODEL` here (yolov8n) or run them on different machines.
