"""Load and validate config.json from the plugin root.

Hardened: load() must never raise. Any malformed config or filesystem
error degrades to Config(enabled=False) with a log entry. Voice is a
UX layer, never load-bearing.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from scripts.log import get_logger

VALID_MODES = {"A", "B", "C"}

DEFAULT_SAY_VOICE_MAP: Mapping[str, str] = MappingProxyType({
    "aura-2-thalia-en": "Samantha",
    "aura-2-orion-en": "Alex",
    "aura-2-luna-en": "Samantha",
    "aura-2-asteria-en": "Samantha",
    "aura-2-zeus-en": "Daniel",
    "aura-2-pandora-en": "Karen",
})


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    mode: str = "A"
    voice: str = "aura-2-thalia-en"
    primary_tts: str = "deepgram"
    fallback_tts: str = "say"
    # Stored as MappingProxyType so the map is read-only after construction.
    say_voice_map: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(dict(DEFAULT_SAY_VOICE_MAP))
    )
    tool_phrases: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    rewrite: bool = False
    haiku_model: str = "claude-haiku-4-5-20251001"
    min_words: int = 3
    max_haiku_chars: int = 4000
    max_deepgram_chars: int = 2000
    speech_rate: float = 1.0
    # Path to a Piper .onnx voice model. Empty disables Piper. Tilde is expanded.
    # Example: "~/piper-voices/en_US-amy-medium.onnx". The matching
    # `.onnx.json` config file must sit next to it (Piper convention).
    piper_voice: str = ""


def plugin_root() -> Path:
    override = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def _coerce_bool(value, default: bool, key: str, log) -> bool:
    if isinstance(value, bool):
        return value
    log.warning("config.%s: expected bool, got %r; using default %r", key, value, default)
    return default


def _coerce_int(value, default: int, key: str, log, *, minimum: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        log.warning("config.%s: not a valid int %r; using default %r", key, value, default)
        return default
    if minimum is not None and n < minimum:
        log.warning("config.%s: %d below minimum %d; using minimum", key, n, minimum)
        return minimum
    return n


def _coerce_float(value, default: float, key: str, log,
                  *, minimum: float | None = None,
                  maximum: float | None = None) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        log.warning("config.%s: not a valid float %r; using default %r", key, value, default)
        return default
    if minimum is not None and n < minimum:
        log.warning("config.%s: %g below minimum %g; clamping", key, n, minimum)
        return minimum
    if maximum is not None and n > maximum:
        log.warning("config.%s: %g above maximum %g; clamping", key, n, maximum)
        return maximum
    return n


def _coerce_voice_map(value, log) -> Mapping[str, str]:
    """Merge user-provided say_voice_map over defaults; ignore garbage."""
    merged = dict(DEFAULT_SAY_VOICE_MAP)
    if value is None:
        return MappingProxyType(merged)
    if not isinstance(value, dict):
        log.warning("config.say_voice_map: expected object, got %r; using defaults", type(value).__name__)
        return MappingProxyType(merged)
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, str):
            merged[k] = v
        else:
            log.warning("config.say_voice_map: ignoring non-string entry %r=%r", k, v)
    return MappingProxyType(merged)


def _coerce_tool_phrases(value, log) -> Mapping[str, str]:
    """Accept dict[str, str] only; ignore garbage. Empty mapping by default."""
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, dict):
        log.warning("config.tool_phrases: expected object, got %r; ignoring",
                    type(value).__name__)
        return MappingProxyType({})
    out: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
        else:
            log.warning("config.tool_phrases: ignoring non-string entry %r=%r", k, v)
    return MappingProxyType(out)


def load() -> Config:
    """Load config.json; degrade silently to Config(enabled=False) on any error."""
    log = get_logger()
    defaults = Config()
    path = plugin_root() / "config.json"
    if not path.exists():
        log.info("config.json not found at %s; voice disabled", path)
        return defaults

    try:
        raw_text = path.read_text()
    except OSError as e:
        log.error("config.json could not be read: %s", e)
        return defaults

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        log.error("config.json is not valid JSON: %s", e)
        return defaults

    if not isinstance(raw, dict):
        log.error("config.json must be a JSON object; got %s", type(raw).__name__)
        return defaults

    try:
        mode = raw.get("mode", defaults.mode)
        if mode not in VALID_MODES:
            log.warning("config.mode: %r is not in %s; falling back to 'A'", mode, sorted(VALID_MODES))
            mode = "A"

        return Config(
            enabled=_coerce_bool(raw.get("enabled", defaults.enabled), defaults.enabled, "enabled", log),
            mode=mode,
            voice=str(raw.get("voice", defaults.voice)),
            primary_tts=str(raw.get("primary_tts", defaults.primary_tts)),
            fallback_tts=str(raw.get("fallback_tts", defaults.fallback_tts)),
            say_voice_map=_coerce_voice_map(raw.get("say_voice_map"), log),
            tool_phrases=_coerce_tool_phrases(raw.get("tool_phrases"), log),
            rewrite=_coerce_bool(raw.get("rewrite", defaults.rewrite), defaults.rewrite, "rewrite", log),
            haiku_model=str(raw.get("haiku_model", defaults.haiku_model)),
            min_words=_coerce_int(raw.get("min_words", defaults.min_words),
                                  defaults.min_words, "min_words", log, minimum=0),
            max_haiku_chars=_coerce_int(raw.get("max_haiku_chars", defaults.max_haiku_chars),
                                        defaults.max_haiku_chars, "max_haiku_chars", log, minimum=1),
            max_deepgram_chars=_coerce_int(raw.get("max_deepgram_chars", defaults.max_deepgram_chars),
                                           defaults.max_deepgram_chars, "max_deepgram_chars", log, minimum=1),
            speech_rate=_coerce_float(raw.get("speech_rate", defaults.speech_rate),
                                      defaults.speech_rate, "speech_rate", log,
                                      minimum=0.5, maximum=2.0),
            piper_voice=str(raw.get("piper_voice", defaults.piper_voice) or ""),
        )
    except Exception as e:  # pragma: no cover — defensive last-resort
        log.error("unexpected error building Config from %s: %s", path, e)
        return defaults
