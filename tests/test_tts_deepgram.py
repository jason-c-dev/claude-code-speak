import io
import urllib.error
from pathlib import Path

import pytest


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_deepgram_writes_mp3_and_returns_path(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResponse(b"\x49\x44\x33fake-mp3-bytes")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)

    path = tts._synthesize_deepgram(
        text="hello world",
        voice="aura-2-thalia-en",
        api_key="dg_test",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is not None
    assert path.exists()
    assert path.suffix == ".mp3"
    assert path.read_bytes().startswith(b"\x49\x44\x33")
    assert "aura-2-thalia-en" in captured["url"]
    assert "encoding=mp3" in captured["url"]
    assert captured["headers"]["Authorization"] == "Token dg_test"
    assert b'"text": "hello world"' in captured["body"]


def test_deepgram_truncates_over_limit(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        return _FakeResponse(b"mp3")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    long_text = "y" * 5000
    tts._synthesize_deepgram(
        text=long_text,
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
    )
    # JSON-encoded body should contain at most ~2010 chars of "y" (2000 + JSON overhead).
    assert captured["body"].count(b"y") == 2000


def test_deepgram_returns_none_on_http_error(voice_home, monkeypatch):
    from scripts import tts

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauth", {}, io.BytesIO(b'{}'))

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    path = tts._synthesize_deepgram(
        text="hi there",
        voice="aura-2-thalia-en",
        api_key="bad",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is None


def test_deepgram_returns_none_on_timeout(voice_home, monkeypatch):
    from scripts import tts

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    path = tts._synthesize_deepgram(
        text="hi there",
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is None
