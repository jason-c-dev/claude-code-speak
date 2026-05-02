from pathlib import Path

import pytest


def test_say_writes_aiff(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_run(args, check, capture_output, timeout):
        captured["args"] = args
        # Pretend `say` wrote the output file.
        out_idx = args.index("-o") + 1
        Path(args[out_idx]).write_bytes(b"FORM????AIFF")

        class R:
            returncode = 0
            stderr = b""
        return R()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    path = tts._synthesize_say(text="testing one two three", voice_name="Samantha")
    assert path is not None
    assert path.suffix == ".aiff"
    assert path.exists()
    assert "Samantha" in captured["args"]
    assert "-v" in captured["args"]


def test_say_returns_none_on_failure(voice_home, monkeypatch):
    from scripts import tts

    def fake_run(args, check, capture_output, timeout):
        raise FileNotFoundError("say not found")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    assert tts._synthesize_say(text="hello", voice_name="Samantha") is None


def test_chain_uses_deepgram_when_configured(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: Path("/tmp/dg.mp3"))
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: pytest.fail("should not fallback"))

    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/dg.mp3")


def test_chain_falls_back_to_say_when_deepgram_fails(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)

    captured = {}
    def fake_say(*, text, voice_name):
        captured["voice"] = voice_name
        return Path("/tmp/say.aiff")
    monkeypatch.setattr(tts, "_synthesize_say", fake_say)

    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/say.aiff")
    assert captured["voice"] == "Samantha"  # mapped from aura-2-thalia-en


def test_chain_uses_say_when_no_deepgram_key(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram",
                        lambda **kw: pytest.fail("should not call DG without key"))
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: Path("/tmp/say.aiff"))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/say.aiff")


def test_chain_returns_none_when_both_fail(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True})
    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    assert tts.synthesize("hello there world") is None
