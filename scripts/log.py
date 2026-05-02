"""Shared logger used by every script in this plugin.

Writes to ~/.claude/voice/voice.log (overridable via $CLAUDE_VOICE_HOME).
Idempotent: get_logger() always returns the same Logger with a single handler.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

_LOGGER: logging.Logger | None = None


def voice_home() -> Path:
    override = os.environ.get("CLAUDE_VOICE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "voice"


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    home = voice_home()
    home.mkdir(parents=True, exist_ok=True)
    log_path = home / "voice.log"

    logger = logging.getLogger("claude_voice")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    _LOGGER = logger
    return logger


def flush() -> None:
    if _LOGGER is None:
        return
    for h in _LOGGER.handlers:
        h.flush()


def reset_for_testing() -> None:
    """Drop the cached logger so the next get_logger() rebuilds with a fresh path."""
    global _LOGGER
    if _LOGGER is not None:
        for h in list(_LOGGER.handlers):
            h.close()
            _LOGGER.removeHandler(h)
    _LOGGER = None
