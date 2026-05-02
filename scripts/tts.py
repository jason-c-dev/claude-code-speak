"""Text-to-speech with Deepgram primary and macOS `say` fallback."""
from __future__ import annotations
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from scripts.log import get_logger, voice_home

DEEPGRAM_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_TIMEOUT_SECONDS = 15


def _tmp_dir() -> Path:
    d = voice_home() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_env_file_into_os() -> None:
    """If DEEPGRAM_API_KEY isn't already set, read it from ~/.claude/voice/.env."""
    if os.environ.get("DEEPGRAM_API_KEY"):
        return
    env_path = voice_home() / ".env"
    if not env_path.exists():
        return
    try:
        text = env_path.read_text()
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        # Only set if not already set; never overwrite real env.
        os.environ.setdefault(k.strip(), v)


_load_env_file_into_os()


def _synthesize_deepgram(
    *,
    text: str,
    voice: str,
    api_key: str,
    speech_rate: float,
    max_chars: int,
) -> Path | None:
    """Return path to written mp3 on success, or None on any failure."""
    log = get_logger()
    if not api_key:
        return None

    if len(text) > max_chars:
        log.info("deepgram input %d chars exceeds %d; truncating", len(text), max_chars)
        text = text[:max_chars]

    qs = urllib.parse.urlencode({
        "model": voice,
        "encoding": "mp3",
        "speed": f"{speech_rate:.2f}",
    })
    url = f"{DEEPGRAM_URL}?{qs}"
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=DEEPGRAM_TIMEOUT_SECONDS) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        log.warning("deepgram returned %d: %s", e.code, e.reason)
        return None
    except urllib.error.URLError as e:
        log.warning("deepgram network error: %s", e.reason)
        return None
    except Exception as e:
        log.warning("deepgram unexpected error: %s", e)
        return None

    try:
        out = _tmp_dir() / f"{uuid.uuid4().hex}.mp3"
        out.write_bytes(audio)
        return out
    except OSError as e:
        log.warning("deepgram could not write tmp file: %s", e)
        return None


SAY_TIMEOUT_SECONDS = 15


def _synthesize_say(*, text: str, voice_name: str) -> Path | None:
    log = get_logger()
    try:
        out = _tmp_dir() / f"{uuid.uuid4().hex}.aiff"
    except OSError as e:
        log.warning("say could not create tmp dir: %s", e)
        return None
    try:
        result = subprocess.run(
            ["say", "-v", voice_name, "-o", str(out), text],
            check=False,
            capture_output=True,
            timeout=SAY_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            log.warning("say failed: rc=%d stderr=%s", result.returncode, result.stderr[:200])
            return None
        if not out.exists():
            log.warning("say returned 0 but produced no file at %s", out)
            return None
        return out
    except FileNotFoundError:
        log.warning("`say` binary not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        log.warning("say timed out")
        return None
    except Exception as e:
        log.warning("say unexpected error: %s", e)
        return None


def synthesize(text: str) -> Path | None:
    """Synthesize `text` to an audio file using the configured TTS chain.

    Returns the path to a playable file (mp3 or aiff), or None if every
    backend failed. Never raises.
    """
    log = get_logger()
    # Lazy import to avoid circular concerns if config evolves.
    from scripts.config import load as load_config
    cfg = load_config()
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    # Primary: Deepgram, only if configured *and* a key is present.
    if cfg.primary_tts == "deepgram" and api_key:
        path = _synthesize_deepgram(
            text=text,
            voice=cfg.voice,
            api_key=api_key,
            speech_rate=cfg.speech_rate,
            max_chars=cfg.max_deepgram_chars,
        )
        if path is not None:
            return path
        log.info("deepgram failed; falling back to %s", cfg.fallback_tts)

    # Fallback: macOS say.
    if cfg.fallback_tts == "say" or cfg.primary_tts == "say":
        say_voice = cfg.say_voice_map.get(cfg.voice, "Samantha")
        path = _synthesize_say(text=text, voice_name=say_voice)
        if path is not None:
            return path
        log.warning("say also failed; speech will be silent")

    return None
