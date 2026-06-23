# corrections_sync.py -- pulls reception's name corrections from the POS and
# applies them to the IdentityResolver. The POS WRITE side (a `corrections`
# feed reception writes when it taps an avatar) is Plan 3; here we only consume
# whatever the fetcher returns. Daemon thread; fetcher injected for testing.
import threading
import time
import urllib.request

import json as _json


class CorrectionsSync(threading.Thread):
    def __init__(self, resolver, fetch, cfg):
        super().__init__(daemon=True)
        self.resolver = resolver
        self.fetch = fetch          # callable() -> [{"id","trackUid","name"}, ...]
        self.poll = cfg.get("corrections_poll_secs", 5)
        self.enabled = bool(cfg.get("corrections", {}).get("enabled"))
        self._seen = set()          # correction ids already applied (idempotent)

    def apply_once(self):
        for c in self.fetch() or []:
            cid = c.get("id")
            if cid in self._seen:
                continue
            uid, name = c.get("trackUid"), c.get("name")
            if uid and name:
                self.resolver.apply_correction(uid, name)
            self._seen.add(cid)     # mark seen even if malformed (won't requeue)

    def run(self):
        if not self.enabled:
            print("corrections sync disabled", flush=True)
            return
        while True:
            try:
                self.apply_once()
            except Exception as e:
                print(f"corrections sync error: {e}", flush=True)
            time.sleep(self.poll)


def make_corrections_fetch(cfg):
    """HTTP fetcher: GET <pos_api.base_url>/cctvCorrections -> list. Returns an
    empty list on any error so the sync loop just retries next poll."""
    api = cfg.get("pos_api", {})
    base = api.get("base_url", "").rstrip("/")
    key = api.get("api_key", "")
    url = base + "/cctvCorrections" if base else None

    def fetch():
        if not url:
            return []
        req = urllib.request.Request(url, headers={"x-cctv-key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return []
                body = _json.loads(resp.read().decode("utf-8"))
                return body.get("corrections", []) if isinstance(body, dict) else body
        except Exception:
            return []

    return fetch
