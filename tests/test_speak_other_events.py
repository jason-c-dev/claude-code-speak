import json
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


async def _async_return(v):
    return v


# --- UserPromptSubmit ---

def test_all_hooks_short_circuit_when_internal_env_set(voice_home, plugin_root,
                                                         write_config, monkeypatch):
    """When CLAUDE_VOICE_INTERNAL=1 (set by extract._voicify_async around the
    Haiku SDK call), every hook handler must immediately return — otherwise
    the rewrite subagent's own UserPromptSubmit/Stop fire and produce echoing
    speech."""
    write_config({"enabled": True, "mode": "B"})
    monkeypatch.setenv("CLAUDE_VOICE_INTERNAL", "1")
    from scripts import speak, tts, playback, state as state_mod

    # Even if pre-conditions for speech are met, internal invocation must
    # short-circuit before any handler runs.
    s = state_mod.load("S")
    s.interactive = True
    state_mod.save(s)
    state_mod.set_active_session("S")

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("internal hook must not synth"))
    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: pytest.fail("internal hook must not enqueue"))
    monkeypatch.setattr(playback, "clear_and_kill",
                        lambda sid: pytest.fail("internal hook must not even kill"))

    for ev in ("Stop", "PreToolUse", "PostToolUse", "UserPromptSubmit",
               "Notification", "SessionStart", "SessionEnd"):
        speak.run(json.dumps({"session_id": "S", "hook_event_name": ev,
                              "transcript_path": "/x", "message": "x"}))


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


def test_userpromptsubmit_marks_session_interactive(voice_home, plugin_root,
                                                     write_config, monkeypatch):
    """UserPromptSubmit is the only signal that a real human is driving this
    session. Stop and Notification later check this flag to filter out
    subagent/programmatic sessions."""
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, playback, state as state_mod

    monkeypatch.setattr(playback, "clear_and_kill", lambda sid: None)

    # Pre-condition: session not interactive.
    s = state_mod.load("S")
    assert s.interactive is False

    speak.run(json.dumps({"session_id": "S", "hook_event_name": "UserPromptSubmit"}))

    s2 = state_mod.load("S")
    assert s2.interactive is True


def test_userpromptsubmit_takes_over_active_session(voice_home, plugin_root,
                                                     write_config, monkeypatch):
    """A new UserPromptSubmit makes its session the only one allowed to speak.
    Previous active sessions get their queues cleared so they go silent
    immediately."""
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, playback, state as state_mod

    # Pre-existing 'old' session was previously active.
    s_old = state_mod.load("old-session")
    s_old.interactive = True
    state_mod.save(s_old)
    state_mod.set_active_session("old-session")

    killed = []
    monkeypatch.setattr(playback, "clear_and_kill", lambda sid: killed.append(sid))

    speak.run(json.dumps({"session_id": "new-session",
                          "hook_event_name": "UserPromptSubmit"}))

    assert state_mod.get_active_session() == "new-session"
    # old-session must have been silenced.
    assert "old-session" in killed
    # new-session also cleared (its own UserPromptSubmit normally does this).
    assert "new-session" in killed


def test_notification_skipped_for_non_interactive_session(voice_home, plugin_root,
                                                            write_config, monkeypatch):
    """Notification (mode C) must also gate on interactive. A subagent emitting
    a Notification shouldn't speak."""
    write_config({"enabled": True, "mode": "C"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize",
                        lambda text: pytest.fail("non-interactive Notification should not synth"))
    payload = {"session_id": "subagent", "hook_event_name": "Notification",
               "message": "Heads up!"}
    assert speak.run(json.dumps(payload)) == 0


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
    from scripts import speak, extract, playback, state as state_mod

    s = state_mod.load("S")
    s.interactive = True
    state_mod.save(s)
    state_mod.set_active_session("S")

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Heads up!"))
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"session_id": "S", "hook_event_name": "Notification",
               "message": "Claude needs your attention to continue."}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "Heads up!")]


def test_notification_skipped_in_mode_A(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "hook_event_name": "Notification",
                          "message": "x"}))


# --- PreToolUse / PostToolUse: removed in favor of speak_cli (mode B narration is
# Bash-driven by the assistant, not hook-scraped). The hooks no longer dispatch
# either event. Tests for the speak CLI live in test_speak_cli.py.


# --- SessionStart additionalContext (mode B narration instructions) ---

def test_sessionstart_emits_additional_context_in_mode_b(
    voice_home, plugin_root, write_config, capsys
):
    """In mode B, SessionStart emits a hookSpecificOutput.additionalContext block
    on stdout so each fresh Claude Code session learns to invoke the speak CLI."""
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak

    wrote = speak.emit_session_start_context_if_applicable(
        {"hook_event_name": "SessionStart"}
    )
    assert wrote is True
    out = capsys.readouterr().out
    assert "hookSpecificOutput" in out
    assert "additionalContext" in out
    assert "speak_cli.py" in out


def test_sessionstart_emits_nothing_in_mode_a(
    voice_home, plugin_root, write_config, capsys
):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak

    wrote = speak.emit_session_start_context_if_applicable(
        {"hook_event_name": "SessionStart"}
    )
    assert wrote is False
    assert capsys.readouterr().out == ""


def test_sessionstart_emits_nothing_in_mode_c(
    voice_home, plugin_root, write_config, capsys
):
    write_config({"enabled": True, "mode": "C"})
    from scripts import speak

    wrote = speak.emit_session_start_context_if_applicable(
        {"hook_event_name": "SessionStart"}
    )
    assert wrote is False
    assert capsys.readouterr().out == ""


def test_sessionstart_emits_nothing_when_disabled(
    voice_home, plugin_root, write_config, capsys
):
    write_config({"enabled": False, "mode": "B"})
    from scripts import speak

    wrote = speak.emit_session_start_context_if_applicable(
        {"hook_event_name": "SessionStart"}
    )
    assert wrote is False


def test_sessionstart_emits_nothing_for_other_events(
    voice_home, plugin_root, write_config, capsys
):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak

    wrote = speak.emit_session_start_context_if_applicable(
        {"hook_event_name": "Stop"}
    )
    assert wrote is False
    assert capsys.readouterr().out == ""
