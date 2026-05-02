"""Tests for scripts/speak_cli.py — the Bash-invokable narration CLI."""
import pytest


async def _async_return(value):
    return value


def test_cli_speaks_when_mode_b(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, extract, tts, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Looking that up"))
    fake = voice_home / "tmp" / "cue.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    synth_calls = []
    def fake_synth(text):
        synth_calls.append(text)
        return fake
    monkeypatch.setattr(tts, "synthesize", fake_synth)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0
    assert synth_calls, "synthesize should have been called"
    assert enqueued, "playback.enqueue should have been called"


def test_cli_silent_in_mode_a(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak_cli, tts

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("should not synth in mode A"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_when_disabled(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": False, "mode": "B"})
    from scripts import speak_cli, tts

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("should not synth when disabled"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_with_empty_text(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, tts

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("should not synth on empty text"))
    rc = speak_cli.main(["speak_cli.py", "--inline", ""])
    assert rc == 0


def test_cli_joins_multiple_args_into_one_phrase(voice_home, plugin_root,
                                                   write_config, monkeypatch):
    """If invoked without quotes, argv comes in as multiple words; join them."""
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, tts, playback

    fake = voice_home / "tmp" / "j.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    synth_calls = []
    def fake_synth(text):
        synth_calls.append(text)
        return fake
    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(playback, "enqueue", lambda s, p: None)

    speak_cli.main(["speak_cli.py", "--inline", "Looking", "that", "up"])
    assert any("Looking that up" in t for t in synth_calls)


def test_cli_uses_latest_session_id(voice_home, plugin_root, write_config, monkeypatch):
    """The CLI looks up which session to enqueue into via the most-recent state file."""
    import time
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, extract, tts, playback

    state_dir = voice_home / "state"
    older = state_dir / "older-session.json"
    newer = state_dir / "newer-session.json"
    older.write_text("{}")
    newer.write_text("{}")
    # Make newer's mtime later than older's.
    now = time.time()
    import os
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("voiced"))
    fake = voice_home / "tmp" / "s.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    speak_cli.main(["speak_cli.py", "--inline", "On it now"])
    assert enqueued, "should have enqueued"
    assert enqueued[0][0] == "newer-session"


def test_cli_bypasses_haiku_even_when_config_rewrite_is_true(
    voice_home, plugin_root, write_config, monkeypatch
):
    """The CLI is for short, intentional cues. cfg.rewrite=True controls the Stop
    hook's final-response polish but must NOT pull Haiku into pre-tool narration."""
    write_config({"enabled": True, "mode": "B", "rewrite": True})
    from scripts import speak_cli, extract, tts, playback

    captured = {}
    def fake_voicify(text, *, model, max_chars, rewrite_enabled):
        captured["rewrite_enabled"] = rewrite_enabled
        return text
    monkeypatch.setattr(extract, "voicify", fake_voicify)
    fake = voice_home / "tmp" / "x.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    monkeypatch.setattr(playback, "enqueue", lambda s, p: None)

    speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert captured.get("rewrite_enabled") is False


def test_cli_skips_when_synthesis_fails(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, extract, tts, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("voiced"))
    monkeypatch.setattr(tts, "synthesize", lambda text: None)
    monkeypatch.setattr(playback, "enqueue",
                        lambda s, p: pytest.fail("enqueue should not run if synth failed"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Trying this"])
    assert rc == 0
