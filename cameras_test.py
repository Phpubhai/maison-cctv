#!/usr/bin/env python3
"""Camera table + default_sources active/stream behaviour."""
from config import CONFIG
from main import default_sources, _camera_url

# config is a table of dicts with the expected flags
cams = {c["name"]: c for c in CONFIG["cameras"]}
assert cams["foot spa"]["active"] is True, cams["foot spa"]
assert cams["office"]["active"] is False, cams["office"]
assert cams["2nd floor"]["stream"] == "sub", cams["2nd floor"]

# _camera_url: sub appends &stream=1, main does not
assert _camera_url({"ch": 6, "stream": "sub"}).endswith("&stream=1")
assert not _camera_url({"ch": 5, "stream": "main"}).endswith("&stream=1")

# default_sources yields ONLY active cameras as (name, url), in order
srcs = default_sources()
names = [n for n, _ in srcs]
assert names == ["reception", "front door", "foot spa", "spa room"], names
assert all(isinstance(u, str) and u.startswith("rtsp") for _, u in srcs)

print("PASS: camera table + default_sources active/stream")

from main import Grabber
assert Grabber("rtsp://x").start_delay == 0.0
assert Grabber("rtsp://x", 3.0).start_delay == 3.0
print("PASS: Grabber start_delay")
