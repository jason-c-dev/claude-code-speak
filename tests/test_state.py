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


def test_save_load_round_trip_of_queue(voice_home: Path):
    """The queue field round-trips through save/load."""
    from scripts import state

    s = state.load("with-queue")
    s.queue = ["/tmp/a.mp3", "/tmp/b.mp3"]
    s.current_pid = 7777
    s.spoken_offsets["m1"] = 42
    state.save(s)

    s2 = state.load("with-queue")
    assert s2.queue == ["/tmp/a.mp3", "/tmp/b.mp3"]
    assert s2.current_pid == 7777
    assert s2.spoken_offsets == {"m1": 42}


def test_save_does_not_raise_on_oserror(voice_home: Path, monkeypatch):
    """save() must swallow OSError per the voice-never-load-bearing invariant."""
    import os
    from scripts import state

    s = state.load("rb")
    s.spoken_offsets["x"] = 1

    def explode(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", explode)

    # Must not raise.
    state.save(s)


def test_save_is_atomic_no_torn_writes(voice_home: Path):
    """After save(), the on-disk file is either absent or a valid full document."""
    import json
    from scripts import state

    s = state.load("atomic")
    s.spoken_offsets["m"] = 99
    state.save(s)

    raw = json.loads((voice_home / "state" / "atomic.json").read_text())
    assert raw["spoken_offsets"] == {"m": 99}


def test_session_id_with_path_traversal_is_pinned(voice_home: Path):
    """A session id like '../escape' must not write outside state_dir."""
    from scripts import state

    s = state.load("../escape")
    s.spoken_offsets["x"] = 1
    state.save(s)

    state_files = list((voice_home / "state").glob("*.json"))
    # The file should be inside state/, with the slash sanitized.
    assert state_files, "save should have produced a file inside state/"
    for f in state_files:
        assert "/" not in f.name
    # And nothing escaped to the parent directory of state/.
    parent_files = list(voice_home.glob("escape*"))
    assert parent_files == []


def test_clean_stale_tolerates_unlink_errors(voice_home: Path, monkeypatch):
    """A clean_stale that hits a permission error on one file still reaps others."""
    from scripts import state
    import os, time

    state_d = voice_home / "state"
    a = state_d / "a.json"
    b = state_d / "b.json"
    a.write_text("{}")
    b.write_text("{}")
    old = time.time() - (25 * 3600)
    os.utime(a, (old, old))
    os.utime(b, (old, old))

    original_unlink = Path.unlink
    def flaky_unlink(self, *args, **kwargs):
        if self.name == "a.json":
            raise PermissionError("nope")
        return original_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    state.clean_stale(max_age_seconds=24 * 3600)
    # b should be reaped despite a's failure.
    assert not b.exists()
