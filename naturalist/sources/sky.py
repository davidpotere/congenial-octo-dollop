"""Tonight's sky over the house, computed locally with PyEphem (no network)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import ephem

from ..hub import Hub

PLANETS = [("Mercury", ephem.Mercury), ("Venus", ephem.Venus), ("Mars", ephem.Mars),
           ("Jupiter", ephem.Jupiter), ("Saturn", ephem.Saturn)]

# Major annual showers: (name, peak month, peak day, ZHR, radiant note)
SHOWERS = [
    ("Quadrantids", 1, 3, 110, "radiant in Boötes, best before dawn"),
    ("Lyrids", 4, 22, 18, "radiant near Vega, best before dawn"),
    ("Eta Aquariids", 5, 6, 50, "low from New England, pre-dawn"),
    ("Perseids", 8, 12, 100, "radiant in Perseus, after midnight"),
    ("Orionids", 10, 21, 20, "radiant near Orion's club, after midnight"),
    ("Leonids", 11, 17, 15, "radiant in Leo, after midnight"),
    ("Geminids", 12, 14, 150, "radiant near Castor, good all night"),
]

PHASES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
          "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]


def _obs(lat: float, lon: float, when: datetime) -> ephem.Observer:
    o = ephem.Observer()
    o.lat, o.lon = str(lat), str(lon)
    o.elevation = 60
    o.date = ephem.Date(when.astimezone(timezone.utc).replace(tzinfo=None))
    return o


def _dt(d) -> int | None:
    return int(ephem.Date(d).datetime().replace(tzinfo=timezone.utc).timestamp()) if d else None


def _next(o, body, fn, horizon="0", use_center=False):
    o = o.copy()
    o.horizon = horizon
    try:
        return _dt(getattr(o, fn)(body, use_center=use_center))
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        return None


def sky_report(lat: float, lon: float, tz: str = "America/New_York", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(tz))
    o = _obs(lat, lon, now)
    sun, moon = ephem.Sun(o), ephem.Moon(o)
    # Phase angle → 8 named phases
    prev_new = ephem.previous_new_moon(o.date)
    age = o.date - prev_new
    phase_idx = int(((age / 29.530588) * 8) + 0.5) % 8
    # Astronomical darkness window tonight
    noon = _obs(lat, lon, local.replace(hour=12, minute=0, second=0, microsecond=0))
    dark_start = _next(noon, ephem.Sun(), "next_setting", "-18", True)
    dark_end = _next(noon, ephem.Sun(), "next_rising", "-18", True)
    # Where the planets sit at 9pm local tonight
    nine = _obs(lat, lon, local.replace(hour=21, minute=0, second=0, microsecond=0))
    planets = []
    for name, cls in PLANETS:
        p = cls(nine)
        alt = math.degrees(p.alt)
        rise, set_ = _next(o, cls(), "next_rising"), _next(o, cls(), "next_setting")
        planets.append({"name": name, "alt_9pm": round(alt), "az_9pm": round(math.degrees(p.az)),
                        "mag": round(p.mag, 1), "rise": rise, "set": set_, "visible_evening": alt > 10})
    today = local.date()
    showers = []
    for name, m, d, zhr, note in SHOWERS:
        peak = date(today.year, m, d)
        if peak < today - timedelta(days=2):
            peak = date(today.year + 1, m, d)
        days = (peak - today).days
        if days <= 21:
            showers.append({"name": name, "peak": peak.isoformat(), "days": days, "zhr": zhr, "note": note})
    return {
        "sunrise": _next(o, ephem.Sun(), "next_rising"),
        "sunset": _next(o, ephem.Sun(), "next_setting"),
        "civil_dawn": _next(o, ephem.Sun(), "next_rising", "-6", True),
        "civil_dusk": _next(o, ephem.Sun(), "next_setting", "-6", True),
        "golden_hour_pm": _next(o, ephem.Sun(), "next_setting", "6", True),
        "dark_start": dark_start, "dark_end": dark_end,
        "sun_alt": round(math.degrees(sun.alt), 1), "sun_az": round(math.degrees(sun.az)),
        "moon": {"phase": PHASES[phase_idx], "illum": round(moon.phase), "age_days": round(age, 1),
                 "alt": round(math.degrees(moon.alt)), "rise": _next(o, ephem.Moon(), "next_rising"),
                 "set": _next(o, ephem.Moon(), "next_setting"),
                 "next_full": _dt(ephem.next_full_moon(o.date)), "next_new": _dt(ephem.next_new_moon(o.date))},
        "planets": planets,
        "showers": showers,
        "day_length_min": _day_length(lat, lon, local),
    }


def _day_length(lat, lon, now) -> int | None:
    o = _obs(lat, lon, now.replace(hour=3, minute=0, second=0, microsecond=0))
    r, s = _next(o, ephem.Sun(), "next_rising"), _next(o, ephem.Sun(), "next_setting")
    return round((s - r) / 60) if r and s and s > r else None


class Sky:
    def __init__(self, hub: Hub):
        self.hub = hub

    async def poll(self) -> None:
        try:
            self.hub.update_now("sky", sky_report(self.hub.cfg.lat, self.hub.cfg.lon, self.hub.cfg.tz))
            self.hub.source_ok("sky")
        except Exception as e:
            self.hub.source_error("sky", e)
