#!/usr/bin/env python3
"""Camera table + default_sources active/stream behaviour."""
from config import CONFIG
from main import default_sources, _camera_url

# config is a table of dicts; every row carries the four flags, right types
cams = {c["name"]: c for c in CONFIG["cameras"]}
for c in CONFIG["cameras"]:
    assert isinstance(c["name"], str) and isinstance(c["ch"], int)
    assert isinstance(c["active"], bool) and c["stream"] in ("main", "sub")
assert cams["2nd floor"]["stream"] == "sub", cams["2nd floor"]

# _camera_url: sub appends &stream=1, main does not
assert _camera_url({"ch": 6, "stream": "sub"}).endswith("&stream=1")
assert not _camera_url({"ch": 5, "stream": "main"}).endswith("&stream=1")

# default_sources yields ONLY the active cameras as (name, url), in table order
srcs = default_sources()
names = [n for n, _ in srcs]
expected = [c["name"] for c in CONFIG["cameras"] if c.get("active", True)]
assert names == expected, (names, expected)
assert all(isinstance(u, str) and u.startswith("rtsp") for _, u in srcs)

print("PASS: camera table + default_sources active/stream")

from main import Grabber
assert Grabber("rtsp://x").start_delay == 0.0
assert Grabber("rtsp://x", 3.0).start_delay == 3.0
print("PASS: Grabber start_delay")
