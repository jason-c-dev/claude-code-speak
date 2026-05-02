from pathlib import Path

import pytest


def test_create_and_read_state(voice_home: Path):
    from scripts import state

    s = state.load("session-abc")
    assert s.session_id == "session-abc"
    assert s.spoken_offsets == {}
    assert s.current_pid is None


def test_record_and_read_offset(voice_home: Path):
    from scripts import state

    s = state.load("session-abc")
    s.spoken_offsets["msg-1"] = 120
    state.save(s)

    s2 = state.load("session-abc")
    assert s2.spoken_offsets == {"msg-1": 120}


def test_set_and_clear_pid(voice_home: Path):
    from scripts import state

    s = state.load("session-xyz")
    s.current_pid = 12345
    state.save(s)

    assert state.load("session-xyz").current_pid == 12345

    s = state.load("session-xyz")
    s.current_pid = None
    state.save(s)
    assert state.load("session-xyz").current_pid is None


def test_remove_session(voice_home: Path):
    from scripts import state

    s = state.load("session-bye")
    s.spoken_offsets["m"] = 1
    state.save(s)
    assert (voice_home / "state" / "session-bye.json").exists()

    state.remove("session-bye")
    assert not (voice_home / "state" / "session-bye.json").exists()


def test_clean_stale(voice_home: Path, monkeypatch):
    """clean_stale removes state files older than the cutoff."""
    from scripts import state
    import os, time

    fresh = voice_home / "state" / "fresh.json"
    stale = voice_home / "state" / "stale.json"
    fresh.write_text("{}")
    stale.write_text("{}")

    # Backdate stale to 25 hours ago.
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))

    state.clean_stale(max_age_seconds=24 * 3600)

    assert fresh.exists()
    assert not stale.exists()


def test_corrupt_state_file_resets(voice_home: Path):
    from scripts import state

    p = voice_home / "state" / "broken.json"
    p.write_text("{not json")
    s = state.load("broken")
    assert s.session_id == "broken"
    assert s.spoken_offsets == {}
    assert s.current_pid is None
