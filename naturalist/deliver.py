"""Getting things to people: Gmail, phone push (ntfy), and the shareable site."""
from __future__ import annotations

import asyncio
import logging
import smtplib
import subprocess
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import httpx

from .config import ROOT, Config

log = logging.getLogger("naturalist.deliver")


class Deliver:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def page_url(self, page: str) -> str | None:
        base = (self.cfg.get("delivery.site.public_base_url") or "").rstrip("/")
        return f"{base}/{page.removesuffix('index.html')}" if base else None

    # ---- phone push ------------------------------------------------------
    async def push(self, title: str, body: str, *, click: str | None = None, tags: str = "",
                   priority: int = 3, image: str | None = None) -> None:
        topic = self.cfg.secret("NTFY_TOPIC")
        if not (topic and self.cfg.get("delivery.ntfy.enabled", True)):
            return
        server = self.cfg.get("delivery.ntfy.server", "https://ntfy.sh").rstrip("/")
        headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Priority": str(priority)}
        if tags:
            headers["Tags"] = tags
        if click:
            headers["Click"] = click
        if image:
            headers["Attach"] = image
        if self.cfg.secret("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {self.cfg.secret('NTFY_TOKEN')}"
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                (await c.post(f"{server}/{topic}", content=body.encode(), headers=headers)).raise_for_status()
        except Exception as e:
            log.warning("push failed: %s", e)

    # ---- email -------------------------------------------------------------
    def _send_email(self, subject: str, html: str, text: str, attachments: list[Path]) -> None:
        user, pw = self.cfg.secret("GMAIL_ADDRESS"), self.cfg.secret("GMAIL_APP_PASSWORD")
        to = self.cfg.get("delivery.email.to") or [user]
        if not (user and pw and self.cfg.get("delivery.email.enabled", True)):
            log.info("email not configured; skipping")
            return
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        for p in attachments:
            if p and p.exists() and p.stat().st_size < 15_000_000:
                sub = {".mp3": "mpeg", ".wav": "wav", ".aiff": "aiff"}.get(p.suffix, "octet-stream")
                msg.add_attachment(p.read_bytes(), maintype="audio", subtype=sub, filename=p.name)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(user, pw)
            s.send_message(msg)

    async def brief(self, kind: str, brief: dict, out_dir: Path, page: str, audio: Path | None) -> None:
        url = self.page_url(page)
        home = self.cfg.raw["home"].get("name", "Home")
        if kind == "morning":
            subject = f"☀ {home} · {brief['headline']}"
            text = brief["greeting"] + "\n\n" + "\n\n".join(f"{s['title']}\n{s['body']}" for s in brief["sections"])
            push_title, push_body = f"Good morning from {home}", brief["push"]
        else:
            subject = f"🌿 {brief['title']} · week at {home}"
            text = brief["intro"] + "\n\n" + "\n\n".join(f"{c['title']}\n{c['body']}" for c in brief["chapters"])
            push_title, push_body = brief["title"], brief["share_blurb"]
        if url:
            text += f"\n\nRead and listen: {url}"
        html = (out_dir / "index.html").read_text()
        base = (self.cfg.get("delivery.site.public_base_url") or "").rstrip("/")
        if base:  # make relative media links absolute so the email shows photos
            html = html.replace('src="../../', f'src="{base}/').replace('href="../../', f'href="{base}/')
            html = html.replace('src="narration', f'src="{url}narration')
        try:
            await asyncio.to_thread(self._send_email, subject, html, text, [audio] if audio else [])
        except Exception as e:
            log.warning("email failed: %s", e)
        await self.push(push_title, push_body, click=url, tags="sunrise" if kind == "morning" else "herb")
        if kind == "weekly":
            await asyncio.to_thread(self.publish_site)

    # ---- shareable site ------------------------------------------------------
    def publish_site(self) -> None:
        g = self.cfg.get("delivery.site.git_publish") or {}
        if not g.get("enabled") or not g.get("repo_dir"):
            return
        src = ROOT / self.cfg.get("delivery.site.dir", "site")
        repo = Path(g["repo_dir"]).expanduser()
        try:
            subprocess.run(["rsync", "-a", "--exclude", ".git", f"{src}/", f"{repo}/"], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", f"Roundup {datetime.now():%Y-%m-%d}"],
                           check=False)
            subprocess.run(["git", "-C", str(repo), "push"], check=True, timeout=120)
        except Exception as e:
            log.warning("site publish failed: %s", e)
