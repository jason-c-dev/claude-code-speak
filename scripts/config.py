"""Load and validate config.json from the plugin root."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from scripts.log import get_logger

VALID_MODES = {"A", "B", "C"}

DEFAULT_SAY_VOICE_MAP = {
    "aura-2-thalia-en": "Samantha",
    "aura-2-orion-en": "Alex",
    "aura-2-luna-en": "Samantha",
    "aura-2-asteria-en": "Samantha",
    "aura-2-zeus-en": "Daniel",
    "aura-2-pandora-en": "Karen",
}


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    mode: str = "A"
    voice: str = "aura-2-thalia-en"
    primary_tts: str = "deepgram"
    fallback_tts: str = "say"
    say_voice_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SAY_VOICE_MAP))
    rewrite: bool = True
    haiku_model: str = "claude-haiku-4-5-20251001"
    min_words: int = 3
    max_haiku_chars: int = 4000
    max_deepgram_chars: int = 2000
    speech_rate: float = 1.0


def plugin_root() -> Path:
    override = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if override:
        return Path(override)
    # Fallback: assume we're being run from the plugin dir.
    return Path(__file__).resolve().parent.parent


def load() -> Config:
    log = get_logger()
    path = plugin_root() / "config.json"
    if not path.exists():
        log.info("config.json not found at %s; voice disabled", path)
        return Config(enabled=False)

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.error("config.json is not valid JSON: %s", e)
        return Config(enabled=False)

    mode = raw.get("mode", "A")
    if mode not in VALID_MODES:
        log.warning("config.json has invalid mode %r; falling back to 'A'", mode)
        mode = "A"

    merged_map = dict(DEFAULT_SAY_VOICE_MAP)
    merged_map.update(raw.get("say_voice_map") or {})

    return Config(
        enabled=bool(raw.get("enabled", False)),
        mode=mode,
        voice=str(raw.get("voice", "aura-2-thalia-en")),
        primary_tts=str(raw.get("primary_tts", "deepgram")),
        fallback_tts=str(raw.get("fallback_tts", "say")),
        say_voice_map=merged_map,
        rewrite=bool(raw.get("rewrite", True)),
        haiku_model=str(raw.get("haiku_model", "claude-haiku-4-5-20251001")),
        min_words=int(raw.get("min_words", 3)),
        max_haiku_chars=int(raw.get("max_haiku_chars", 4000)),
        max_deepgram_chars=int(raw.get("max_deepgram_chars", 2000)),
        speech_rate=float(raw.get("speech_rate", 1.0)),
    )
