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


def test_unreadable_file_returns_disabled(plugin_root: Path):
    from scripts import config

    # Make config.json a directory so read_text raises IsADirectoryError (an OSError).
    (plugin_root / "config.json").mkdir()
    cfg = config.load()
    assert cfg.enabled is False


def test_non_dict_json_returns_disabled(plugin_root: Path):
    from scripts import config

    (plugin_root / "config.json").write_text("[1, 2, 3]")
    cfg = config.load()
    assert cfg.enabled is False


def test_invalid_json_returns_disabled(plugin_root: Path):
    from scripts import config

    (plugin_root / "config.json").write_text("{not json}")
    cfg = config.load()
    assert cfg.enabled is False


def test_string_enabled_is_rejected(plugin_root: Path, write_config):
    from scripts import config

    # bool("false") would be True if we naively coerced; we should reject.
    write_config({"enabled": "false"})
    cfg = config.load()
    assert cfg.enabled is False  # falls back to default (False)


def test_invalid_min_words_falls_back_to_default(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "min_words": "three"})
    cfg = config.load()
    assert cfg.min_words == 3


def test_speech_rate_clamps_to_bounds(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "speech_rate": 99.0})
    cfg = config.load()
    assert cfg.speech_rate == 2.0  # clamped to maximum


def test_negative_min_words_clamps_to_zero(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "min_words": -5})
    cfg = config.load()
    assert cfg.min_words == 0


def test_say_voice_map_user_override_merges(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "say_voice_map": {"aura-2-thalia-en": "Bob"}})
    cfg = config.load()
    assert cfg.say_voice_map["aura-2-thalia-en"] == "Bob"   # overridden
    assert cfg.say_voice_map["aura-2-orion-en"] == "Alex"   # default preserved


def test_say_voice_map_is_immutable(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True})
    cfg = config.load()
    with pytest.raises(TypeError):
        cfg.say_voice_map["aura-2-thalia-en"] = "Bob"  # MappingProxyType blocks


def test_garbage_in_voice_map_is_ignored(plugin_root: Path, write_config):
    from scripts import config

    write_config({
        "enabled": True,
        "say_voice_map": {"aura-2-thalia-en": "Bob", "bad": 123, 42: "x"},
    })
    cfg = config.load()
    assert cfg.say_voice_map["aura-2-thalia-en"] == "Bob"
    assert "bad" not in cfg.say_voice_map  # value not a string → rejected
