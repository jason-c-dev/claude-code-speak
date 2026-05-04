import os
import time
from pathlib import Path

import pytest


def _make_audio_file(voice_home: Path, name: str = "a.mp3") -> Path:
    p = voice_home / "tmp" / name
    p.write_bytes(b"audio")
    return p


def test_enqueue_appends_to_queue(voice_home, monkeypatch):
    from scripts import playback, state

    started_pids = []
    def fake_popen(args, **kw):
        class P:
            pid = 90000 + len(started_pids)
        started_pids.append(P.pid)
        return P()
    monkeypatch.setattr(playback.subprocess, "Popen", fake_popen)

    playback.enqueue("sess1", "first chunk")
    playback.enqueue("sess1", "second chunk")

    s = state.load("sess1")
    # Both chunks queued; first one's pid recorded as current.
    assert "first chunk" in s.queue
    assert "second chunk" in s.queue
    # Exactly one player started for this burst (second enqueue reuses).
    assert len(started_pids) == 1


def test_clear_and_kill(voice_home, monkeypatch):
    """clear_and_kill must SIGTERM the player's *process group* (not just its
    pid) so any ffplay/afplay child it spawned dies too. Monkeypatch killpg
    rather than kill — kill is only the PermissionError fallback path."""
    from scripts import playback, state

    killed = []
    monkeypatch.setattr(playback.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(playback.os, "killpg",
                        lambda pgid, sig: killed.append((pgid, sig)))

    s = state.load("sess2")
    s.current_pid = 12345
    s.queue = ["/tmp/x.mp3", "/tmp/y.mp3"]
    state.save(s)

    playback.clear_and_kill("sess2")

    assert killed and killed[0][0] == 12345
    s2 = state.load("sess2")
    assert s2.queue == []
    assert s2.current_pid is None


def test_clear_and_kill_handles_missing_pid(voice_home):
    from scripts import playback, state
    # Should not raise when there's nothing to kill.
    playback.clear_and_kill("nonexistent-session")
    s = state.load("nonexistent-session")
    assert s.queue == []
    assert s.current_pid is None


def test_player_loop_drains_queue(voice_home, monkeypatch, tmp_path):
    """The internal player loop processes queued files in FIFO order."""
    from scripts import playback, state

    a = _make_audio_file(voice_home, "a.mp3")
    b = _make_audio_file(voice_home, "b.mp3")

    s = state.load("sess3")
    s.queue = [str(a), str(b)]
    state.save(s)

    played = []
    def fake_run(args, check, capture_output, timeout):
        played.append(args[-1])  # last arg is the audio path
        Path(args[-1]).unlink(missing_ok=True)
        class R:
            returncode = 0
            stderr = b""
        return R()
    monkeypatch.setattr(playback.subprocess, "run", fake_run)

    playback.player_loop("sess3")

    assert played == [str(a), str(b)]
    s2 = state.load("sess3")
    assert s2.queue == []
    assert s2.current_pid is None
