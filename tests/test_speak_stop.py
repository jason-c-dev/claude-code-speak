import io
import json
import sys
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_stop_pipeline_strip_voicify_enqueues_text(voice_home, plugin_root,
                                                    write_config, monkeypatch, tmp_path):
    """The Stop pipeline strips, voicifies, chunks, and enqueues each chunk
    as text for the player loop to stream-synthesize."""
    write_config({"enabled": True, "mode": "A", "rewrite": True})

    # Build a transcript with a final assistant message containing prose + code.
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant",
         "message": {"id": "m1", "content": [
             {"type": "text", "text": "Here is what I did. ```python\nx=1\n``` That is all."}
         ]}},
    ])

    from scripts import speak, extract, playback, state as state_mod

    # Mark the session as human-interactive (UserPromptSubmit normally does this).
    s = state_mod.load("abc")
    s.interactive = True
    state_mod.save(s)

    # Mock the rewrite step so we don't hit the SDK.
    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Polished words go here today."))
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(playback, "enqueue", lambda session, text: enqueued.append((session, text)))

    payload = {"session_id": "abc", "transcript_path": str(transcript), "hook_event_name": "Stop"}
    rc = speak.run(json.dumps(payload))
    assert rc == 0
    assert enqueued == [("abc", "Polished words go here today.")]


def test_stop_with_disabled_config_is_noop(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": False})
    from scripts import speak, playback

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
    from scripts import speak, extract, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: pytest.fail("voicify should not be called"))
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: pytest.fail("enqueue should not run"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_skips_when_voicify_returns_empty(voice_home, plugin_root, write_config,
                                                monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Here is some prose to speak about today."}
        ]}},
    ])
    from scripts import speak, extract, playback

    monkeypatch.setattr(extract, "_voicify_async", lambda text, model: _async_return(""))
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: pytest.fail("enqueue should not run"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


# --- helpers ---

async def _async_return(value):
    return value
