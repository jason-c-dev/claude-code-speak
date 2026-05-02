import logging
from pathlib import Path

import pytest


def test_get_logger_writes_to_voice_home(voice_home: Path, monkeypatch):
    from scripts import log

    log.reset_for_testing()
    logger = log.get_logger()
    logger.info("hello world")
    log.flush()

    log_file = voice_home / "voice.log"
    assert log_file.exists()
    contents = log_file.read_text()
    assert "hello world" in contents


def test_logger_is_idempotent(voice_home: Path):
    from scripts import log

    log.reset_for_testing()
    a = log.get_logger()
    b = log.get_logger()
    assert a is b
    # Logging twice must not double up handlers (only one line per call).
    a.info("once")
    log.flush()
    contents = (voice_home / "voice.log").read_text()
    assert contents.count("once") == 1
