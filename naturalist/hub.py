"""The hub: shared state, the event timeline, and live fan-out to browsers and alerts."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .config import Config
from .store import Store

log = logging.getLogger("naturalist.hub")

Listener = Callable[[dict], Awaitable[None]]


class Hub:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.data_dir / "naturalist.db")
        self.now: dict[str, Any] = {}          # latest snapshot per source: now["tempest"], now["sky"], ...
        self._subscribers: set[asyncio.Queue] = set()
        self._listeners: list[Listener] = []
        self.status: dict[str, dict] = {}      # per-source health for the dashboard

    # ---- live state ---------------------------------------------------
    def update_now(self, key: str, value: Any) -> None:
        self.now[key] = value
        self.broadcast({"type": "now", "key": key, "value": value})

    def source_ok(self, name: str, note: str = "") -> None:
        self.status[name] = {"ok": True, "ts": int(time.time()), "note": note}

    def source_error(self, name: str, err: Exception | str) -> None:
        log.warning("%s: %s", name, err)
        self.status[name] = {"ok": False, "ts": int(time.time()), "note": str(err)[:300]}

    def source_off(self, name: str, why: str) -> None:
        self.status[name] = {"ok": None, "ts": int(time.time()), "note": why}

    # ---- events -------------------------------------------------------
    async def emit(self, **event: Any) -> bool:
        """Record an event. New events go to browsers (SSE) and alert listeners."""
        is_new = self.store.add_event(**event)
        if is_new:
            ev = dict(event)
            self.broadcast({"type": "event", "event": ev})
            for fn in self._listeners:
                try:
                    await fn(ev)
                except Exception:  # an alert failure must never stop collection
                    log.exception("listener failed")
        return is_new

    def on_event(self, fn: Listener) -> None:
        self._listeners.append(fn)

    # ---- browser fan-out (Server-Sent Events) -------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
