"""Thin wrapper around the Anthropic SDK used for briefs, forum scans, and critter IDs."""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .config import Config

log = logging.getLogger("naturalist.claude")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Claude:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = cfg.get("claude.model", "claude-opus-5-5")
        self.effort = cfg.get("claude.effort", "medium")
        key = cfg.secret("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=key) if key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _call(self, *, system: str, messages: list[dict], max_tokens: int = 16000,
              tools: list[dict] | None = None, schema: dict | None = None) -> anthropic.types.beta.BetaMessage:
        output_config: dict[str, Any] = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=max_tokens, system=system, messages=messages,
            output_config=output_config, thinking={"type": "adaptive"},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
        if tools:
            kwargs["tools"] = tools
        with self.client.beta.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined: {getattr(msg, 'stop_details', None)}")
        return msg

    @staticmethod
    def _text(msg) -> str:
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def json(self, system: str, prompt: str | list[dict], schema: dict, max_tokens: int = 16000) -> dict:
        content = prompt if isinstance(prompt, list) else [{"type": "text", "text": prompt}]
        msg = self._call(system=system, messages=[{"role": "user", "content": content}],
                         schema=schema, max_tokens=max_tokens)
        return json.loads(self._text(msg))

    def research(self, system: str, prompt: str, domains: list[str], max_uses: int = 8) -> str:
        """Let Claude search the web (limited to `domains`) and return its notes as text."""
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses,
                  "allowed_domains": domains}]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        for _ in range(4):  # server tools can pause long turns; resume them
            msg = self._call(system=system, messages=messages, tools=tools, max_tokens=32000)
            if msg.stop_reason != "pause_turn":
                return self._text(msg)
            messages.append({"role": "assistant", "content": msg.content})
        return self._text(msg)

    def identify_animal(self, image_b64: str, media_type: str = "image/jpeg") -> dict:
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["animal", "confidence", "note"],
            "properties": {
                "animal": {"type": "string", "description": "Common name, e.g. 'Red fox', or 'none' if no animal"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "note": {"type": "string", "description": "One short naturalist's aside about what it is doing"},
            },
        }
        return self.json(
            "You are a field naturalist in eastern Massachusetts identifying wildlife on a backyard camera.",
            [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
             {"type": "text", "text": "What animal is in this backyard camera frame?"}],
            schema, max_tokens=4000,
        )
