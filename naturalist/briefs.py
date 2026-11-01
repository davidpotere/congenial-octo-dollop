"""Morning wake-up and end-of-week roundup.

1. Gather: everything the house noticed (events), the weather record, the
   forecast, tonight's sky, and what the community is talking about.
2. Research: Claude scans local weather/birding/naturalist/astronomy forums.
3. Write: Claude composes the brief as structured JSON (and a spoken script).
4. Render: a beautiful standalone page (photos and audio copied locally so
   the page keeps working after Bird Buddy's signed links expire).
5. Narrate + deliver: audio file, email, phone push.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .claude import Claude
from .config import ROOT
from .hub import Hub
from .narration import narrate

log = logging.getLogger("naturalist.briefs")

ICONS = ["weather", "birds", "bats", "critters", "sky", "community", "alert", "plants", "insects"]

VOICE = """You are the resident naturalist of {home} in {town}: equal parts field-guide author,
weather nerd, and backyard astronomer. You write for one family (and the friends they share with).
Your voice is warm, precise and a little wry, like a great nature essayist writing a postcard.
Ground every claim in the data you are given; name species exactly; give numbers with units
(°F, mph, inches, miles). Never invent sightings, counts or forum posts. If a source is quiet,
say so briefly or skip it. Prefer the vivid specific detail (the 6:12am Carolina Wren, the
gust to 31 mph) over generalities. Point out connections across sources: a cold front and
a pulse of migrants; a clear moonless night and bat activity; a rare bird two towns over."""

MORNING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "greeting", "sections", "narration", "push", "hero_event_id"],
    "properties": {
        "headline": {"type": "string", "description": "One evocative line, max 12 words"},
        "greeting": {"type": "string", "description": "2 sentences: the shape of the day ahead"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["icon", "title", "body"],
            "properties": {"icon": {"type": "string", "enum": ICONS}, "title": {"type": "string"},
                           "body": {"type": "string", "description": "2-4 sentences"}}}},
        "narration": {"type": "string", "description": "Spoken script, 60-100 seconds read aloud, no markup"},
        "push": {"type": "string", "description": "Phone notification text, max 140 chars"},
        "hero_event_id": {"type": "string", "description": "id of the best photo event to feature, or ''"},
    },
}

WEEKLY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "subtitle", "intro", "stats", "chapters", "species_of_the_week", "sky_ahead",
                 "community", "try_this", "narration", "share_blurb", "hero_event_id"],
    "properties": {
        "title": {"type": "string"}, "subtitle": {"type": "string"},
        "intro": {"type": "string", "description": "One paragraph setting the scene for the week"},
        "stats": {"type": "array", "description": "4-6 headline numbers", "items": {
            "type": "object", "additionalProperties": False, "required": ["label", "value"],
            "properties": {"label": {"type": "string"}, "value": {"type": "string"}}}},
        "chapters": {"type": "array", "description": "3-6 short chapters of the week's story", "items": {
            "type": "object", "additionalProperties": False, "required": ["icon", "title", "body", "event_ids"],
            "properties": {"icon": {"type": "string", "enum": ICONS}, "title": {"type": "string"},
                           "body": {"type": "string"},
                           "event_ids": {"type": "array", "items": {"type": "string"},
                                         "description": "ids of events with photos/audio to show"}}}},
        "species_of_the_week": {"type": "object", "additionalProperties": False, "required": ["name", "why"],
                                "properties": {"name": {"type": "string"}, "why": {"type": "string"}}},
        "sky_ahead": {"type": "string", "description": "What to look up for in the coming week"},
        "community": {"type": "string", "description": "What local birders/weather/sky folks were buzzing about"},
        "try_this": {"type": "string", "description": "A small observation activity for a kid or a classroom"},
        "narration": {"type": "string", "description": "Spoken roundup, 2-3 minutes aloud, no markup"},
        "share_blurb": {"type": "string", "description": "2 sentences to accompany a shared link"},
        "hero_event_id": {"type": "string"},
    },
}


class Briefs:
    def __init__(self, hub: Hub, claude: Claude, deliver):
        self.hub, self.claude, self.deliver = hub, claude, deliver
        self.site = ROOT / hub.cfg.get("delivery.site.dir", "site")
        self.site.mkdir(parents=True, exist_ok=True)
        self.env = Environment(loader=FileSystemLoader(ROOT / "naturalist" / "templates"),
                               autoescape=select_autoescape(["html"]))
        self.env.filters["clock"] = self._clock
        self.env.filters["day"] = lambda ts: self._local(ts).strftime("%a %b %-d") if ts else ""

    # ---- helpers ----------------------------------------------------------
    def _local(self, ts: int) -> datetime:
        return datetime.fromtimestamp(ts, ZoneInfo(self.hub.cfg.tz))

    def _clock(self, ts) -> str:
        return self._local(int(ts)).strftime("%-I:%M%p").lower() if ts else ""

    def _voice(self) -> str:
        h = self.hub.cfg.raw["home"]
        return VOICE.format(home=h.get("name", "home"), town=h.get("town", ""))

    def gather(self, hours: int) -> dict:
        since = int(time.time()) - hours * 3600
        st = self.hub.store
        events = st.events(since=since, limit=3000)
        yard = [e for e in events if e["source"] in ("haikubox", "birdbuddy", "arlo")]
        by_species = collections.Counter((e["kind"], e["species"] or e["title"]) for e in yard)
        firsts = sorted({e["species"] for e in yard if (e.get("data") or {}).get("first_this_year")} - {None})
        photos = [e for e in yard if e.get("media_url")][:40]
        wx = st.weather_series(since)
        temps = [w["temp_f"] for w in wx if w.get("temp_f") is not None]
        gusts = [w["gust_mph"] for w in wx if w.get("gust_mph") is not None]
        rain_by_day: dict[str, float] = {}
        for w in wx:
            if w.get("rain_today_in") is not None:
                d = self._local(w["ts"]).strftime("%a")
                rain_by_day[d] = max(rain_by_day.get(d, 0), w["rain_today_in"])
        bats = [e for e in yard if e["kind"] == "bat"]
        return {
            "now_local": self._local(int(time.time())).strftime("%A %B %-d, %Y %-I:%M%p"),
            "window_hours": hours,
            "station_now": self.hub.now.get("weather"),
            "forecast_next_24h": (self.hub.now.get("forecast") or {}).get("hourly", [])[:24],
            "forecast_daily": (self.hub.now.get("forecast") or {}).get("daily", [])[:7],
            "weather_record": {
                "low_f": min(temps) if temps else None, "high_f": max(temps) if temps else None,
                "max_gust_mph": max(gusts) if gusts else None, "rain_by_day_in": rain_by_day,
                "lightning_strikes": len([e for e in events if e["kind"] == "lightning"]),
            },
            "yard_counts": [{"kind": k, "name": n, "detections": c} for (k, n), c in by_species.most_common(60)],
            "first_of_year": firsts,
            "bats": {"passes": len(bats), "species": sorted({b["species"] for b in bats if b["species"]}),
                     "first": self._clock(min(b["ts"] for b in bats)) if bats else None,
                     "last": self._clock(max(b["ts"] for b in bats)) if bats else None},
            "critters": [{"id": e["id"], "title": e["title"], "note": e["subtitle"], "time": self._clock(e["ts"])}
                         for e in yard if e["source"] == "arlo"][:20],
            "photo_events": [{"id": e["id"], "title": e["title"], "time": self._clock(e["ts"]), "source": e["source"]}
                             for e in photos],
            "community": [{"id": e["id"], "source": e["source"], "title": e["title"], "subtitle": e["subtitle"],
                           "topic": (e.get("data") or {}).get("topic"), "link": e["link"]}
                          for e in events if e["kind"] in ("community", "alert", "sky")][:60],
            "sky_tonight": self.hub.now.get("sky"),
            "kp_forecast": (self.hub.now.get("kp") or {}).get("peak"),
            "nws_alerts": self.hub.now.get("nws_alerts"),
            "nws_forecaster_discussion": _afd_excerpt((self.hub.now.get("afd") or {}).get("text", "")),
            "_events": {e["id"]: e for e in events},
        }

    def research(self, days: int) -> str:
        town = self.hub.cfg.raw["home"].get("town", "")
        domains = self.hub.cfg.get("community.web_watch_domains", [])
        if not (self.claude.enabled and domains):
            return ""
        try:
            return self.claude.research(
                "You scan local enthusiast forums and lists for a home naturalist. Report only what you "
                "actually find, with the site name and date for each item. Be concise: bullet points.",
                f"What have weather enthusiasts, birders, naturalists and amateur astronomers around {town} "
                f"and greater Boston / eastern Massachusetts been discussing or reporting in the last {days} "
                "day(s)? Look for: storm or pattern talk, rare or notable birds, migration, interesting "
                "wildlife or plant phenology, aurora or sky events, local club star parties or bird walks "
                "coming up. Up to 12 bullets.", domains)
        except Exception as e:
            log.warning("forum research failed: %s", e)
            return ""

    async def _localize(self, url: str | None, sub: str) -> str | None:
        """Copy a remote photo/audio into site/media so shared pages never break."""
        if not url:
            return None
        ext = Path(url.split("?")[0]).suffix[:5] or ".jpg"
        name = hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:20] + ext
        dest = self.site / "media" / name
        if not dest.exists():
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                dest.write_bytes(r.content)
            except Exception as e:
                log.info("could not copy media %s: %s", url[:80], e)
                return None
        return f"{sub}media/{name}"

    # ---- morning ------------------------------------------------------------
    async def morning(self, send: bool = True) -> dict:
        if not self.claude.enabled:
            raise RuntimeError("ANTHROPIC_API_KEY is needed to write briefs")
        ctx = self.gather(hours=24)
        events = ctx.pop("_events")
        notes = await asyncio.to_thread(self.research, 2)
        prompt = (
            "Write this morning's wake-up brief for the household. Order sections by what matters most "
            "today (a storm beats a sparrow; a first-of-year warbler beats a routine cardinal). Always "
            "cover: today's weather (with the hour things change), what visited and sang overnight and "
            "yesterday (including bats), any critters on camera, tonight's sky worth stepping out for, "
            "and anything notable from the community. 4-7 sections.\n\n"
            f"DATA:\n{json.dumps(ctx, default=str)}\n\nFORUM SCAN:\n{notes or '(none)'}")
        brief = await asyncio.to_thread(self.claude.json, self._voice(), prompt, MORNING_SCHEMA)
        return await self._publish("morning", brief, events, ctx, send)

    # ---- weekly ---------------------------------------------------------------
    async def weekly(self, send: bool = True) -> dict:
        if not self.claude.enabled:
            raise RuntimeError("ANTHROPIC_API_KEY is needed to write briefs")
        ctx = self.gather(hours=24 * 7)
        events = ctx.pop("_events")
        notes = await asyncio.to_thread(self.research, 7)
        prompt = (
            "Write the end-of-week roundup: a lovely, shareable story of the week at the house, the kind "
            "friends forward and a teacher could read to a class. Chapters should follow the week's real "
            "arc (fronts, quiet days, the busiest morning at the feeder, the night the bats were busiest). "
            "Choose event_ids that have photos to illustrate each chapter. Stats are punchy headline "
            "numbers (species heard, feeder visits, bat passes, rain total, peak gust, lightning).\n\n"
            f"DATA:\n{json.dumps(ctx, default=str)}\n\nFORUM SCAN:\n{notes or '(none)'}")
        brief = await asyncio.to_thread(self.claude.json, self._voice(), prompt, WEEKLY_SCHEMA)
        return await self._publish("weekly", brief, events, ctx, send)

    # ---- render + deliver -------------------------------------------------------
    async def _publish(self, kind: str, brief: dict, events: dict, ctx: dict, send: bool) -> dict:
        now = self._local(int(time.time()))
        slug = now.strftime("%Y-%m-%d") if kind == "morning" else now.strftime("%G-W%V")
        out_dir = self.site / kind / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        up = "../../"
        hero = events.get(brief.get("hero_event_id") or "")
        hero_img = await self._localize(hero.get("media_url"), up) if hero else None
        gallery = []
        if kind == "weekly":
            for ch in brief["chapters"]:
                ch["media"] = []
                for eid in ch.get("event_ids", [])[:4]:
                    e = events.get(eid)
                    if not e or (e["source"] == "arlo" and not self._share_arlo()):
                        continue
                    img = await self._localize(e.get("media_url"), up)
                    aud = await self._localize(e.get("audio_url"), up)
                    if img or aud:
                        ch["media"].append({"img": img, "audio": aud, "caption": e["title"],
                                            "time": self._clock(e["ts"])})
                        gallery.append(ch["media"][-1])
        audio_rel = None
        audio_path = await asyncio.to_thread(narrate, brief.get("narration", ""), out_dir / "narration",
                                             self.hub.cfg)
        if audio_path:
            audio_rel = audio_path.name
        sky = ctx.get("sky_tonight") or {}
        tpl = self.env.get_template(f"{kind}.html")
        html = tpl.render(b=brief, home=self.hub.cfg.raw["home"], date=now, hero_img=hero_img,
                          audio=audio_rel, sky=sky, wx=ctx.get("weather_record"), now=ctx.get("station_now") or {},
                          daily=ctx.get("forecast_daily") or [], yard=ctx.get("yard_counts") or [],
                          gallery=gallery, series=self._series(kind))
        (out_dir / "index.html").write_text(html)
        page = f"{kind}/{slug}/index.html"
        self.hub.store.add_brief(f"{kind}-{slug}", kind, int(time.time()), brief, page,
                                 f"{kind}/{slug}/{audio_rel}" if audio_rel else None)
        self.hub.broadcast({"type": "brief", "kind": kind, "page": page})
        self._write_index()
        if send:
            await self.deliver.brief(kind, brief, out_dir, page, audio_path)
        log.info("%s brief published: %s", kind, page)
        return {"page": page, "brief": brief}

    def _share_arlo(self) -> bool:
        return bool(self.hub.cfg.get("delivery.site.git_publish.share_arlo", False))

    def _series(self, kind: str) -> list[dict]:
        hrs = 24 * 7 if kind == "weekly" else 24
        pts = self.hub.store.weather_series(int(time.time()) - hrs * 3600)
        step = max(1, len(pts) // 168)
        return [{"ts": p["ts"], "t": p.get("temp_f"), "r": p.get("rain_today_in")} for p in pts[::step]]

    def _write_index(self) -> None:
        tpl = self.env.get_template("archive.html")
        html = tpl.render(home=self.hub.cfg.raw["home"], weekly=self.hub.store.briefs("weekly", 52),
                          morning=self.hub.store.briefs("morning", 30))
        (self.site / "index.html").write_text(html)


def _afd_excerpt(text: str, limit: int = 3500) -> str:
    """Keep the forecaster's KEY MESSAGES / SYNOPSIS, which carry the story of the pattern."""
    if not text:
        return ""
    t = text.replace("\r", "")
    for marker in (".KEY MESSAGES", "KEY MESSAGES", ".SYNOPSIS"):
        i = t.find(marker)
        if i >= 0:
            return t[i:i + limit]
    return t[:limit]
