"""Wire it all together: sources on timers, briefs on schedule, the web dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .alerts import Alerts
from .briefs import Briefs
from .claude import Claude
from .config import ROOT, Config, load_config
from .deliver import Deliver
from .hub import Hub
from .skycam import SkyCam
from .sources.arlo import ArloSource
from .sources.birdbuddy import BirdBuddySource
from .sources.community import Community
from .sources.haikubox import Haikubox
from .sources.sky import Sky
from .sources.tempest import Tempest

log = logging.getLogger("naturalist")
DAYS = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}


class Naturalist:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.hub = Hub(cfg)
        self.claude = Claude(cfg)
        self.deliver = Deliver(cfg)
        self.tempest = Tempest(self.hub)
        self.sky = Sky(self.hub)
        self.sources = [self.tempest, Haikubox(self.hub), BirdBuddySource(self.hub),
                        ArloSource(self.hub, self.claude), Community(self.hub), self.sky]
        self.alerts = Alerts(self.hub, self.deliver)
        self.skycam = SkyCam(self.hub)
        self.briefs = Briefs(self.hub, self.claude, self.deliver)
        self.sched = AsyncIOScheduler(timezone=cfg.tz)
        self._tasks: list[asyncio.Task] = []

    async def poll_all(self) -> None:
        await asyncio.gather(*(s.poll() for s in self.sources), return_exceptions=True)
        await self.skycam.evaluate()

    async def start(self) -> None:
        if self.tempest.enabled:
            try:
                await self.tempest.discover()   # station coordinates first, so everything else is local
            except Exception as e:
                self.hub.source_error("tempest", e)
        await self.poll_all()
        self._tasks += [asyncio.create_task(self.tempest.run_websocket()),
                        asyncio.create_task(self.tempest.run_udp())]
        every = int(self.cfg.get("schedule.poll_minutes", 5))
        self.sched.add_job(self.poll_all, "interval", minutes=every, id="poll", max_instances=1, coalesce=True)
        self.sched.add_job(self.tempest.poll, "interval", minutes=1, id="tempest", max_instances=1, coalesce=True)
        self._schedule_morning()
        day, hm = (self.cfg.get("schedule.weekly_roundup", "sun 08:30").split() + ["08:30"])[:2]
        h, m = hm.split(":")
        self.sched.add_job(self._safe(self.briefs.weekly), CronTrigger(day_of_week=DAYS.get(day[:3].lower(), "sun"),
                           hour=int(h), minute=int(m)), id="weekly")
        self.sched.add_job(self._schedule_morning, CronTrigger(hour=1, minute=5), id="plan-morning")
        self.sched.start()
        log.info("naturalist running at %.4f, %.4f", self.cfg.lat, self.cfg.lon)

    def _schedule_morning(self) -> None:
        """Fixed "HH:MM" or relative to sunrise, e.g. "sunrise-20"."""
        spec = str(self.cfg.get("schedule.morning_brief", "06:30"))
        tz = ZoneInfo(self.cfg.tz)
        now = datetime.now(tz)
        if spec.startswith("sunrise"):
            offset = int(spec[7:] or 0)
            sunrise = (self.hub.now.get("sky") or {}).get("sunrise")
            if not sunrise:
                return
            when = datetime.fromtimestamp(sunrise, tz) + timedelta(minutes=offset)
        else:
            h, m = map(int, spec.split(":"))
            when = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=1)
        if when > now:
            self.sched.add_job(self._safe(self.briefs.morning), DateTrigger(when), id="morning", replace_existing=True)
            log.info("morning brief scheduled for %s", when.strftime("%a %-I:%M%p"))

    def _safe(self, fn):
        async def run():
            try:
                await fn()
            except Exception:
                log.exception("brief failed")
        return run

    async def stop(self) -> None:
        self.sched.shutdown(wait=False)
        for t in self._tasks:
            t.cancel()


def create_app(cfg: Config | None = None, nat: Naturalist | None = None, live: bool = True) -> FastAPI:
    cfg = cfg or (nat.cfg if nat else load_config())
    nat = nat or Naturalist(cfg)
    site = ROOT / cfg.get("delivery.site.dir", "site")
    site.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if live:
            await nat.start()
        yield
        if live:
            await nat.stop()

    app = FastAPI(title="Pine Ridge Naturalist", lifespan=lifespan)
    app.state.nat = nat
    static = ROOT / "naturalist" / "web" / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/site", StaticFiles(directory=site, html=True), name="site")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    @app.get("/api/state")
    async def state():
        hub = nat.hub
        day_ago = int(time.time()) - 86400
        return {
            "home": cfg.raw["home"] | {"latitude": round(cfg.lat, 2), "longitude": round(cfg.lon, 2)},
            "demo": not live,
            "now": {k: v for k, v in hub.now.items() if k != "afd"},
            "status": hub.status,
            "feed": hub.store.events(since=day_ago, kinds=["bird", "bat", "mammal", "lightning", "rain", "alert", "sky"], limit=120),
            "community": hub.store.events(since=int(time.time()) - 3 * 86400, kinds=["community"], limit=40),
            "series": hub.store.weather_series(day_ago),
            "morning": nat.hub.store.latest_brief("morning"),
            "weekly": nat.hub.store.latest_brief("weekly"),
        }

    @app.get("/api/stream")
    async def stream():
        q = nat.hub.subscribe()

        async def gen():
            try:
                yield "retry: 5000\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=25)
                        yield f"data: {json.dumps(msg, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                nat.hub.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/brief/{kind}")
    async def make_brief(kind: str, send: bool = False):
        if kind not in ("morning", "weekly"):
            raise HTTPException(404)
        try:
            fn = nat.briefs.morning if kind == "morning" else nat.briefs.weekly
            return await fn(send=send)
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    return app
