from pathlib import Path

import pytest


def test_loads_config_from_plugin_root(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "B", "voice": "aura-2-orion-en"})
    cfg = config.load()
    assert cfg.enabled is True
    assert cfg.mode == "B"
    assert cfg.voice == "aura-2-orion-en"


def test_defaults_apply_when_keys_missing(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "A"})
    cfg = config.load()
    assert cfg.voice == "aura-2-thalia-en"
    assert cfg.primary_tts == "deepgram"
    assert cfg.fallback_tts == "say"
    assert cfg.rewrite is True
    assert cfg.min_words == 3
    assert cfg.max_haiku_chars == 4000
    assert cfg.max_deepgram_chars == 2000
    assert cfg.speech_rate == pytest.approx(1.0)


def test_missing_file_returns_disabled_defaults(plugin_root: Path):
    from scripts import config

    cfg = config.load()
    assert cfg.enabled is False  # if the user hasn't configured anything, stay silent


def test_invalid_mode_falls_back_to_A(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "Z"})
    cfg = config.load()
    assert cfg.mode == "A"


def test_say_voice_map_default(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True})
    cfg = config.load()
    assert cfg.say_voice_map["aura-2-thalia-en"] == "Samantha"
    assert cfg.say_voice_map["aura-2-orion-en"] == "Alex"
