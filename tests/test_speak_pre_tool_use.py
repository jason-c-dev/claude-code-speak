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
    assert enqueued == [("S", "running a command")]


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
    assert enqueued == [("S", "reading the file")]


def test_pre_tool_use_dedups_consecutive_identical_cues(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Three Edit calls in a row should speak the cue once, not three times."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Edit"}
    for _ in range(3):
        speak.run(json.dumps(payload))
    assert enqueued == [("S", "making an edit")], \
        f"only the first Edit cue should speak; got {enqueued}"


def test_pre_tool_use_speaks_when_phrase_changes(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Different tools (different phrases) must each speak. The dedup is
    only against the last consecutive identical phrase, not 'have we ever
    said this'."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    fire = lambda tool: speak.run(json.dumps(
        {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": tool}))

    # Edit, Edit, Write, Edit — second Edit dedups, Write speaks, second Edit
    # speaks again because the previous cue was Write, not Edit.
    fire("Edit")
    fire("Edit")
    fire("Write")
    fire("Edit")
    assert enqueued == [
        ("S", "making an edit"),
        ("S", "writing a file"),
        ("S", "making an edit"),
    ], f"got {enqueued}"


def test_pre_tool_use_renders_basename_from_tool_input(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Edit cue should include the basename of the file being edited."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {
        "hook_event_name": "PreToolUse", "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/Users/jason/dev/claude-chat/scripts/speak.py"},
    }
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "editing speak.py")]


def test_pre_tool_use_dedup_passes_for_different_files(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Two Edits to DIFFERENT files render different phrases, so both speak.
    The dedup naturally adapts — it compares the rendered phrase, not the
    tool name."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    fire_edit = lambda path: speak.run(json.dumps({
        "hook_event_name": "PreToolUse", "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": path},
    }))

    fire_edit("/x/foo.py")
    fire_edit("/x/foo.py")  # same file → dedups
    fire_edit("/x/bar.py")  # different file → speaks
    fire_edit("/x/bar.py")  # same again → dedups
    fire_edit("/x/foo.py")  # back to foo → speaks (last cue was bar)
    assert enqueued == [
        ("S", "editing foo.py"),
        ("S", "editing bar.py"),
        ("S", "editing foo.py"),
    ], f"got {enqueued}"


def test_pre_tool_use_dedup_resets_on_user_prompt_submit(
    voice_home, plugin_root, write_config, monkeypatch
):
    """A new user prompt clears the dedup cache so the first cue of the new
    turn plays even if it matches the last cue of the previous turn."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))
    monkeypatch.setattr(playback, "clear_and_kill", lambda sid: None)

    fire_tool = lambda tool: speak.run(json.dumps(
        {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": tool}))

    # Turn 1: two Edits — first speaks, second dedups.
    fire_tool("Edit")
    fire_tool("Edit")
    assert enqueued == [("S", "making an edit")]

    # User prompts again — UserPromptSubmit must reset the dedup cache.
    speak.run(json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": "S",
        "transcript_path": "/nonexistent",
    }))

    # Turn 2: another Edit — should speak (dedup was reset).
    fire_tool("Edit")
    assert enqueued == [
        ("S", "making an edit"),
        ("S", "making an edit"),
    ], f"second turn's first Edit should speak; got {enqueued}"
