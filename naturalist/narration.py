"""Turn a brief's script into audio on the home box.

macOS: the built-in `say` voices (Premium/Enhanced voices sound lovely; add
them in System Settings > Accessibility > Spoken Content).
Linux / Raspberry Pi: Piper (https://github.com/rhasspy/piper), fast and local.
ffmpeg (if installed) converts to .mp3 so it plays everywhere, email included.
If nothing is available, the web page reads the script with the browser's voice.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path

from .config import Config

log = logging.getLogger("naturalist.narration")


def _engine(cfg: Config) -> str:
    e = cfg.get("narration.engine", "auto")
    if e != "auto":
        return e
    if platform.system() == "Darwin" and shutil.which("say"):
        return "say"
    if shutil.which("piper") and cfg.get("narration.voice"):
        return "piper"
    return "none"


def narrate(text: str, out_base: Path, cfg: Config) -> Path | None:
    if not text.strip():
        return None
    engine, voice = _engine(cfg), cfg.get("narration.voice", "")
    try:
        if engine == "say":
            raw = out_base.with_suffix(".aiff")
            cmd = ["say", "-o", str(raw)] + (["-v", voice] if voice else []) + [text]
            subprocess.run(cmd, check=True, timeout=300)
        elif engine == "piper":
            raw = out_base.with_suffix(".wav")
            subprocess.run(["piper", "--model", voice, "--output_file", str(raw)], input=text.encode(),
                           check=True, timeout=300)
        else:
            return None
    except Exception as e:
        log.warning("narration failed (%s): %s", engine, e)
        return None
    if shutil.which("ffmpeg"):
        mp3 = out_base.with_suffix(".mp3")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-codec:a", "libmp3lame",
                            "-q:a", "3", str(mp3)], check=True, timeout=300)
            raw.unlink(missing_ok=True)
            return mp3
        except Exception as e:
            log.info("mp3 conversion failed: %s", e)
    return raw
