# Push the penalty+customer subset of the local timeline to the POS, and
# serve evidence images read-only on the LAN so the POS "view image" link
# resolves in-shop (images never reach the cloud -- PDPA).
#
# Both run as daemon threads started from main(). Disabled by default until
# the POS Cloud Function exists and POS_API_KEY is set (local_settings.py).
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import json as _json

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


# ── local image server ────────────────────────────────────────────────────
def start_image_server(cfg):
    """Serve ONLY the evidence folders read-only on the LAN. Returns the base
    URL the push worker stamps onto rows, or None if disabled/failed.

    Security: the served root (the app folder) holds secrets, biometric data
    and models, so this serves NOTHING by default -- only files that resolve
    inside an allowlisted evidence dir (Penalty/, behavior_events/). Any other
    path, or a traversal escape, gets 404."""
    spec = cfg.get("image_server", {})
    if not spec.get("enabled"):
        return None
    host, port = spec["host"], spec["port"]
    # allowlist: {url-prefix -> real absolute dir}, prefix = the dir basename
    allowed = {}
    for d in (cfg["penalty_dir"], cfg["evidence_dir"]):
        allowed[os.path.basename(d.rstrip("/\\"))] = os.path.realpath(d)

    class EvidenceHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_error(405)

        def do_GET(self):
            rel = urllib.parse.unquote(self.path.lstrip("/")).replace("\\", "/")
            parts = rel.split("/", 1)
            if len(parts) != 2 or parts[0] not in allowed:
                self.send_error(404)
                return
            base = allowed[parts[0]]
            target = os.path.realpath(os.path.join(base, parts[1]))
            # must stay inside the allowlisted dir (blocks ../ escapes) and be
            # an image file that exists
            if (os.path.commonpath([base, target]) != base
                    or not os.path.isfile(target)
                    or os.path.splitext(target)[1].lower() not in _MIME):
                self.send_error(404)
                return
            with open(target, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", _MIME[os.path.splitext(target)[1].lower()])
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass  # quiet

    try:
        httpd = HTTPServer((host, port), EvidenceHandler)
    except OSError as e:
        print(f"image server disabled ({e})", flush=True)
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://{host}:{port}"
    print(f"image server on {base} (evidence dirs only: {', '.join(allowed)})", flush=True)
    return base


def _image_url(base, image_path, cfg):
    """Map a local evidence path to its LAN URL under the served root."""
    if not base or not image_path:
        return None
    root = os.path.dirname(cfg["penalty_dir"])
    try:
        rel = os.path.relpath(image_path, root).replace("\\", "/")
    except ValueError:
        return None
    return f"{base}/{rel}"


def _iso7(s):
    """'YYYY-MM-DD HH:MM:SS' (shop local) -> ISO-8601 +07:00."""
    return s.replace(" ", "T") + "+07:00" if s else None


def presence_payload(r):
    """Map a presence_intervals row to the /cctvPresence request body."""
    return {
        "id": r["id"],
        "therapist": r["therapist"],
        "therapistId": r["therapist_id"],
        "room": r["room"],
        "status": r["status"],
        "startedAt": _iso7(r["started_at"]),
        "endedAt": _iso7(r["ended_at"]),
        "confidence": r["confidence"],
        "camera": r["camera"],
    }


# ── push worker ────────────────────────────────────────────────────────────
class PushWorker(threading.Thread):
    """Polls the store for unpushed penalty/customer rows and POSTs them to
    the POS cctvTimeline Cloud Function. Marks rows pushed only on success,
    so a POS/network outage just delays them."""

    def __init__(self, store, cfg, image_base):
        super().__init__(daemon=True)
        self.store = store
        self.cfg = cfg
        self.image_base = image_base
        pt = cfg.get("pos_timeline", {})
        self.enabled = pt.get("enabled", False)
        self.poll = pt.get("poll_secs", 5)
        self.batch = pt.get("batch", 25)
        # base_url + key from the pos_api block (shared with arrival pushing)
        api = cfg.get("pos_api", {})
        self.url = (api.get("base_url", "").rstrip("/") + "/cctvTimeline") if api.get("base_url") else None
        self.presence_url = (api.get("base_url", "").rstrip("/") + "/cctvPresence"
                             if api.get("base_url") else None)
        self.key = api.get("api_key", "")

    def run(self):
        if not self.enabled or not self.url:
            print("pos timeline push disabled (no pos_api.base_url / pos_timeline.enabled)", flush=True)
            return
        print(f"pos timeline push -> {self.url} every {self.poll}s", flush=True)
        while True:
            try:
                self._flush()
                self._flush_presence()
            except Exception as e:
                print(f"timeline push error: {e}", flush=True)
            time.sleep(self.poll)

    def _flush(self):
        rows = self.store.fetch_unpushed(self.batch)
        sent = []
        for r in rows:
            payload = {
                "id": r["id"], "ts": r["ts"], "camera": r["camera"],
                "actorType": r["actor_type"], "actorName": r["actor_name"],
                "therapistId": r["therapist_id"], "event": r["event"],
                "description": r["description"], "severity": r["severity"],
                "imageUrl": _image_url(self.image_base, r["image_path"], self.cfg),
            }
            if self._post(payload):
                sent.append(r["id"])
            else:
                break  # POS unreachable -- stop, retry whole batch next cycle
        self.store.mark_pushed(sent)
        if sent:
            print(f"pushed {len(sent)} timeline row(s) to POS", flush=True)

    def _flush_presence(self):
        if not self.presence_url:
            return
        rows = self.store.fetch_unpushed_presence(self.batch)
        sent = []
        for r in rows:
            if self._post_presence(presence_payload(r)):
                sent.append(r["id"])
            else:
                break  # POS unreachable -- retry the whole batch next cycle
        self.store.mark_presence_pushed(sent)
        if sent:
            print(f"pushed {len(sent)} presence row(s) to POS", flush=True)

    def _post_presence(self, payload):
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.presence_url, data=data, method="POST",
                                     headers={"content-type": "application/json",
                                              "x-cctv-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _post(self, payload):
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST",
                                     headers={"content-type": "application/json",
                                              "x-cctv-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
