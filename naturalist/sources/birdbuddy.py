"""Bird Buddy feeder via the community `pybirdbuddy` GraphQL client.

Bird Buddy has no official public API. pybirdbuddy logs in with email and
password, so an account created with "Sign in with Google/Apple" needs a
password set in the Bird Buddy app first.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..hub import Hub

log = logging.getLogger("naturalist.birdbuddy")


class BirdBuddySource:
    def __init__(self, hub: Hub):
        self.hub = hub
        self.email = hub.cfg.secret("BIRDBUDDY_EMAIL")
        self.password = hub.cfg.secret("BIRDBUDDY_PASSWORD")
        self.client = None

    @property
    def enabled(self) -> bool:
        return bool(self.email and self.password)

    async def poll(self) -> None:
        if not self.enabled:
            self.hub.source_off("birdbuddy", "add BIRDBUDDY_EMAIL / BIRDBUDDY_PASSWORD to .env")
            return
        try:
            from birdbuddy.client import BirdBuddy
            from birdbuddy.feed import FeedNodeType

            if self.client is None:
                self.client = BirdBuddy(self.email, self.password)
            await self.client.refresh()
            last = self.hub.store.get("birdbuddy_since")
            since = datetime.fromisoformat(last) if last else datetime.now(timezone.utc) - timedelta(days=2)
            nodes = await self.client.refresh_feed(since=since)
            newest = since
            year_start = int(datetime(datetime.now().year, 1, 1).timestamp())
            for node in nodes:
                created = node.created_at or datetime.now(timezone.utc)
                newest = max(newest, created)
                if node.node_type not in (FeedNodeType.NewPostcard, FeedNodeType.SpeciesSighting,
                                          FeedNodeType.CollectedPostcard, FeedNodeType.SpeciesUnlocked):
                    continue
                await self._handle_node(node, created, year_start)
            self.hub.store.set("birdbuddy_since", newest.isoformat())
            feeders = self.client.feeders
            self.hub.update_now("feeders", [{
                "name": f.get("name"), "food": f.get("food", {}).get("state") if isinstance(f.get("food"), dict) else f.get("food"),
                "battery": (f.get("battery") or {}).get("percentage") if isinstance(f.get("battery"), dict) else None,
                "state": f.get("state"),
            } for f in feeders.values()])
            self.hub.source_ok("birdbuddy", f"{len(feeders)} feeder(s)")
        except Exception as e:
            self.hub.source_error("birdbuddy", e)

    async def _handle_node(self, node, created: datetime, year_start: int) -> None:
        name, media = None, []
        try:
            # A postcard becomes a sighting with species guesses and photos/videos.
            sighting = await self.client.sighting_from_postcard(node)
            for s in sighting.report.sightings:
                if s.is_recognized and s.species:
                    name = s.species.name
                    break
            media = [m for m in sighting.medias if not m.is_video]
        except Exception:
            # Older feed item shapes carry the species directly.
            sp = node.get("species") or (node.get("collection") or {}).get("species") or {}
            name = sp.get("name") if isinstance(sp, dict) else None
            media = []
        name = name or "Mystery visitor"
        photo = media[0].content_url or media[0].thumbnail_url if media else None
        ts = int(created.timestamp())
        await self.hub.emit(
            id=f"bb-{node.node_id}", source="birdbuddy", kind="bird", ts=ts, species=name,
            title=name, subtitle="at the feeder", media_url=photo,
            data={"photos": [m.content_url or m.thumbnail_url for m in media][:6],
                  "first_this_year": self.hub.store.species_first_seen(name, year_start)},
        )
