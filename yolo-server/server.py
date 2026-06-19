#!/usr/bin/env python3
"""VPS event server -- receives detection events from the camera client and
relays them to the POS in realtime.

Stdlib only (no pip install): deploy on any VPS with just Python 3.

Endpoints (all under the shared X-API-Key):
  POST /events   accept one event object OR an array; store + broadcast.
                 201 {"stored": N, "events": [...]}
  GET  /events   recent events (?limit=, ?since=ISO).  200 {"count", "events"}
  GET  /stream   Server-Sent Events -- the POS opens this and gets each new
                 event pushed live (text/event-stream).
  GET  /health   200 {"ok": true}  (no key needed -- for uptime checks)

Auth: header  X-API-Key: <API_KEY env>.  Wrong/missing -> 401.
Config (env): API_KEY (required), PORT (default 8080), DB_PATH (default events.db),
              MAX_RETURN (GET /events cap, default 500).

Behind a TLS reverse proxy (nginx/Caddy) in production -- see README.
"""
import json
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

API_KEY = os.environ.get("API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "events.db")
MAX_RETURN = int(os.environ.get("MAX_RETURN", "500"))

# Evidence snapshots stay on the camera machine; the server serves them over
# the LAN (key required) at /snapshot/<path>. Only these subtrees under
# SNAPSHOT_ROOT are served -- never config, secrets, faces, or the db.
SNAPSHOT_ROOT = os.environ.get(
    "SNAPSHOT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAPSHOT_DIRS = ("Penalty", "behavior_events")     # allowlisted subtrees only
IMG_EXT = (".jpg", ".jpeg", ".png")
# the camera also UPLOADS snapshots here (POST /snapshot/...), so the server has
# its own copy and can serve images even when it runs on a different machine
# than the cameras (a hub). Served files are looked up here first, then in the
# local evidence dirs above.
UPLOAD_DIR = os.environ.get(
    "UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots"))
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))  # 10 MB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  received   TEXT NOT NULL,         -- server receive time (ISO UTC)
  ts         TEXT,                  -- event time from the camera
  camera_id  TEXT NOT NULL,
  label      TEXT NOT NULL,         -- what happened (e.g. SLEEPING, person)
  actor      TEXT,                  -- who: staff name / "customer" / null
  duration   REAL,                  -- how long it lasted, in seconds
  confidence REAL,
  count      INTEGER,
  meta       TEXT                   -- JSON blob
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
"""

# columns added after v1 -- ALTER existing DBs so old events.db keeps working
_MIGRATE = {"actor": "TEXT", "duration": "REAL"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """SQLite-backed event log + live SSE fan-out. Thread-safe."""

    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(_SCHEMA)
            have = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
            for col, typ in _MIGRATE.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
            self.conn.commit()
        self.subscribers = set()        # set[queue.Queue] for /stream clients
        self.sub_lock = threading.Lock()

    def add(self, events):
        """Persist a list of events; return them with server fields filled."""
        out = []
        with self.lock:
            for e in events:
                received = now_iso()
                cur = self.conn.execute(
                    "INSERT INTO events (received,ts,camera_id,label,actor,duration,"
                    "confidence,count,meta) VALUES (?,?,?,?,?,?,?,?,?)",
                    (received, e.get("ts"), e["camera_id"], e["label"],
                     e.get("actor"), e.get("duration"),
                     e.get("confidence"), e.get("count"),
                     json.dumps(e.get("meta")) if e.get("meta") is not None else None))
                row = {"id": cur.lastrowid, "received": received, **e}
                out.append(row)
            self.conn.commit()
        self._broadcast(out)
        return out

    def recent(self, limit, since_id):
        limit = min(limit, MAX_RETURN)
        with self.lock:
            if since_id:
                rows = self.conn.execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?",
                    (since_id, limit)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r):
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else None
        return d

    # --- SSE fan-out ------------------------------------------------------
    def subscribe(self):
        q = queue.Queue()
        with self.sub_lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.sub_lock:
            self.subscribers.discard(q)

    def _broadcast(self, events):
        with self.sub_lock:
            subs = list(self.subscribers)
        for q in subs:
            for e in events:
                q.put(e)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store = None  # set on the server instance

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_viewer(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html")
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json(404, {"error": "viewer.html not found"})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        # header is preferred (camera always uses it); a browser opening
        # /stream or /events via EventSource can't set headers, so a ?key=
        # query param is accepted as a fallback for GET viewers on the LAN.
        key = self.headers.get("X-API-Key")
        if not key:
            key = parse_qs(urlparse(self.path).query).get("key", [None])[0]
        if key != API_KEY:
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    # --- POST /events | /snapshot ----------------------------------------
    def do_POST(self):
        p = urlparse(self.path).path
        if p.startswith("/snapshot/"):
            if not self._auth():
                return
            return self._recv_snapshot(p[len("/snapshot/"):])
        if p.rstrip("/") != "/events":
            return self._json(404, {"error": "not found"})
        if not self._auth():
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        events = data if isinstance(data, list) else [data]
        if not events:
            return self._json(400, {"error": "empty body"})
        for e in events:
            if not isinstance(e, dict) or not e.get("camera_id") or not e.get("label"):
                return self._json(400, {"error": "each event needs camera_id and label"})
        stored = self.store.add(events)
        self._json(201, {"stored": len(stored), "events": stored})

    # --- GET /events | /stream | /health ----------------------------------
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path in ("", "/viewer"):
            return self._serve_viewer()
        if path == "/health":
            return self._json(200, {"ok": True, "time": now_iso()})
        if path == "/events":
            if not self._auth():
                return
            qs = parse_qs(urlparse(self.path).query)
            limit = int(qs.get("limit", ["100"])[0])
            since = int(qs.get("since", ["0"])[0]) if qs.get("since") else 0
            ev = self.store.recent(limit, since)
            return self._json(200, {"count": len(ev), "events": ev})
        if path == "/stream":
            if not self._auth():
                return
            return self._sse()
        if path.startswith("/snapshot/"):
            if not self._auth():
                return
            return self._serve_snapshot(path[len("/snapshot/"):])
        self._json(404, {"error": "not found"})

    @staticmethod
    def _safe_target(base, rel, restrict_subdirs):
        """Resolve rel under base with realpath containment + image extension.
        If restrict_subdirs, the first path segment must be allowlisted. Returns
        the absolute path or None if it fails any check (traversal/ext/scope)."""
        target = os.path.realpath(os.path.join(base, rel))
        root = os.path.realpath(base)
        if not (target == root or target.startswith(root + os.sep)):
            return None
        if restrict_subdirs:
            sub = os.path.relpath(target, root).replace("\\", "/").split("/")[0]
            if sub not in SNAPSHOT_DIRS:
                return None
        if os.path.splitext(target)[1].lower() not in IMG_EXT:
            return None
        return target

    def _serve_snapshot(self, rel):
        """Serve an evidence image. Look in the server's uploaded copies first
        (UPLOAD_DIR, any path), then the local evidence dirs (SNAPSHOT_ROOT,
        allowlisted subtrees only). Anything else -> 404; no config/faces leak."""
        rel = unquote(rel)
        for base, restrict in ((UPLOAD_DIR, False), (SNAPSHOT_ROOT, True)):
            target = self._safe_target(base, rel, restrict)
            if target and os.path.isfile(target):
                with open(target, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "max-age=31536000")  # immutable
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
        self._json(404, {"error": "not found"})

    def _recv_snapshot(self, rel):
        """Store an uploaded evidence image under UPLOAD_DIR so the server (even
        on a different machine than the cameras) can serve it. Path is sanitized
        + must be an image; oversized/garbage uploads are rejected."""
        rel = unquote(rel)
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return self._json(400, {"error": "empty body"})
        if n > MAX_UPLOAD:
            return self._json(413, {"error": "too large"})
        target = self._safe_target(UPLOAD_DIR, rel, False)
        if not target:
            return self._json(400, {"error": "bad path"})
        data = self.rfile.read(n)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        self._json(201, {"stored": rel})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = self.store.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    e = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # heartbeat / dead-client detect
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                       # client disconnected
        finally:
            self.store.unsubscribe(q)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


def main():
    if not API_KEY:
        sys.exit("set API_KEY env (the shared secret with the camera client)")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    Handler.store = Store(DB_PATH)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"event server on :{PORT}  (db={DB_PATH})  POST /events  GET /events  "
          f"GET /stream  GET/POST /snapshot  GET /health  (uploads={UPLOAD_DIR})",
          flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
