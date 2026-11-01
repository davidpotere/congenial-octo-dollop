"""Configuration: config.yaml for settings, .env for secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: dict[str, Any]
    data_dir: Path
    secrets: dict[str, str] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def secret(self, name: str) -> str:
        return self.secrets.get(name, "") or ""

    # Location is mutable: the Tempest station's coordinates replace the defaults.
    @property
    def lat(self) -> float:
        return float(self.raw["home"]["latitude"])

    @property
    def lon(self) -> float:
        return float(self.raw["home"]["longitude"])

    @property
    def tz(self) -> str:
        return self.raw["home"].get("timezone", "America/New_York")

    def set_location(self, lat: float, lon: float) -> None:
        self.raw["home"]["latitude"] = lat
        self.raw["home"]["longitude"] = lon


SECRET_KEYS = [
    "TEMPEST_TOKEN", "ANTHROPIC_API_KEY", "HAIKUBOX_SERIAL",
    "BIRDBUDDY_EMAIL", "BIRDBUDDY_PASSWORD",
    "ARLO_USERNAME", "ARLO_PASSWORD", "ARLO_TFA_IMAP_HOST", "ARLO_TFA_IMAP_USER", "ARLO_TFA_IMAP_PASSWORD",
    "EBIRD_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NTFY_TOPIC", "NTFY_TOKEN",
]


def load_config(path: str | os.PathLike | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    example = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    user = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    raw = _deep_merge(example, user or {})
    data_dir = Path(os.environ.get("NATURALIST_DATA", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    secrets = {k: os.environ.get(k, "").strip() for k in SECRET_KEYS}
    return Config(raw=raw, data_dir=data_dir, secrets=secrets)
