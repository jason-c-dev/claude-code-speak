"""Shared pytest fixtures for claude-voice tests."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make `scripts/` importable as a top-level package in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def voice_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.claude/voice/ to a tmp dir for the duration of a test."""
    home = tmp_path / "voice_home"
    home.mkdir()
    (home / "state").mkdir()
    (home / "tmp").mkdir()
    monkeypatch.setenv("CLAUDE_VOICE_HOME", str(home))
    return home


@pytest.fixture
def plugin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ${CLAUDE_PLUGIN_ROOT} to a tmp dir."""
    root = tmp_path / "plugin"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    return root


@pytest.fixture
def write_config(plugin_root: Path):
    """Helper to write a config.json under the plugin root."""
    def _write(cfg: dict[str, Any]) -> Path:
        path = plugin_root / "config.json"
        path.write_text(json.dumps(cfg))
        return path
    return _write
