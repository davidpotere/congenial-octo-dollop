"""Haikubox (birds and bats) via its public API.

Endpoints used (public, keyed by the box serial):
  GET https://api.haikubox.com/haikubox/{serial}/detections?hours=N
  GET https://api.haikubox.com/haikubox/{serial}/daily-count?date=YYYY-MM-DD
Field names differ between box generations, so parsing here is deliberately
forgiving. If something looks off, run `python -m naturalist probe haikubox`
to print the raw responses.
"""
from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..hub import Hub

API = "https://api.haikubox.com/haikubox"

# Bats of Massachusetts. Haikubox bat detections are matched against these.
MA_BATS = {
    "big brown bat", "little brown bat", "eastern red bat", "hoary bat", "silver-haired bat",
    "tricolored bat", "northern long-eared bat", "eastern small-footed bat", "evening bat",
    "indiana bat", "little brown myotis", "northern myotis",
}


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _ts(v) -> int:
    if v is None:
        return int(time.time())
    if isinstance(v, (int, float)):
        return int(v / 1000 if v > 1e12 else v)
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


def is_bat(det: dict, name: str) -> bool:
    taxon = str(_first(det, "taxon", "type", "category", "class", default="")).lower()
    return "bat" in taxon or "chiroptera" in taxon or name.lower() in MA_BATS or name.lower().endswith(" bat")


class Haikubox:
    def __init__(self, hub: Hub):
        self.hub = hub
        self.serial = hub.cfg.secret("HAIKUBOX_SERIAL")

    @property
    def enabled(self) -> bool:
        return bool(self.serial)

    async def fetch(self, path: str, **params):
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{API}/{self.serial}{path}", params=params)
            r.raise_for_status()
            return r.json()

    async def poll(self) -> None:
        if not self.enabled:
            self.hub.source_off("haikubox", "add HAIKUBOX_SERIAL to .env")
            return
        try:
            raw = await self.fetch("/detections", hours=6)
            dets = raw.get("detections", raw) if isinstance(raw, dict) else raw
            min_score = float(self.hub.cfg.get("haikubox.bird_min_score", 0) or 0)
            year_start = int(datetime(datetime.now().year, 1, 1).timestamp())
            for d in dets or []:
                name = _first(d, "cn", "commonName", "common_name", "species", default="Unknown")
                sci = _first(d, "sn", "scientificName", "scientific_name", default="")
                score = float(_first(d, "score", "confidence", default=1) or 1)
                if score < min_score:
                    continue
                ts = _ts(_first(d, "dt", "timestamp", "time", "detectedAt"))
                kind = "bat" if is_bat(d, name) else "bird"
                first_this_year = self.hub.store.species_first_seen(name, year_start)
                await self.hub.emit(
                    id=f"hb-{name}-{ts}", source="haikubox", kind=kind, ts=ts, species=name,
                    title=name, subtitle=sci or None,
                    audio_url=_first(d, "audioUrl", "audio_url", "clipUrl", "url"),
                    media_url=_first(d, "spectrogramUrl", "spectrogram", "imageUrl"),
                    data={"score": score, "first_this_year": first_this_year,
                          "freq_khz": _first(d, "frequency", "freq_khz")},
                )
            today = datetime.now().strftime("%Y-%m-%d")
            counts = await self.fetch("/daily-count", date=today)
            species = counts.get("species", counts) if isinstance(counts, dict) else counts
            shaped = sorted(
                ({"name": _first(s, "cn", "commonName", "species", default="?"),
                  "count": int(_first(s, "count", "detections", default=0) or 0),
                  "bat": is_bat(s, _first(s, "cn", "commonName", "species", default=""))}
                 for s in (species or [])),
                key=lambda s: -s["count"])
            self.hub.update_now("haikubox_today", shaped)
            self.hub.source_ok("haikubox", f"{len(shaped)} species today")
        except Exception as e:
            self.hub.source_error("haikubox", e)
