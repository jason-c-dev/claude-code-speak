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

    from scripts import speak, extract, tts, playback

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
    from scripts import speak, extract, tts

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: pytest.fail("voicify should not be called"))
    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not be called"))
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
    from scripts import speak, extract, tts

    monkeypatch.setattr(extract, "_voicify_async", lambda text, model: _async_return(""))
    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not run"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


# --- helpers ---

async def _async_return(value):
    return value
