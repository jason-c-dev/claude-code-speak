import json
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


async def _async_return(v):
    return v


# --- UserPromptSubmit ---

def test_userpromptsubmit_clears_queue_and_kills_pid(voice_home, plugin_root,
                                                     write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, playback, state as state_mod

    s = state_mod.load("S")
    s.queue = ["/tmp/x.mp3"]
    s.current_pid = 999
    state_mod.save(s)

    killed = []
    monkeypatch.setattr(playback, "clear_and_kill", lambda sid: killed.append(sid))

    payload = {"session_id": "S", "hook_event_name": "UserPromptSubmit"}
    speak.run(json.dumps(payload))
    assert killed == ["S"]


# --- SessionStart / SessionEnd ---

def test_sessionstart_cleans_stale(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, state as state_mod

    called = []
    monkeypatch.setattr(state_mod, "clean_stale", lambda max_age_seconds: called.append(max_age_seconds))
    speak.run(json.dumps({"session_id": "X", "hook_event_name": "SessionStart"}))
    assert called == [24 * 3600]


def test_sessionend_removes_state_and_tmp(voice_home, plugin_root, write_config):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, state as state_mod

    s = state_mod.load("Z")
    state_mod.save(s)
    assert (voice_home / "state" / "Z.json").exists()

    junk = voice_home / "tmp" / "leftover.mp3"
    junk.write_bytes(b"audio")

    speak.run(json.dumps({"session_id": "Z", "hook_event_name": "SessionEnd"}))
    assert not (voice_home / "state" / "Z.json").exists()
    assert not junk.exists()


# --- Notification (mode C) ---

def test_notification_speaks_message_in_mode_C(voice_home, plugin_root,
                                                 write_config, monkeypatch):
    write_config({"enabled": True, "mode": "C"})
    from scripts import speak, extract, tts, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Heads up!"))
    fake = voice_home / "tmp" / "n.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "S", "hook_event_name": "Notification",
               "message": "Claude needs your attention to continue."}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", fake)]


def test_notification_skipped_in_mode_A(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "hook_event_name": "Notification",
                          "message": "x"}))


# --- Pre/PostToolUse (mode B) ---

def test_pretooluse_speaks_text_since_last_offset_in_mode_B(voice_home, plugin_root,
                                                              write_config, monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "B"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Looking at the file now to find the bug."},
            {"type": "tool_use", "name": "Read"},
        ]}},
    ])

    from scripts import speak, extract, tts, playback, state as state_mod

    captured = {}
    async def fake_voicify(text, model):
        captured["text"] = text
        return "voiced"
    monkeypatch.setattr(extract, "_voicify_async", fake_voicify)
    fake = voice_home / "tmp" / "p.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "S", "transcript_path": str(transcript),
               "hook_event_name": "PreToolUse"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", fake)]
    assert "Looking at the file" in captured["text"]

    # Offset should now be at end of that text.
    s = state_mod.load("S")
    assert s.spoken_offsets["m1"] == len("Looking at the file now to find the bug.")


def test_pretooluse_skipped_in_mode_A(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "transcript_path": "/x",
                          "hook_event_name": "PreToolUse"}))


def test_pretooluse_skips_when_no_new_text(voice_home, plugin_root, write_config,
                                             monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak, tts, state as state_mod

    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Already spoken."},
            {"type": "tool_use", "name": "Read"},
        ]}},
    ])

    s = state_mod.load("S")
    s.spoken_offsets["m1"] = len("Already spoken.")
    state_mod.save(s)

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "transcript_path": str(transcript),
                          "hook_event_name": "PreToolUse"}))
