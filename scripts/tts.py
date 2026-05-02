"""Text-to-speech with Deepgram primary and macOS `say` fallback."""
from __future__ import annotations
import json
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
