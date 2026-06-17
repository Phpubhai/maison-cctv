# Local event history in SQLite -- the camera's full, queryable timeline
# (time / who / what / image). One writer (the monitor) plus a push worker
# thread, so all access is serialized behind a lock on one connection.
#
# This is the source of truth kept in the shop. A subset (penalty + customer)
# is pushed to the POS from here; see push_worker. Evidence images stay on
# disk -- only their path is stored.
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  camera       TEXT NOT NULL,
  actor_type   TEXT,
  actor_name   TEXT,
  therapist_id TEXT,
  event        TEXT NOT NULL,
  description  TEXT,
  severity     TEXT,
  image_path   TEXT,
  pushed       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_name);
CREATE INDEX IF NOT EXISTS idx_events_pushed ON events(pushed);
"""

# events that belong on the POS timeline: staff penalties + anything about a
# customer. Mirrors what the table shows.
_PENALTY = {"SLEEPING", "PHONE USE", "GREETING MISSED", "ROOM MESSY",
            "OBJECT ON FLOOR"}


def is_pushable(event, severity, actor_type):
    return (event in _PENALTY or severity in ("warning", "alert")
            or actor_type == "customer")


class EventStore:
    def __init__(self, db_path):
        self.lock = threading.Lock()
        # check_same_thread=False: the logger thread writes, the push worker
        # thread reads/updates -- the lock keeps it safe.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(_SCHEMA)
            self.conn.commit()

    def add(self, ts, camera, actor_type, actor_name, therapist_id,
            event, description, severity, image_path=None):
        """Insert one event; returns its row id. Rows that don't belong on
        the POS timeline are born pushed=1 ("no push needed") so the push
        queue only ever holds pushable rows -- otherwise ENTER/LEAVE pile up
        as pushed=0 and starve the worker's LIMIT window."""
        pushed = 0 if is_pushable(event, severity, actor_type) else 1
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO events (ts,camera,actor_type,actor_name,"
                "therapist_id,event,description,severity,image_path,pushed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, camera, actor_type, actor_name, therapist_id,
                 event, description, severity, image_path, pushed))
            self.conn.commit()
            return cur.lastrowid

    def set_image(self, row_id, image_path):
        with self.lock:
            self.conn.execute("UPDATE events SET image_path=? WHERE id=?",
                              (image_path, row_id))
            self.conn.commit()

    def fetch_unpushed(self, limit=50):
        """Pushable rows not yet sent to the POS, oldest first."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE pushed=0 ORDER BY id LIMIT ?",
                (limit,)).fetchall()
        return [r for r in rows
                if is_pushable(r["event"], r["severity"], r["actor_type"])]

    def mark_pushed(self, ids):
        if not ids:
            return
        with self.lock:
            self.conn.executemany("UPDATE events SET pushed=1 WHERE id=?",
                                  [(i,) for i in ids])
            self.conn.commit()

    def query(self, actor=None, since=None, severity=None, limit=200):
        """Read the timeline (for local viewing / reports). Filters optional."""
        sql = "SELECT * FROM events WHERE 1=1"
        args = []
        if actor:
            sql += " AND actor_name=?"; args.append(actor)
        if since:
            sql += " AND ts>=?"; args.append(since)
        if severity:
            sql += " AND severity=?"; args.append(severity)
        sql += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def purge_old(self, days):
        """Delete rows older than `days`; return image paths now orphaned so
        the caller can remove the files."""
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(time.time() - days * 86400))
        with self.lock:
            paths = [r["image_path"] for r in self.conn.execute(
                "SELECT image_path FROM events WHERE ts < ? AND image_path IS NOT NULL",
                (cutoff,)).fetchall()]
            self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.conn.commit()
        return paths

    def close(self):
        with self.lock:
            self.conn.close()
