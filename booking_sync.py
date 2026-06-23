# booking_sync.py -- pulls today's bookings from the POS (GET /cctvBookings, see
# contracts/cctv-api.v1.md) and feeds the IdentityResolver a ROSTER: the
# therapist names expected today. A face match to someone NOT on the roster is
# then ignored (cuts wrong matches).
#
# LIMITATION: cctvBookings has therapistName + startTime but NO room, so this
# can only narrow WHO is on shift -- it cannot say WHICH ROOM a therapist is in.
# Room-level booking matching would need a `room` field added to the contract.
import threading
import time
import urllib.request

import json as _json

_ACTIVE = {"pending", "confirmed", "checked_in"}


def roster_from_bookings(bookings):
    """Set of therapist names from today's still-active bookings."""
    return {b["therapistName"] for b in bookings or []
            if b.get("therapistName") and b.get("status") in _ACTIVE}


class RosterSync(threading.Thread):
    def __init__(self, resolver, fetch, cfg):
        super().__init__(daemon=True)
        self.resolver = resolver
        self.fetch = fetch          # callable() -> [booking dict, ...]
        self.poll = cfg.get("roster_poll_secs", 300)
        self.enabled = bool(cfg.get("roster", {}).get("enabled"))

    def refresh_once(self):
        roster = roster_from_bookings(self.fetch() or [])
        if roster:                  # fail-open: never lock to an empty roster
            self.resolver.set_roster(roster)

    def run(self):
        if not self.enabled:
            print("roster sync disabled", flush=True)
            return
        while True:
            try:
                self.refresh_once()
            except Exception as e:
                print(f"roster sync error: {e}", flush=True)
            time.sleep(self.poll)


def make_bookings_fetch(cfg):
    """HTTP fetcher: GET <pos_api.base_url>/cctvBookings -> [bookings]. Returns
    an empty list on any error so the loop just retries next poll."""
    api = cfg.get("pos_api", {})
    base = api.get("base_url", "").rstrip("/")
    key = api.get("api_key", "")
    url = base + "/cctvBookings" if base else None

    def fetch():
        if not url:
            return []
        req = urllib.request.Request(url, headers={"x-cctv-key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return []
                body = _json.loads(resp.read().decode("utf-8"))
                return body.get("bookings", []) if isinstance(body, dict) else body
        except Exception:
            return []

    return fetch
