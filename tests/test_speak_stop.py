import io
import json
import sys
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_stop_pipeline_strip_voicify_synth_enqueue(voice_home, plugin_root,
                                                    write_config, monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})

    # Build a transcript with a final assistant message containing prose + code.
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant",
         "message": {"id": "m1", "content": [
             {"type": "text", "text": "Here is what I did. ```python\nx=1\n``` That is all."}
         ]}},
    ])

    from scripts import speak, extract, tts, playback, state as state_mod

    # Mark session interactive AND active (UserPromptSubmit would have done both).
    s = state_mod.load("abc")
    s.interactive = True
    state_mod.save(s)
    state_mod.set_active_session("abc")

    # Mock the rewrite step so we don't hit the SDK.
    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Polished!"))
    # Mock TTS to return a dummy file.
    fake_audio = voice_home / "tmp" / "fake.mp3"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"audio")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake_audio)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda session, path: enqueued.append((session, path)))

    payload = {"session_id": "abc", "transcript_path": str(transcript), "hook_event_name": "Stop"}
    rc = speak.run(json.dumps(payload))
    assert rc == 0
    assert enqueued == [("abc", fake_audio)]


def test_stop_skipped_when_session_not_interactive(voice_home, plugin_root,
                                                     write_config, monkeypatch, tmp_path):
    """Subagent and other non-human sessions never get UserPromptSubmit, so
    Stop must skip them — otherwise every parallel agent on the box speaks."""
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant",
         "message": {"id": "m1", "content": [
             {"type": "text", "text": "I'm ready when you are! What would you like to work on?"}
         ]}},
    ])
    from scripts import speak, tts, playback

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("non-interactive Stop should not synth"))
    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("non-interactive Stop should not enqueue"))

    payload = {"session_id": "subagent-xyz", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_with_disabled_config_is_noop(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": False})
    from scripts import speak, tts, playback

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not run"))
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: pytest.fail("enqueue should not run"))

    payload = {"session_id": "abc", "transcript_path": "/nonexistent",
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_skips_when_strip_returns_empty(voice_home, plugin_root, write_config,
                                              monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    # Pure code block — strip will return ''.
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "```python\nprint('x')\n```"}
        ]}},
    ])
    from scripts import speak, extract, tts, state as state_mod

    s = state_mod.load("x")
    s.interactive = True
    state_mod.save(s)
    state_mod.set_active_session("x")

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: pytest.fail("voicify should not be called"))
    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not be called"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_falls_back_to_stripped_when_voicify_returns_empty(
    voice_home, plugin_root, write_config, monkeypatch, tmp_path
):
    """Safety net: if Haiku returns empty for non-trivial input, voicify falls
    back to the stripped text so the turn still produces audio."""
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Here is some prose to speak about today."}
        ]}},
    ])
    from scripts import speak, extract, tts, playback, state as state_mod

    s = state_mod.load("x")
    s.interactive = True
    state_mod.save(s)
    state_mod.set_active_session("x")

    monkeypatch.setattr(extract, "_voicify_async", lambda text, model: _async_return(""))
    fake = voice_home / "tmp" / "fb.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"audio")
    synth_calls = []
    def fake_synth(text):
        synth_calls.append(text)
        return fake
    monkeypatch.setattr(tts, "synthesize", fake_synth)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0
    assert synth_calls, "synthesize should have been called with stripped fallback"
    assert "Here is some prose" in synth_calls[0]
    assert enqueued == [("x", fake)]


def test_stop_skipped_when_session_is_not_active(voice_home, plugin_root,
                                                   write_config, monkeypatch, tmp_path):
    """When a second Claude Code window has more recently received a prompt,
    the older window's Stop must NOT speak — only the active session does."""
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Old window response that should not speak."}
        ]}},
    ])
    from scripts import speak, tts, playback, state as state_mod

    # Both sessions are interactive, but only "newer-window" is active.
    s1 = state_mod.load("older-window")
    s1.interactive = True
    state_mod.save(s1)
    s2 = state_mod.load("newer-window")
    s2.interactive = True
    state_mod.save(s2)
    state_mod.set_active_session("newer-window")

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("inactive session should not synth"))
    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("inactive session should not enqueue"))

    payload = {"session_id": "older-window", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


# --- helpers ---

async def _async_return(value):
    return value
