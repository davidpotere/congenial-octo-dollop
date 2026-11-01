"""SQLite store for everything the house notices.

`events` is one timeline for every source: a bird at the feeder, a Haikubox
song, a bat pass, a fox on the Arlo, a lightning strike, a forum post.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,          -- tempest | haikubox | birdbuddy | arlo | ebird | inat | rss | nws | swpc | sky
    kind TEXT NOT NULL,            -- bird | bat | mammal | weather | lightning | rain | community | sky | alert
    ts INTEGER NOT NULL,           -- unix seconds
    title TEXT NOT NULL,
    subtitle TEXT,
    species TEXT,
    count INTEGER,
    media_url TEXT,
    audio_url TEXT,
    link TEXT,
    data TEXT,
    pushed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
CREATE TABLE IF NOT EXISTS weather (
    ts INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,            -- morning | weekly
    ts INTEGER NOT NULL,
    content TEXT NOT NULL,         -- JSON from Claude
    page TEXT,                     -- relative path under site/
    audio TEXT
);
"""

EVENT_COLS = ["id", "source", "kind", "ts", "title", "subtitle", "species", "count",
              "media_url", "audio_url", "link", "data"]


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- events -------------------------------------------------------
    def add_event(self, **ev: Any) -> bool:
        """Insert an event; returns True only if it is new (ids are stable per source)."""
        ev.setdefault("ts", int(time.time()))
        if isinstance(ev.get("data"), (dict, list)):
            ev["data"] = json.dumps(ev["data"], default=str)
        cols = [c for c in EVENT_COLS if c in ev]
        with self._lock:
            cur = self._db.execute(
                f"INSERT OR IGNORE INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [ev[c] for c in cols],
            )
            self._db.commit()
            return cur.rowcount == 1

    def events(self, since: int = 0, until: int | None = None, kinds: Iterable[str] | None = None,
               sources: Iterable[str] | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT * FROM events WHERE ts >= ?"
        args: list[Any] = [since]
        if until is not None:
            q += " AND ts < ?"
            args.append(until)
        if kinds:
            kinds = list(kinds)
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if sources:
            sources = list(sources)
            q += f" AND source IN ({','.join('?' * len(sources))})"
            args += sources
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("data"):
                try:
                    d["data"] = json.loads(d["data"])
                except ValueError:
                    pass
            out.append(d)
        return out

    def has_event(self, event_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is not None

    def mark_pushed(self, event_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET pushed = 1 WHERE id = ?", (event_id,))
            self._db.commit()

    def species_first_seen(self, species: str, since: int) -> bool:
        """True if `species` has no yard detection (haikubox/birdbuddy) before `since`."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM events WHERE species = ? AND source IN ('haikubox','birdbuddy') AND ts < ? LIMIT 1",
                (species, since),
            ).fetchone()
        return row is None

    # ---- weather ------------------------------------------------------
    def add_weather(self, ts: int, data: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO weather (ts, data) VALUES (?, ?)", (ts, json.dumps(data)))
            self._db.commit()

    def weather_series(self, since: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT ts, data FROM weather WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        return [{"ts": r["ts"], **json.loads(r["data"])} for r in rows]

    # ---- kv -----------------------------------------------------------
    def get(self, k: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set(self, k: str, v: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, json.dumps(v, default=str)))
            self._db.commit()

    # ---- briefs -------------------------------------------------------
    def add_brief(self, id: str, kind: str, ts: int, content: dict, page: str | None, audio: str | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO briefs (id, kind, ts, content, page, audio) VALUES (?,?,?,?,?,?)",
                (id, kind, ts, json.dumps(content), page, audio),
            )
            self._db.commit()

    def latest_brief(self, kind: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM briefs WHERE kind = ? ORDER BY ts DESC LIMIT 1", (kind,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d

    def briefs(self, kind: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, kind, ts, page, audio FROM briefs WHERE kind = ? ORDER BY ts DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [dict(r) for r in rows]
