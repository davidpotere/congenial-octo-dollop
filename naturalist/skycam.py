"""Sky camera director (coming soon): a skyward GoPro + a 360 camera.

The house already knows when the sky is about to be worth filming: a gust
front on the Tempest, lightning closing in, a sunset with broken clouds, a
clear moonless night. This module turns those moments into time-lapse
triggers. It is off until `skycam.enabled: true`; see docs/skycam.md.

GoPro control uses the Open GoPro HTTP API (HERO9 and newer), ideally wired
over USB-C so the camera is powered and controlled on one cable:
  GET /gopro/camera/presets/set_group?id=1002   (time-lapse group)
  GET /gopro/camera/shutter/start | /stop
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .hub import Hub

log = logging.getLogger("naturalist.skycam")


class GoPro:
    def __init__(self, host: str):
        self.base = f"http://{host}"

    async def _get(self, path: str) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            (await c.get(self.base + path)).raise_for_status()

    async def timelapse(self, minutes: int, night: bool = False) -> None:
        await self._get("/gopro/camera/control/wired_usb?p=1")   # take control over USB
        await self._get("/gopro/camera/presets/set_group?id=1002")  # time-lapse preset group
        await self._get("/gopro/camera/shutter/start")
        await asyncio.sleep(minutes * 60)
        await self._get("/gopro/camera/shutter/stop")


class SkyCam:
    def __init__(self, hub: Hub):
        self.hub = hub
        self.enabled = bool(hub.cfg.get("skycam.enabled"))
        self.rules = hub.cfg.get("skycam.rules", []) or []
        self.gopro = GoPro(hub.cfg.get("skycam.gopro_host", "172.20.110.51:8080"))
        self.busy_until = 0.0
        hub.on_event(self.on_event)

    def conditions(self) -> set[str]:
        """Which trigger conditions are true right now."""
        out: set[str] = set()
        w, sky = self.hub.now.get("weather") or {}, self.hub.now.get("sky") or {}
        hourly = (self.hub.now.get("forecast") or {}).get("hourly", [])
        now = time.time()
        if sky.get("sunset") and 0 < sky["sunset"] - now < 45 * 60:
            cond = " ".join(str(h.get("conditions", "")) for h in hourly[:2]).lower()
            if "partly" in cond or "mostly cloudy" in cond:
                out.add("sunset_with_clouds")
        if (w.get("pressure_trend") == "falling" and (w.get("gust_mph") or 0) > 20) or \
                any((h.get("pop") or 0) >= 70 and "thunder" in str(h.get("conditions", "")).lower() for h in hourly[:2]):
            out.add("storm_approaching")
        if (w.get("lightning_last_mi") or 99) < 10 and now - (w.get("lightning_last_ts") or 0) < 900:
            out.add("lightning_close")
        return out

    async def on_event(self, ev: dict) -> None:
        if ev.get("kind") == "lightning":
            await self.evaluate()

    async def evaluate(self) -> None:
        if not self.enabled or time.time() < self.busy_until:
            return
        active = self.conditions()
        for rule in self.rules:
            if rule.get("when") in active:
                mins = int(rule.get("minutes", 30))
                self.busy_until = time.time() + mins * 60
                await self.hub.emit(id=f"skycam-{int(time.time())}", source="skycam", kind="sky",
                                    title=f"Sky camera rolling: {rule['when'].replace('_', ' ')}",
                                    subtitle=f"{mins}-minute {rule.get('action', 'timelapse')}")
                asyncio.create_task(self._run(mins, rule.get("action") == "night_lapse"))
                return

    async def _run(self, minutes: int, night: bool) -> None:
        try:
            await self.gopro.timelapse(minutes, night)
        except Exception as e:
            self.hub.source_error("skycam", e)
