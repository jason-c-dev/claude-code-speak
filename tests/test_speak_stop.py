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
    from scripts import speak, extract, tts, playback

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


def test_stop_in_mode_b_speaks_only_text_past_offset(
    voice_home, plugin_root, write_config, monkeypatch, tmp_path
):
    """In mode B, Pre/PostToolUse may already have spoken some prose. Stop
    must speak only the new text past the recorded offset, not the whole
    message — otherwise it re-speaks "Fetching it now" before the new summary."""
    write_config({"enabled": True, "mode": "B"})
    transcript = tmp_path / "session.jsonl"
    full_message = "Fetching it now. Crowthorne shows nine degrees and foggy."
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m9", "content": [
            {"type": "text", "text": full_message}
        ]}},
    ])
    from scripts import speak, extract, tts, playback, state as state_mod

    # Pretend PreToolUse already spoke the first sentence.
    s = state_mod.load("S")
    s.spoken_offsets["m9"] = len("Fetching it now.")
    state_mod.save(s)

    captured = {}
    async def fake_voicify(text, model):
        captured["voiced_input"] = text
        return text  # round-trip the stripped tail
    monkeypatch.setattr(extract, "_voicify_async", fake_voicify)

    fake = voice_home / "tmp" / "tail.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "S", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    speak.run(json.dumps(payload))

    assert enqueued == [("S", fake)]
    # Voicify must have received the tail only, NOT the already-spoken intro.
    assert "Fetching it now" not in captured["voiced_input"]
    assert "Crowthorne" in captured["voiced_input"]

    # Offset must now be at the end of the full message.
    s2 = state_mod.load("S")
    assert s2.spoken_offsets["m9"] == len(full_message)


def test_stop_in_mode_b_skips_when_no_new_text(
    voice_home, plugin_root, write_config, monkeypatch, tmp_path
):
    """If the full message has already been spoken (offset == len), Stop is a no-op."""
    write_config({"enabled": True, "mode": "B"})
    transcript = tmp_path / "session.jsonl"
    full_message = "Already fully spoken."
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m10", "content": [
            {"type": "text", "text": full_message}
        ]}},
    ])
    from scripts import speak, tts, state as state_mod

    s = state_mod.load("S")
    s.spoken_offsets["m10"] = len(full_message)
    state_mod.save(s)

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    payload = {"session_id": "S", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


# --- helpers ---

async def _async_return(value):
    return value
