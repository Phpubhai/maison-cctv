#!/usr/bin/env python3
"""Tiny stand-in for the VPS /events endpoint, for local testing only.
Implements the same contract: POST /events (X-API-Key), GET /events.

  API_KEY env (default 'testkey'), PORT env (default 8099).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

API_KEY = os.environ.get("API_KEY", "testkey")
PORT = int(os.environ.get("PORT", "8099"))
_STORE = []


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        if self.headers.get("X-API-Key") != API_KEY:
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    def do_POST(self):
        if self.path.rstrip("/") != "/events":
            return self._json(404, {"error": "not found"})
        if not self._auth_ok():
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        events = data if isinstance(data, list) else [data]
        for e in events:
            if not e.get("camera_id") or not e.get("label"):
                return self._json(400, {"error": "missing camera_id or label"})
        _STORE.extend(events)
        self._json(201, {"stored": len(events), "events": events})

    def do_GET(self):
        if self.path.rstrip("/") != "/events":
            return self._json(404, {"error": "not found"})
        if not self._auth_ok():
            return
        self._json(200, {"count": len(_STORE), "events": _STORE})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"mock VPS on http://127.0.0.1:{PORT}/events (key={API_KEY})", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
