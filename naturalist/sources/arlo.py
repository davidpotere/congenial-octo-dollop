"""Arlo cameras via the community `pyaarlo` library.

Arlo has no public consumer API. pyaarlo logs in as you; Arlo then emails a
2FA code, which pyaarlo reads over IMAP (use a Gmail App Password). Arlo's own
smart detection tags clips Animal / Person / Vehicle; we keep the animals and,
optionally, let Claude say which animal it is.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from ..claude import Claude
from ..hub import Hub

log = logging.getLogger("naturalist.arlo")


class ArloSource:
    def __init__(self, hub: Hub, claude: Claude):
        self.hub = hub
        self.claude = claude
        s = hub.cfg.secret
        self.user, self.password = s("ARLO_USERNAME"), s("ARLO_PASSWORD")
        self.arlo = None

    @property
    def enabled(self) -> bool:
        return bool(self.user and self.password)

    def _connect(self):
        import pyaarlo
        s = self.hub.cfg.secret
        return pyaarlo.PyArlo(
            username=self.user, password=self.password,
            tfa_source="imap", tfa_type="email",
            tfa_host=s("ARLO_TFA_IMAP_HOST") or "imap.gmail.com",
            tfa_username=s("ARLO_TFA_IMAP_USER") or self.user,
            tfa_password=s("ARLO_TFA_IMAP_PASSWORD"),
            storage_dir=str(self.hub.cfg.data_dir / "aarlo"),
            library_days=2, synchronous_mode=True,
        )

    def _videos(self) -> list[dict]:
        if self.arlo is None:
            self.arlo = self._connect()
        ml = self.arlo.ml
        ml.update()
        out = []
        for v in ml.videos[:60]:
            out.append({
                "id": v.id, "ts": int(v.created_at / 1000) if v.created_at > 1e12 else int(v.created_at),
                "camera": v.camera.name if v.camera else "camera",
                "object": (v.object_type or "").lower(), "thumb": v.thumbnail_url, "video": v.video_url,
                "seconds": v.media_duration_seconds,
            })
        return out

    async def poll(self) -> None:
        if not self.enabled:
            self.hub.source_off("arlo", "add ARLO_USERNAME / ARLO_PASSWORD (+ IMAP 2FA) to .env")
            return
        try:
            videos = await asyncio.to_thread(self._videos)
            animal_only = self.hub.cfg.get("arlo.animal_only", True)
            for v in videos:
                if animal_only and v["object"] != "animal":
                    continue
                eid = f"arlo-{v['id']}"
                if self.hub.store.has_event(eid):
                    continue  # already identified; don't pay for Claude twice
                ident = await self._identify(v["thumb"])
                name = ident.get("animal") if ident and ident.get("animal", "none") != "none" else "Animal"
                await self.hub.emit(
                    id=eid, source="arlo", kind="mammal", ts=v["ts"], species=name if name != "Animal" else None,
                    title=f"{name} on the {v['camera']}", subtitle=(ident or {}).get("note"),
                    media_url=v["thumb"], link=v["video"],
                    data={"camera": v["camera"], "seconds": v["seconds"], "id": ident},
                )
            self.hub.source_ok("arlo", f"{len(videos)} recent clips")
        except Exception as e:
            self.hub.source_error("arlo", e)

    async def _identify(self, url: str | None) -> dict | None:
        if not (url and self.claude.enabled and self.hub.cfg.get("arlo.identify_with_claude", True)):
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(url)
                r.raise_for_status()
            b64 = base64.standard_b64encode(r.content).decode()
            mt = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return await asyncio.to_thread(self.claude.identify_animal, b64, mt)
        except Exception as e:
            log.info("could not identify arlo clip: %s", e)
            return None
