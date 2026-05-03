"""Tests for the PreToolUse hook handler in scripts/speak.py."""
import json


def _mark_interactive(sid: str):
    from scripts import state as state_mod
    s = state_mod.load(sid)
    s.interactive = True
    state_mod.save(s)


def test_pre_tool_use_speaks_known_tool_in_mode_b(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "running this")]


def test_pre_tool_use_uses_fallback_for_unknown_tool(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S",
               "tool_name": "NewMysteryTool"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "calling NewMysteryTool")]


def test_pre_tool_use_respects_user_overrides(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({
        "enabled": True, "mode": "B",
        "tool_phrases": {"Bash": "executing", "FrobTool": "frobbing"},
    })
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload_bash = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    payload_frob = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "FrobTool"}
    speak.run(json.dumps(payload_bash))
    speak.run(json.dumps(payload_frob))
    assert enqueued == [("S", "executing"), ("S", "frobbing")]


def test_pre_tool_use_skipped_in_mode_a(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "A"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    calls = []
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: calls.append((a, kw)))
    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    speak.run(json.dumps(payload))
    assert calls == [], f"enqueue was called unexpectedly in mode A: {calls}"


def test_pre_tool_use_skipped_when_session_not_interactive(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Subagent sessions never get UserPromptSubmit, so they remain
    non-interactive. Their tool calls must not produce audio."""
    write_config({"enabled": True, "mode": "B"})
    # Note: no _mark_interactive — subagent default state.
    from scripts import speak, playback
    calls = []
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: calls.append((a, kw)))
    payload = {"hook_event_name": "PreToolUse",
               "session_id": "subagent-X", "tool_name": "Bash"}
    speak.run(json.dumps(payload))
    assert calls == [], f"enqueue was called unexpectedly for non-interactive: {calls}"


def test_pre_tool_use_skipped_when_session_not_active(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Two interactive sessions can exist; only the most-recently-prompted one
    speaks. The other's PreToolUse must be silent."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("OTHER")
    _mark_interactive("ACTIVE")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("ACTIVE")  # OTHER is not active

    calls = []
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: calls.append((a, kw)))
    payload = {"hook_event_name": "PreToolUse",
               "session_id": "OTHER", "tool_name": "Bash"}
    speak.run(json.dumps(payload))
    assert calls == [], f"enqueue was called unexpectedly for non-active: {calls}"


def test_pre_tool_use_no_op_on_missing_tool_name(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    calls = []
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: calls.append((a, kw)))
    payload = {"hook_event_name": "PreToolUse", "session_id": "S"}
    speak.run(json.dumps(payload))
    assert calls == [], f"enqueue was called unexpectedly without tool_name: {calls}"


def test_pre_tool_use_speaks_in_mode_c(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "C"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Read"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "reading")]
