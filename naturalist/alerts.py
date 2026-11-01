"""Live phone pushes for the moments worth looking up from your coffee."""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from .deliver import Deliver
from .hub import Hub

log = logging.getLogger("naturalist.alerts")


class Alerts:
    def __init__(self, hub: Hub, deliver: Deliver):
        self.hub, self.deliver = hub, deliver
        self.a = hub.cfg.get("alerts", {}) or {}
        self._last: dict[str, float] = {}  # rate-limit per alert type
        hub.on_event(self.handle)

    def _quiet(self) -> bool:
        q = self.a.get("quiet_hours")
        if not q:
            return False
        now = datetime.now(ZoneInfo(self.hub.cfg.tz)).time()
        start, end = (dtime.fromisoformat(x) for x in q)
        return (start <= now or now < end) if start > end else (start <= now < end)

    def _cool(self, key: str, seconds: int) -> bool:
        if time.time() - self._last.get(key, 0) < seconds:
            return False
        self._last[key] = time.time()
        return True

    async def handle(self, ev: dict) -> None:
        if ev.get("ts", 0) < time.time() - 3 * 3600:  # never push about stale backfill
            return
        data = ev.get("data") or {}
        kind, src = ev["kind"], ev["source"]
        msg = None  # (title, body, tags, priority, severe)
        if kind == "lightning" and (data.get("distance_km") or 999) <= self.a.get("lightning_km", 15):
            if self._cool("lightning", 1800):
                msg = ("⚡ Lightning nearby", ev["title"] + " — time to come inside.", "zap", 4, True)
        elif kind == "rain" and self.a.get("rain_start", True) and self._cool("rain", 3 * 3600):
            msg = ("🌧 Rain at the house", "The Tempest just felt the first drops.", "umbrella", 3, False)
        elif kind == "alert" and src == "nws":
            msg = (f"⚠ {ev['title']}", ev.get("subtitle") or "", "warning", 4, True)
        elif src == "ebird" and data.get("notable"):
            dist = data.get("distance_km")
            if dist is not None and dist <= self.a.get("notable_bird_km", 8) and self._cool(f"eb-{ev.get('species')}", 86400):
                msg = ("🔭 Notable bird nearby", f"{ev['title']} ({dist} km away)", "bird", 3, False)
        elif src in ("haikubox", "birdbuddy") and kind == "bird" and data.get("first_this_year") \
                and self.a.get("new_yard_species", True):
            msg = ("✨ New yard bird this year", f"{ev['species']} — {ev.get('subtitle') or 'heard by the Haikubox'}",
                   "bird", 3, False)
        elif kind == "bat" and self.a.get("first_bat_of_night", True) and self._cool("bat", 10 * 3600):
            msg = ("🦇 The bats are out", f"First pass tonight: {ev.get('species') or 'a bat'}.", "bat", 2, False)
        elif src == "arlo" and self.a.get("arlo_animal", True) and self._cool(f"arlo-{ev.get('species')}", 1800):
            msg = ("🦊 " + ev["title"], ev.get("subtitle") or "Caught on camera.", "camera", 3, False)
        elif src == "swpc" and self._cool("kp", 6 * 3600):
            msg = ("🌌 " + ev["title"], "Look north after dark if it's clear.", "milky_way", 4, False)
        if not msg:
            return
        title, body, tags, prio, severe = msg
        if self._quiet() and not severe:
            return
        await self.deliver.push(title, body, tags=tags, priority=prio, image=ev.get("media_url"), click=ev.get("link"))
        self.hub.store.mark_pushed(ev["id"])
