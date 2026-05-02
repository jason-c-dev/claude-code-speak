import pytest


def test_voicify_returns_haiku_output(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        assert model == "claude-haiku-4-5"
        assert "stripped input" in text
        return "Polished spoken sentence."

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("stripped input here", model="claude-haiku-4-5")
    assert out == "Polished spoken sentence."


def test_voicify_truncates_long_input(monkeypatch):
    from scripts import extract

    captured = {}

    async def fake_async(text, model):
        captured["text"] = text
        return "ok"

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    long_text = "X" * 10000
    extract.voicify(long_text, max_chars=4000)
    assert len(captured["text"]) == 4000
    # Truncate from the start (keep the most recent prose).
    assert captured["text"] == long_text[-4000:]


def test_voicify_falls_back_to_input_on_sdk_error(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        raise RuntimeError("auth failed")

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("plain stripped text", rewrite_enabled=True)
    assert out == "plain stripped text"  # raw fallback


def test_voicify_skips_when_disabled(monkeypatch):
    from scripts import extract

    called = {"n": 0}

    async def fake_async(text, model):
        called["n"] += 1
        return "should not be called"

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("plain", rewrite_enabled=False)
    assert out == "plain"
    assert called["n"] == 0


def test_voicify_passes_empty_through(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        raise AssertionError("should not be called for empty input")

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    assert extract.voicify("") == ""
    assert extract.voicify("   ") == ""
