"""Tests for gapless multi-chunk streaming through a single ffplay process."""
from __future__ import annotations
import io
import urllib.error

import pytest

from tests.test_tts_streaming import _ChunkedFakeResponse, _FakeStdin, _FakePopen


def test_stream_session_uses_one_ffplay_for_multiple_chunks(voice_home, monkeypatch):
    """Multiple text chunks pulled via get_next should feed ONE ffplay process,
    not spawn a new one per chunk. That's how we eliminate the inter-chunk gap."""
    from scripts import tts

    fake_player = _FakePopen(pid=77777)
    spawn_count = 0
    def fake_popen(args, **kw):
        nonlocal spawn_count
        spawn_count += 1
        return fake_player
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/ffplay")
    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)

    # Each chunk gets its own Deepgram response.
    responses = [
        _ChunkedFakeResponse([b"chunkA-byte1-", b"chunkA-byte2"]),
        _ChunkedFakeResponse([b"chunkB-byte1-", b"chunkB-byte2"]),
        _ChunkedFakeResponse([b"chunkC-byte1"]),
    ]
    response_iter = iter(responses)
    monkeypatch.setattr(
        tts.urllib.request, "urlopen",
        lambda req, timeout: next(response_iter),
    )

    chunks = iter(["first sentence", "second sentence", "third sentence"])
    def get_next():
        return next(chunks, None)

    pids: list[int] = []
    ok = tts._stream_chunks_through_ffplay(
        get_next_text=get_next,
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=pids.append,
    )

    assert ok is True
    assert spawn_count == 1, "ffplay should be spawned exactly once"
    assert pids == [77777]
    # All bytes from all three chunks land in the single ffplay's stdin in order.
    expected = b"chunkA-byte1-chunkA-byte2chunkB-byte1-chunkB-byte2chunkC-byte1"
    assert b"".join(fake_player.stdin.writes) == expected
    assert fake_player.stdin.closed is True
    assert fake_player.waited is True


def test_stream_session_continues_after_one_chunk_fails(voice_home, monkeypatch):
    """If Deepgram fails on chunk 2, chunks 1 and 3 should still play through
    the same ffplay — we just skip the broken chunk."""
    from scripts import tts

    fake_player = _FakePopen(pid=88888)
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/x/ffplay")
    monkeypatch.setattr(tts.subprocess, "Popen", lambda *a, **kw: fake_player)

    call_n = 0
    def fake_urlopen(req, timeout):
        nonlocal call_n
        call_n += 1
        if call_n == 2:
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b""))
        if call_n == 1:
            return _ChunkedFakeResponse([b"good-A"])
        return _ChunkedFakeResponse([b"good-C"])
    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)

    chunks = iter(["A", "B-fails", "C"])
    def get_next():
        return next(chunks, None)

    ok = tts._stream_chunks_through_ffplay(
        get_next_text=get_next,
        voice="x", api_key="k", speech_rate=1.0, max_chars=2000,
        on_player_pid=lambda p: None,
    )

    assert ok is True  # at least one chunk streamed successfully
    assert b"".join(fake_player.stdin.writes) == b"good-Agood-C"
    assert fake_player.killed is False  # ffplay was NOT killed; still drained cleanly


def test_stream_session_returns_false_when_ffplay_missing(voice_home, monkeypatch):
    from scripts import tts

    monkeypatch.setattr(tts.shutil, "which", lambda name: None)

    ok = tts._stream_chunks_through_ffplay(
        get_next_text=lambda: "anything",
        voice="x", api_key="k", speech_rate=1.0, max_chars=2000,
        on_player_pid=lambda p: None,
    )
    assert ok is False


def test_stream_session_returns_false_when_no_chunks(voice_home, monkeypatch):
    """Edge case: get_next returns None on first call. Don't spawn ffplay at all."""
    from scripts import tts

    monkeypatch.setattr(tts.shutil, "which", lambda name: "/x/ffplay")
    spawn_count = 0
    def fake_popen(*a, **kw):
        nonlocal spawn_count
        spawn_count += 1
        return _FakePopen()
    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)

    ok = tts._stream_chunks_through_ffplay(
        get_next_text=lambda: None,
        voice="x", api_key="k", speech_rate=1.0, max_chars=2000,
        on_player_pid=lambda p: None,
    )
    assert ok is False
    assert spawn_count == 0


def test_player_loop_drains_queue_through_single_streaming_session(voice_home, monkeypatch):
    """player_loop should hand the entire queue to one streaming session, not
    call into tts once per item."""
    from scripts import playback, state, tts

    s = state.load("sess-stream-multi")
    s.queue = ["one", "two", "three"]
    state.save(s)

    seen_pulls: list[str] = []
    sessions_started = 0

    def fake_session(get_next_text, *, on_player_pid):
        nonlocal sessions_started
        sessions_started += 1
        while True:
            t = get_next_text()
            if t is None:
                break
            seen_pulls.append(t)
        on_player_pid(91234)
        return True

    monkeypatch.setattr(tts, "synthesize_and_play_session", fake_session)

    playback.player_loop("sess-stream-multi")

    assert sessions_started == 1, "exactly one streaming session for the burst"
    assert seen_pulls == ["one", "two", "three"]
    s2 = state.load("sess-stream-multi")
    assert s2.queue == []
