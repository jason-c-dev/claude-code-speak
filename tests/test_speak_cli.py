"""Tests for scripts/speak_cli.py — the Bash-invokable narration CLI."""
import pytest


async def _async_return(value):
    return value


def _mark_interactive(sid: str):
    from scripts import state as state_mod
    s = state_mod.load(sid)
    s.interactive = True
    state_mod.save(s)


def test_cli_speaks_when_mode_b(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak_cli, extract, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Looking that up"))
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0
    assert enqueued, "playback.enqueue should have been called with text"
    assert enqueued[0][1] == "Looking that up", \
        "enqueue must receive the voiced text, not a synthesized audio path"


def test_cli_short_circuits_when_internal_env_set(voice_home, plugin_root,
                                                    write_config, monkeypatch):
    """The CLI must respect CLAUDE_VOICE_INTERNAL — otherwise a Haiku rewrite
    subagent could call the CLI and double up on audio."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    monkeypatch.setenv("CLAUDE_VOICE_INTERNAL", "1")
    from scripts import speak_cli, playback

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("must not enqueue when internal"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_when_no_interactive_session(voice_home, plugin_root,
                                                  write_config, monkeypatch):
    """Without any interactive session marker, the CLI must not enqueue audio.
    This prevents subagents from playing audio that stacks with the user's
    own session."""
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, playback

    # Create state files but none flagged interactive — simulating subagent state.
    (voice_home / "state" / "subagent-1.json").write_text(
        '{"session_id": "subagent-1", "interactive": false, "queue": []}'
    )

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("must not enqueue without interactive session"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_in_mode_a(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    _mark_interactive("S")
    from scripts import speak_cli, playback

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("should not enqueue in mode A"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_when_disabled(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": False, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak_cli, playback

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("should not enqueue when disabled"))
    rc = speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert rc == 0


def test_cli_silent_with_empty_text(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak_cli, playback

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("should not enqueue on empty text"))
    rc = speak_cli.main(["speak_cli.py", "--inline", ""])
    assert rc == 0


def test_cli_joins_multiple_args_into_one_phrase(voice_home, plugin_root,
                                                   write_config, monkeypatch):
    """If invoked without quotes, argv comes in as multiple words; join them."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak_cli, playback

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    speak_cli.main(["speak_cli.py", "--inline", "Looking", "that", "up"])
    assert any("Looking that up" in t for _, t in enqueued)


def test_cli_picks_latest_interactive_session(voice_home, plugin_root,
                                                write_config, monkeypatch):
    """The CLI picks the most-recently-modified interactive session, skipping
    non-interactive ones (subagents) even if they're newer."""
    import time, os, json as _json
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak_cli, extract, tts, playback

    state_dir = voice_home / "state"
    older_interactive = state_dir / "older-interactive.json"
    newer_subagent = state_dir / "newer-subagent.json"
    older_interactive.write_text(_json.dumps(
        {"session_id": "older-interactive", "interactive": True, "queue": []}
    ))
    newer_subagent.write_text(_json.dumps(
        {"session_id": "newer-subagent", "interactive": False, "queue": []}
    ))
    now = time.time()
    os.utime(older_interactive, (now - 100, now - 100))
    os.utime(newer_subagent, (now, now))

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("voiced"))
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    speak_cli.main(["speak_cli.py", "--inline", "On it now"])
    assert enqueued, "should have enqueued"
    assert enqueued[0][0] == "older-interactive", \
        "should skip newer-but-non-interactive session"


def test_cli_bypasses_haiku_even_when_config_rewrite_is_true(
    voice_home, plugin_root, write_config, monkeypatch
):
    """The CLI is for short, intentional cues. cfg.rewrite=True controls the Stop
    hook's final-response polish but must NOT pull Haiku into pre-tool narration."""
    write_config({"enabled": True, "mode": "B", "rewrite": True})
    _mark_interactive("S")
    from scripts import speak_cli, extract, playback

    captured = {}
    def fake_voicify(text, *, model, max_chars, rewrite_enabled):
        captured["rewrite_enabled"] = rewrite_enabled
        return text
    monkeypatch.setattr(extract, "voicify", fake_voicify)
    monkeypatch.setattr(playback, "enqueue", lambda s, t: None)

    speak_cli.main(["speak_cli.py", "--inline", "Looking that up"])
    assert captured.get("rewrite_enabled") is False
