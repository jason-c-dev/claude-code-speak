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


# --- Piper backend ---

def test_piper_returns_none_when_voice_not_configured(voice_home, monkeypatch):
    from scripts import tts
    assert tts._synthesize_piper(text="hi", voice_path="") is None


def test_piper_returns_none_when_model_missing(voice_home, monkeypatch, tmp_path):
    from scripts import tts
    missing = tmp_path / "no-such-voice.onnx"
    assert tts._synthesize_piper(text="hi", voice_path=str(missing)) is None


def test_piper_returns_none_when_binary_missing(voice_home, monkeypatch, tmp_path):
    """If `piper` isn't on PATH, fail gracefully."""
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")  # exists, content irrelevant for this test
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    assert tts._synthesize_piper(text="hi", voice_path=str(voice)) is None


def test_piper_writes_wav_on_success(voice_home, monkeypatch, tmp_path):
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/fake/piper")

    captured = {}
    def fake_run(args, input, text, check, capture_output, timeout):
        captured["args"] = args
        captured["input"] = input
        # Find --output_file and write a fake wav there
        out_idx = args.index("--output_file") + 1
        Path(args[out_idx]).write_bytes(b"RIFF????WAVEfmt ")

        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    path = tts._synthesize_piper(text="hello world", voice_path=str(voice),
                                  speech_rate=1.0)
    assert path is not None
    assert path.suffix == ".wav"
    assert path.exists()
    assert "--model" in captured["args"]
    assert str(voice) in captured["args"]
    assert "--length-scale" in captured["args"]
    assert captured["input"] == "hello world"


def test_piper_length_scale_inverts_speech_rate(voice_home, monkeypatch, tmp_path):
    """speech_rate=2.0 → length-scale=0.5 (faster speech)."""
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/fake/piper")

    captured = {}
    def fake_run(args, input, text, check, capture_output, timeout):
        captured["args"] = args
        out_idx = args.index("--output_file") + 1
        Path(args[out_idx]).write_bytes(b"wav")
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    tts._synthesize_piper(text="hi", voice_path=str(voice), speech_rate=2.0)
    ls_idx = captured["args"].index("--length-scale") + 1
    assert float(captured["args"][ls_idx]) == pytest.approx(0.5, rel=1e-3)


def test_piper_returns_none_on_nonzero_exit(voice_home, monkeypatch, tmp_path):
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/fake/piper")

    def fake_run(args, input, text, check, capture_output, timeout):
        class R:
            returncode = 1
            stderr = "error: model load failed"
        return R()
    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    assert tts._synthesize_piper(text="hi", voice_path=str(voice)) is None


def test_piper_expands_tilde_in_voice_path(voice_home, monkeypatch, tmp_path):
    """`~/piper-voices/...` should resolve to $HOME."""
    from scripts import tts
    monkeypatch.setenv("HOME", str(tmp_path))
    voice = tmp_path / "voices" / "v.onnx"
    voice.parent.mkdir(parents=True)
    voice.write_bytes(b"x")
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/fake/piper")
    captured = {}
    def fake_run(args, input, text, check, capture_output, timeout):
        captured["args"] = args
        out_idx = args.index("--output_file") + 1
        Path(args[out_idx]).write_bytes(b"wav")
        class R:
            returncode = 0
            stderr = ""
        return R()
    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    out = tts._synthesize_piper(text="hi", voice_path="~/voices/v.onnx")
    assert out is not None
    # The resolved --model arg must be the actual path, not the literal tilde
    model_idx = captured["args"].index("--model") + 1
    assert "~" not in captured["args"][model_idx]
    assert str(voice) == captured["args"][model_idx]


# --- Chain integration with Piper ---

def test_chain_uses_piper_as_primary(voice_home, monkeypatch, write_config, tmp_path):
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    write_config({
        "enabled": True, "primary_tts": "piper", "fallback_tts": "say",
        "piper_voice": str(voice),
    })
    monkeypatch.setattr(tts, "_synthesize_piper",
                         lambda **kw: Path("/tmp/p.wav"))
    monkeypatch.setattr(tts, "_synthesize_say",
                         lambda **kw: pytest.fail("should not fallback"))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert tts.synthesize("hi") == Path("/tmp/p.wav")


def test_chain_falls_back_to_piper_when_deepgram_fails(
    voice_home, monkeypatch, write_config, tmp_path,
):
    from scripts import tts
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    write_config({
        "enabled": True, "primary_tts": "deepgram", "fallback_tts": "piper",
        "piper_voice": str(voice),
    })
    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)
    monkeypatch.setattr(tts, "_synthesize_piper",
                         lambda **kw: Path("/tmp/p.wav"))
    monkeypatch.setattr(tts, "_synthesize_say",
                         lambda **kw: pytest.fail("should not reach say"))
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    assert tts.synthesize("hi") == Path("/tmp/p.wav")


def test_chain_drops_unknown_backend_names(voice_home, monkeypatch, write_config):
    """A typo'd backend name in config shouldn't crash — log and skip."""
    from scripts import tts
    write_config({"enabled": True, "primary_tts": "elevenlabs", "fallback_tts": "say"})
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: Path("/tmp/say.aiff"))
    assert tts.synthesize("hi") == Path("/tmp/say.aiff")
