"""Tests for streaming Deepgram → ffplay pipeline."""
from __future__ import annotations
import io
import urllib.error

import pytest


class _ChunkedFakeResponse:
    """Fake urllib response that yields bytes in chunks via read(n)."""
    def __init__(self, chunks: list[bytes]):
        # Append b"" sentinel to signal EOF.
        self._chunks = list(chunks) + [b""]
        self._idx = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakePopen:
    def __init__(self, pid: int = 11111, returncode: int = 0):
        self.pid = pid
        self.stdin = _FakeStdin()
        self._rc = returncode
        self.waited = False
        self.killed = False

    def wait(self, timeout=None):
        self.waited = True
        return self._rc

    def kill(self):
        self.killed = True

    def terminate(self):
        self.killed = True


def test_stream_deepgram_pipes_response_to_ffplay_stdin(voice_home, monkeypatch):
    from scripts import tts

    fake_player = _FakePopen(pid=12345)
    popen_args: list = []
    popen_kwargs: list = []

    def fake_popen(args, **kw):
        popen_args.append(args)
        popen_kwargs.append(kw)
        return fake_player

    monkeypatch.setattr(tts.shutil, "which", lambda name: "/opt/homebrew/bin/ffplay")
    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        tts.urllib.request, "urlopen",
        lambda req, timeout: _ChunkedFakeResponse([b"id3-", b"frame1-", b"frame2"]),
    )

    pids: list[int] = []
    ok = tts._stream_deepgram_to_ffplay(
        text="hello there",
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=pids.append,
    )

    assert ok is True
    assert b"".join(fake_player.stdin.writes) == b"id3-frame1-frame2"
    assert fake_player.stdin.closed is True
    assert fake_player.waited is True
    assert pids == [12345]

    # ffplay must be invoked with low-latency flags or audio start gets delayed
    # by stream-probing and analysis (~3-5s on mp3-from-pipe by default).
    cmd = popen_args[0]
    assert "-fflags" in cmd and "nobuffer" in cmd
    assert "-probesize" in cmd
    assert "-analyzeduration" in cmd
    # bufsize=0 ensures every write to ffplay's stdin hits the pipe immediately
    # rather than sitting in Python's BufferedWriter until the 8KB threshold.
    assert popen_kwargs[0].get("bufsize") == 0


def test_stream_deepgram_flushes_each_write(voice_home, monkeypatch):
    """Belt-and-suspenders: even if bufsize were non-zero, an explicit flush
    after every write keeps audio bytes moving to ffplay continuously."""
    from scripts import tts

    flushes: list[int] = []

    class _FlushingStdin(_FakeStdin):
        def flush(self):
            flushes.append(len(self.writes))

    class _FlushingPopen(_FakePopen):
        def __init__(self):
            super().__init__(pid=55555)
            self.stdin = _FlushingStdin()

    fake_player = _FlushingPopen()

    monkeypatch.setattr(tts.shutil, "which", lambda name: "/x/ffplay")
    monkeypatch.setattr(tts.subprocess, "Popen", lambda *a, **kw: fake_player)
    monkeypatch.setattr(
        tts.urllib.request, "urlopen",
        lambda req, timeout: _ChunkedFakeResponse([b"a", b"b", b"c"]),
    )

    tts._stream_deepgram_to_ffplay(
        text="x",
        voice="x",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=lambda p: None,
    )

    # One flush per non-empty chunk written.
    assert flushes == [1, 2, 3]


def test_stream_deepgram_returns_false_when_ffplay_missing(voice_home, monkeypatch):
    from scripts import tts

    monkeypatch.setattr(tts.shutil, "which", lambda name: None)

    pids: list[int] = []
    ok = tts._stream_deepgram_to_ffplay(
        text="hi",
        voice="x",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=pids.append,
    )
    assert ok is False
    assert pids == []


def test_stream_deepgram_returns_false_on_http_error_and_kills_player(voice_home, monkeypatch):
    from scripts import tts

    fake_player = _FakePopen(pid=22222)

    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/ffplay")
    monkeypatch.setattr(tts.subprocess, "Popen", lambda *a, **kw: fake_player)

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauth", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)

    ok = tts._stream_deepgram_to_ffplay(
        text="hi",
        voice="x",
        api_key="bad",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=lambda p: None,
    )
    assert ok is False
    assert fake_player.killed is True


def test_stream_deepgram_returns_false_on_no_api_key(voice_home, monkeypatch):
    from scripts import tts

    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/ffplay")

    ok = tts._stream_deepgram_to_ffplay(
        text="hi",
        voice="x",
        api_key="",
        speech_rate=1.0,
        max_chars=2000,
        on_player_pid=lambda p: None,
    )
    assert ok is False


def test_synthesize_and_play_uses_streaming_when_available(voice_home, monkeypatch, write_config):
    """Top-level entrypoint prefers streaming Deepgram→ffplay path."""
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    calls = {"stream": 0, "file_dg": 0, "say": 0}
    def fake_stream(**kw):
        calls["stream"] += 1
        kw["on_player_pid"](33333)
        return True
    def fake_dg(**kw):
        calls["file_dg"] += 1
        return None
    def fake_say(**kw):
        calls["say"] += 1
        return None

    monkeypatch.setattr(tts, "_stream_deepgram_to_ffplay", fake_stream)
    monkeypatch.setattr(tts, "_synthesize_deepgram", fake_dg)
    monkeypatch.setattr(tts, "_synthesize_say", fake_say)

    pids: list[int] = []
    ok = tts.synthesize_and_play("hello there", on_player_pid=pids.append)

    assert ok is True
    assert calls == {"stream": 1, "file_dg": 0, "say": 0}
    assert pids == [33333]


def test_synthesize_and_play_falls_back_to_file_when_streaming_unavailable(
    voice_home, monkeypatch, write_config
):
    """If streaming returns False (e.g. no ffplay), fall back to file-based synth."""
    from scripts import tts
    from pathlib import Path

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    monkeypatch.setattr(tts, "_stream_deepgram_to_ffplay", lambda **kw: False)

    fake_path = voice_home / "tmp" / "fallback.mp3"
    fake_path.write_bytes(b"mp3")
    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: fake_path)

    afplay_calls: list[list] = []
    fake_proc = _FakePopen(pid=44444, returncode=0)
    def fake_popen(args, **kw):
        afplay_calls.append(args)
        return fake_proc
    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)

    pids: list[int] = []
    ok = tts.synthesize_and_play("hello there", on_player_pid=pids.append)

    assert ok is True
    assert len(afplay_calls) == 1
    assert afplay_calls[0][0] == "afplay"
    assert str(fake_path) in afplay_calls[0]
    assert pids == [44444]


def test_synthesize_and_play_returns_false_when_all_backends_fail(
    voice_home, monkeypatch, write_config
):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    monkeypatch.setattr(tts, "_stream_deepgram_to_ffplay", lambda **kw: False)
    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: None)

    ok = tts.synthesize_and_play("hello there", on_player_pid=lambda p: None)
    assert ok is False
