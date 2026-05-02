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
    assert len(a.handlers) == 1, "get_logger should never double-attach handlers"


def test_get_logger_does_not_raise_when_voice_home_unwritable(tmp_path, monkeypatch):
    """Voice is a UX layer. A bad CLAUDE_VOICE_HOME must not crash the hook."""
    from scripts import log

    # Create a regular file, then point CLAUDE_VOICE_HOME at a path beneath it
    # so mkdir(parents=True) is forced to fail with NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bad_home = blocker / "voice"
    monkeypatch.setenv("CLAUDE_VOICE_HOME", str(bad_home))

    log.reset_for_testing()
    logger = log.get_logger()  # must not raise
    logger.info("this should be a no-op, not a crash")  # must not raise either
    log.flush()
