"""WeatherFlow Tempest: station discovery, observations, forecast, and live events.

REST:      https://swd.weatherflow.com/swd/rest  (token auth)
WebSocket: wss://ws.weatherflow.com/swd/data     (lightning, rain start, rapid wind)
LAN UDP:   port 50222 broadcast from the hub     (works with the internet down)
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time

import httpx
import websockets

from ..hub import Hub

log = logging.getLogger("naturalist.tempest")
REST = "https://swd.weatherflow.com/swd/rest"
WS = "wss://ws.weatherflow.com/swd/data"

# obs_st field order (Tempest UDP / websocket "obs" arrays)
OBS_ST = ["ts", "wind_lull", "wind_avg", "wind_gust", "wind_direction", "wind_interval", "station_pressure",
          "air_temperature", "relative_humidity", "brightness", "uv", "solar_radiation", "precip",
          "precip_type", "lightning_strike_avg_distance", "lightning_strike_count", "battery", "report_interval"]


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32, 1)


def ms_to_mph(v):
    return None if v is None else round(v * 2.23694, 1)


def mm_to_in(v):
    return None if v is None else round(v / 25.4, 2)


def km_to_mi(v):
    return None if v is None else round(v * 0.621371, 1)


class Tempest:
    def __init__(self, hub: Hub):
        self.hub = hub
        self.token = hub.cfg.secret("TEMPEST_TOKEN")
        self.station_id = hub.cfg.get("tempest.station_id")
        self.device_id: int | None = None
        self.station: dict = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _get(self, path: str, **params) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{REST}{path}", params={"token": self.token, **params})
            r.raise_for_status()
            return r.json()

    async def discover(self) -> None:
        data = await self._get("/stations")
        stations = data.get("stations", [])
        if not stations:
            raise RuntimeError("token has no stations")
        st = next((s for s in stations if s["station_id"] == self.station_id), stations[0])
        self.station = st
        self.station_id = st["station_id"]
        # the Tempest sensor ("ST") device carries observations and events
        for d in st.get("devices", []):
            if d.get("device_type") == "ST":
                self.device_id = d["device_id"]
        if st.get("latitude") and st.get("longitude"):
            self.hub.cfg.set_location(st["latitude"], st["longitude"])
        self.hub.store.set("tempest_station", {"id": self.station_id, "name": st.get("name"),
                                               "elevation": st.get("station_meta", {}).get("elevation")})
        log.info("Tempest station %s (%s), device %s", st.get("name"), self.station_id, self.device_id)

    async def poll(self) -> None:
        """Current observation + forecast. Called every few minutes."""
        if not self.enabled:
            self.hub.source_off("tempest", "add TEMPEST_TOKEN to .env")
            return
        try:
            if not self.station_id or not self.device_id:
                await self.discover()
            obs = (await self._get(f"/observations/station/{self.station_id}")).get("obs") or [{}]
            o = obs[0]
            fc = await self._get("/better_forecast", station_id=self.station_id, units_temp="f",
                                 units_wind="mph", units_pressure="inhg", units_precip="in", units_distance="mi")
            now = self._shape_now(o, fc)
            self.hub.update_now("weather", now)
            self.hub.update_now("forecast", self._shape_forecast(fc))
            self.hub.store.add_weather(now["ts"], {k: now[k] for k in (
                "temp_f", "humidity", "wind_mph", "gust_mph", "pressure_inhg", "rain_today_in", "uv",
                "solar", "lightning_1h") if k in now})
            self.hub.source_ok("tempest", self.station.get("name", ""))
        except Exception as e:
            self.hub.source_error("tempest", e)

    def _shape_now(self, o: dict, fc: dict) -> dict:
        cc = fc.get("current_conditions", {})
        return {
            "ts": int(o.get("timestamp") or time.time()),
            "station": self.station.get("name"),
            "temp_f": c_to_f(o.get("air_temperature")),
            "feels_f": c_to_f(o.get("feels_like")),
            "dew_f": c_to_f(o.get("dew_point")),
            "humidity": o.get("relative_humidity"),
            "wind_mph": ms_to_mph(o.get("wind_avg")),
            "gust_mph": ms_to_mph(o.get("wind_gust")),
            "wind_dir": o.get("wind_direction"),
            "pressure_inhg": round(o["sea_level_pressure"] * 0.02953, 2) if o.get("sea_level_pressure") else None,
            "pressure_trend": o.get("pressure_trend"),
            "uv": o.get("uv"),
            "solar": o.get("solar_radiation"),
            "brightness": o.get("brightness"),
            "rain_today_in": mm_to_in(o.get("precip_accum_local_day")),
            "rain_rate_in": mm_to_in((o.get("precip") or 0) * 60),
            "lightning_1h": o.get("lightning_strike_count_last_1hr"),
            "lightning_3h": o.get("lightning_strike_count_last_3hr"),
            "lightning_last_mi": km_to_mi(o.get("lightning_strike_last_distance")),
            "lightning_last_ts": o.get("lightning_strike_last_epoch"),
            "conditions": cc.get("conditions"),
            "icon": cc.get("icon"),
            "is_precip_local_day_rain_check": cc.get("is_precip_local_day_rain_check"),
        }

    def _shape_forecast(self, fc: dict) -> dict:
        f = fc.get("forecast", {})
        hourly = [{
            "ts": h.get("time"), "temp_f": h.get("air_temperature"), "feels_f": h.get("feels_like"),
            "pop": h.get("precip_probability"), "precip_in": h.get("precip"), "icon": h.get("icon"),
            "conditions": h.get("conditions"), "wind_mph": h.get("wind_avg"), "gust_mph": h.get("wind_gust"),
            "wind_dir": h.get("wind_direction"), "uv": h.get("uv"), "humidity": h.get("relative_humidity"),
        } for h in (f.get("hourly") or [])[:48]]
        daily = [{
            "ts": d.get("day_start_local"), "high_f": d.get("air_temp_high"), "low_f": d.get("air_temp_low"),
            "pop": d.get("precip_probability"), "icon": d.get("icon"), "conditions": d.get("conditions"),
            "sunrise": d.get("sunrise"), "sunset": d.get("sunset"), "precip_type": d.get("precip_type"),
        } for d in (f.get("daily") or [])[:10]]
        return {"hourly": hourly, "daily": daily}

    # ---- live events ---------------------------------------------------
    async def run_websocket(self) -> None:
        if not (self.enabled and self.hub.cfg.get("tempest.websocket", True)):
            return
        backoff = 5
        while True:
            try:
                if not self.device_id:
                    await self.discover()
                async with websockets.connect(f"{WS}?token={self.token}", ping_interval=30) as ws:
                    for t in ("listen_start", "listen_rapid_start"):
                        await ws.send(json.dumps({"type": t, "device_id": self.device_id, "id": t}))
                    backoff = 5
                    async for raw in ws:
                        await self._handle(json.loads(raw))
            except Exception as e:
                log.warning("tempest websocket: %s (reconnect in %ss)", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def run_udp(self) -> None:
        """Listen for the hub's LAN broadcasts. No token or internet needed."""
        if not self.hub.cfg.get("tempest.udp", True):
            return
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", 50222))
        except OSError as e:
            log.info("tempest UDP not available: %s", e)
            return
        sock.setblocking(False)
        while True:
            raw = await loop.sock_recv(sock, 4096)
            try:
                await self._handle(json.loads(raw))
            except Exception:
                log.debug("bad udp packet", exc_info=True)

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "evt_strike":
            ts, dist_km, energy = (msg.get("evt") or [time.time(), None, None])[:3]
            mi = km_to_mi(dist_km)
            self.hub.broadcast({"type": "lightning", "distance_mi": mi})
            await self.hub.emit(id=f"strike-{int(ts)}", source="tempest", kind="lightning", ts=int(ts),
                                title=f"Lightning ~{mi} mi away", data={"distance_km": dist_km, "energy": energy})
        elif t == "evt_precip":
            ts = int((msg.get("evt") or [time.time()])[0])
            self.hub.broadcast({"type": "rain_start"})
            await self.hub.emit(id=f"rainstart-{ts}", source="tempest", kind="rain", ts=ts,
                                title="Rain just started at the house")
        elif t == "rapid_wind":
            ob = msg.get("ob") or []
            if len(ob) >= 3:
                self.hub.broadcast({"type": "wind", "mph": ms_to_mph(ob[1]), "dir": ob[2]})
        elif t == "obs_st":
            for row in msg.get("obs") or []:
                o = dict(zip(OBS_ST, row))
                w = dict(self.hub.now.get("weather") or {})
                w.update({"ts": o["ts"], "temp_f": c_to_f(o["air_temperature"]), "humidity": o["relative_humidity"],
                          "wind_mph": ms_to_mph(o["wind_avg"]), "gust_mph": ms_to_mph(o["wind_gust"]),
                          "wind_dir": o["wind_direction"], "uv": o["uv"], "solar": o["solar_radiation"],
                          "brightness": o["brightness"]})
                self.hub.update_now("weather", w)
