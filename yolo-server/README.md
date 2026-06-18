# yolo-server — the VPS side

Receives detection events from the camera client (`yolo-client/`) and relays
them to the POS **in realtime**. Stdlib-only Python 3 — no `pip install`,
deploy on any VPS.

```
 camera (NAT/CGNAT) ──POST /events──►  VPS (this)  ──SSE /stream──►  POS browser
                                          │
                                       events.db  (SQLite history)
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/events` | `X-API-Key` | camera sends one event **or** an array → `201 {stored, events}` |
| GET | `/events?limit=&since=` | `X-API-Key` | recent history (`since=<id>` for incremental) |
| GET | `/stream` | `X-API-Key` | **Server-Sent Events** — POS subscribes, gets each new event live |
| GET | `/health` | none | uptime check → `200 {ok:true}` |

Errors: `401` wrong/missing key, `400` missing `camera_id`/`label` or bad JSON.

## Config (env)

| Var | Default | Notes |
|-----|---------|-------|
| `API_KEY` | — (**required**) | shared secret; must equal the camera's `API_KEY` |
| `PORT` | `8080` | bind port (put TLS proxy in front) |
| `DB_PATH` | `events.db` | SQLite history file |
| `MAX_RETURN` | `500` | cap on `GET /events` rows |

## Run locally / test

```bash
cd yolo-server
API_KEY=testkey PORT=8080 python server.py      # terminal A
python test_server.py                           # terminal B — full E2E
```

`test_server.py` checks auth, validation, POST/GET, batch, and realtime SSE.

## Deploy on a VPS

**1. Copy the file and run as a service (systemd).** No dependencies — just
Python 3 (every VPS has it).

```bash
sudo useradd -r -s /usr/sbin/nologin events || true
sudo mkdir -p /opt/yolo-server && sudo cp server.py /opt/yolo-server/
sudo chown -R events: /opt/yolo-server
```

`/etc/systemd/system/yolo-events.service`:

```ini
[Unit]
Description=YOLO event server
After=network.target

[Service]
User=events
WorkingDirectory=/opt/yolo-server
Environment=API_KEY=CHANGE_ME_long_random_secret
Environment=PORT=8080
Environment=DB_PATH=/opt/yolo-server/events.db
ExecStart=/usr/bin/python3 /opt/yolo-server/server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yolo-events
sudo systemctl status yolo-events
```

**2. Put TLS in front (Caddy = automatic HTTPS).** The camera must reach the
server over `https://`. Caddy gets a free cert and proxies to `:8080`:

`/etc/caddy/Caddyfile`:

```
api.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

> SSE note: Caddy streams fine out of the box. With **nginx**, add
> `proxy_buffering off;` and `proxy_read_timeout 1h;` on the `/stream` location
> or the live feed will stall.

**3. Firewall — only 80/443 inbound.** The app port `8080` stays loopback-only;
nothing else needs to be open.

```bash
sudo ufw allow 80,443/tcp && sudo ufw enable
```

**4. Point the camera at it** (on the camera machine, `yolo-client/`):

```bash
export SERVER_URL="https://api.yourdomain.com"
export API_KEY="CHANGE_ME_long_random_secret"   # same as the service
export CAMERA_ID="front-door"
python send_test_event.py        # expect 201, then GET echoes it
```

## POS integration (realtime timeline)

The POS frontend subscribes once and renders events as they arrive. Because
`EventSource` can't set headers, pass the key as a query param **only if** your
proxy strips it from logs — otherwise expose `/stream` on an internal network,
or wrap it in your POS backend. Simplest from a POS **backend** (Node):

```js
const es = new EventSource("https://api.yourdomain.com/stream", {
  headers: { "X-API-Key": process.env.API_KEY },   // node 'eventsource' pkg
});
es.onmessage = (m) => {
  const ev = JSON.parse(m.data);   // {id, ts, camera_id, label, confidence, count, meta}
  timeline.push(ev);               // render on the POS timeline
};
```

For history / reconnect: on connect, `GET /events?limit=50` to backfill, then
stream live; track the last `id` and resume with `?since=<id>` after a drop.

## Notes

- **`events.db` and `test.db` are gitignored** (the repo blocks `*.db`).
- One process handles many cameras and many POS viewers concurrently
  (threaded). For heavy load, run several behind the proxy with a shared DB
  path on the same host.
- Pairs with `../yolo-client/` — same `API_KEY`, same event shape.
