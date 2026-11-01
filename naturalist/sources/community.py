"""The neighborhood: what local birders, naturalists, weather and sky people are seeing.

- eBird notable sightings nearby (API key, free)
- iNaturalist observations within a few km (no key)
- NWS Boston: active alerts + the Area Forecast Discussion (forecasters' own notes)
- NOAA SWPC planetary K-index forecast (aurora odds)
- Any RSS/Atom feeds from config (Reddit searches, club blogs, listservs)
Forums without feeds are covered by Claude's web search in the briefs.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from ..hub import Hub

log = logging.getLogger("naturalist.community")
UA = {"User-Agent": "pine-ridge-naturalist/1.0 (home weather & wildlife station)"}


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


class Community:
    def __init__(self, hub: Hub):
        self.hub = hub

    async def poll(self) -> None:
        await asyncio.gather(self.ebird(), self.inaturalist(), self.nws(), self.swpc(), self.rss())

    # ---- eBird ---------------------------------------------------------
    async def ebird(self) -> None:
        key = self.hub.cfg.secret("EBIRD_API_KEY")
        if not key:
            self.hub.source_off("ebird", "add EBIRD_API_KEY (ebird.org/api/keygen)")
            return
        cfg = self.hub.cfg
        try:
            async with httpx.AsyncClient(timeout=20, headers={"X-eBirdApiToken": key, **UA}) as c:
                r = await c.get("https://api.ebird.org/v2/data/obs/geo/recent/notable", params={
                    "lat": round(cfg.lat, 2), "lng": round(cfg.lon, 2), "dist": cfg.get("community.ebird_radius_km", 15),
                    "back": cfg.get("community.ebird_days_back", 3), "detail": "full"})
                r.raise_for_status()
            seen = set()
            for o in r.json():
                key_ = (o.get("speciesCode"), o.get("locId"), o.get("obsDt"))
                if key_ in seen:
                    continue
                seen.add(key_)
                ts = int(datetime.strptime(o["obsDt"], "%Y-%m-%d %H:%M" if " " in o["obsDt"] else "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc).timestamp()) if o.get("obsDt") else int(time.time())
                await self.hub.emit(
                    id=f"ebird-{_h(str(key_))}", source="ebird", kind="community", ts=ts, species=o.get("comName"),
                    title=f"{o.get('comName')} reported at {o.get('locName')}",
                    subtitle=f"eBird notable · {o.get('howMany') or '?'} seen",
                    link=f"https://ebird.org/checklist/{o['subId']}" if o.get("subId") else None,
                    data={"lat": o.get("lat"), "lng": o.get("lng"), "notable": True, "topic": "birding",
                          "distance_km": _km(cfg.lat, cfg.lon, o.get("lat"), o.get("lng"))},
                )
            self.hub.source_ok("ebird", f"{len(seen)} notable reports")
        except Exception as e:
            self.hub.source_error("ebird", e)

    # ---- iNaturalist -----------------------------------------------------
    async def inaturalist(self) -> None:
        cfg = self.hub.cfg
        try:
            d1 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            async with httpx.AsyncClient(timeout=20, headers=UA) as c:
                r = await c.get("https://api.inaturalist.org/v1/observations", params={
                    "lat": cfg.lat, "lng": cfg.lon, "radius": cfg.get("community.inaturalist_radius_km", 4),
                    "d1": d1, "order_by": "observed_on", "per_page": 40, "photos": "true", "geoprivacy": "open"})
                r.raise_for_status()
            results = r.json().get("results", [])
            for o in results:
                taxon = o.get("taxon") or {}
                name = taxon.get("preferred_common_name") or taxon.get("name") or "Something alive"
                photo = (o.get("photos") or [{}])[0].get("url", "")
                ts = _iso(o.get("time_observed_at") or o.get("observed_on"))
                await self.hub.emit(
                    id=f"inat-{o['id']}", source="inat", kind="community", ts=ts, species=name,
                    title=f"{name} spotted nearby", subtitle=f"iNaturalist · {o.get('place_guess') or ''}".strip(" ·"),
                    media_url=photo.replace("square", "medium") if photo else None,
                    link=o.get("uri"), data={"iconic": taxon.get("iconic_taxon_name"), "topic": "naturalist"},
                )
            self.hub.source_ok("inat", f"{len(results)} nearby observations")
        except Exception as e:
            self.hub.source_error("inat", e)

    # ---- National Weather Service (Boston/Norton office) -----------------
    async def nws(self) -> None:
        cfg = self.hub.cfg
        try:
            async with httpx.AsyncClient(timeout=20, headers={**UA, "Accept": "application/geo+json"}) as c:
                a = await c.get("https://api.weather.gov/alerts/active", params={"point": f"{cfg.lat:.4f},{cfg.lon:.4f}"})
                a.raise_for_status()
                alerts = []
                for f in a.json().get("features", []):
                    p = f["properties"]
                    alerts.append({"event": p.get("event"), "headline": p.get("headline"),
                                   "severity": p.get("severity"), "ends": p.get("ends")})
                    await self.hub.emit(
                        id=f"nws-{_h(p.get('id', p.get('headline', '')))}", source="nws", kind="alert",
                        ts=_iso(p.get("sent")), title=p.get("event") or "Weather alert", subtitle=p.get("headline"),
                        data={"severity": p.get("severity"), "description": (p.get("description") or "")[:1500]})
                self.hub.update_now("nws_alerts", alerts)
                lst = await c.get("https://api.weather.gov/products/types/AFD/locations/BOX")
                lst.raise_for_status()
                latest = (lst.json().get("@graph") or [{}])[0]
                if latest.get("@id"):
                    prod = (await c.get(latest["@id"])).json()
                    text = prod.get("productText", "")
                    self.hub.update_now("afd", {"issued": prod.get("issuanceTime"), "text": text})
            self.hub.source_ok("nws", f"{len(alerts)} active alerts")
        except Exception as e:
            self.hub.source_error("nws", e)

    # ---- Space weather -----------------------------------------------------
    async def swpc(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=20, headers=UA) as c:
                r = await c.get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json")
                r.raise_for_status()
            rows = r.json()
            header, data = rows[0], rows[1:]
            idx = {k: i for i, k in enumerate(header)}
            upcoming = [{"time": d[idx["time_tag"]], "kp": float(d[idx["kp"]]), "kind": d[idx["observed"]]}
                        for d in data if d[idx["observed"]] in ("predicted", "estimated")]
            peak = max(upcoming, key=lambda x: x["kp"]) if upcoming else None
            self.hub.update_now("kp", {"peak": peak, "series": upcoming[:24]})
            thresh = float(self.hub.cfg.get("sky.aurora_kp_alert", 6))
            if peak and peak["kp"] >= thresh:
                await self.hub.emit(
                    id=f"kp-{peak['time']}", source="swpc", kind="sky", ts=int(time.time()),
                    title=f"Aurora watch: Kp {peak['kp']:.0f} forecast", subtitle=f"peaking {peak['time']} UTC",
                    data={"kp": peak["kp"]})
            self.hub.source_ok("swpc", f"peak Kp {peak['kp'] if peak else '?'}")
        except Exception as e:
            self.hub.source_error("swpc", e)

    # ---- RSS ---------------------------------------------------------------
    async def rss(self) -> None:
        feeds = self.hub.cfg.get("community.rss", []) or []
        n = 0
        for f in feeds:
            try:
                async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as c:
                    r = await c.get(f["url"])
                    r.raise_for_status()
                parsed = feedparser.parse(r.content)
                for e in parsed.entries[:15]:
                    t = e.get("published_parsed") or e.get("updated_parsed")
                    ts = int(time.mktime(t)) if t else int(time.time())
                    if ts < time.time() - 7 * 86400:
                        continue
                    n += 1
                    await self.hub.emit(
                        id=f"rss-{_h(e.get('id') or e.get('link') or e.get('title', ''))}", source="rss",
                        kind="community", ts=ts, title=e.get("title", "(untitled)"), subtitle=f["name"],
                        link=e.get("link"), data={"topic": f.get("kind", "general"),
                                                  "summary": _strip(e.get("summary", ""))[:600]})
            except Exception as ex:
                log.info("feed %s: %s", f.get("name"), ex)
        self.hub.source_ok("rss", f"{n} recent posts across {len(feeds)} feeds")


def _iso(s) -> int:
    if not s:
        return int(time.time())
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


def _strip(html: str) -> str:
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    from math import asin, cos, radians, sin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return round(2 * 6371 * asin(sqrt(a)), 1)
