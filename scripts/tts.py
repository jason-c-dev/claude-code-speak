"""Text-to-speech with three local/cloud backends:
- Deepgram (cloud, neural Aura-2 voices, streaming-capable)
- Piper (local, neural ONNX voices, file-based)
- macOS `say` (local, classic system voices, file-based)

The synthesize chain walks `[cfg.primary_tts, cfg.fallback_tts]` in order
and returns the first backend that produces audio. Each backend
short-circuits gracefully if its prerequisites are missing (no API key,
binary not on PATH, model file missing, etc.) so callers can list any
combination without worrying about install state.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from scripts.log import get_logger, voice_home

DEEPGRAM_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_TIMEOUT_SECONDS = 15
# Size of each read() from the Deepgram response. Small enough that the first
# write to ffplay's stdin happens within ~one TTFB; large enough that we don't
# spin in tight loops over tiny TCP packets.
_STREAM_CHUNK_BYTES = 4096


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


PIPER_TIMEOUT_SECONDS = 30


def _synthesize_piper(
    *, text: str, voice_path: str, speech_rate: float = 1.0,
) -> Path | None:
    """Synthesize `text` via the local Piper TTS binary.

    Piper voice models are an `.onnx` file plus a sibling `.onnx.json`
    config file. `voice_path` points to the `.onnx`. Tilde is expanded
    so `~/piper-voices/...` works.

    Returns a wav path on success. Returns None — and logs why — on:
      - empty `voice_path` (Piper not configured)
      - missing `piper` binary (not installed / not on PATH)
      - missing model file
      - non-zero exit / timeout / unexpected error

    `speech_rate` maps to Piper's `--length-scale` inversely (rate=1.0 →
    length-scale=1.0; rate=2.0 → length-scale=0.5; etc.). Clamped to a
    sane range so a typo doesn't produce silence.
    """
    log = get_logger()
    if not voice_path:
        log.info("piper: piper_voice not configured; skipping")
        return None

    expanded = Path(voice_path).expanduser()
    if not expanded.exists():
        log.warning("piper: voice model not found at %s", expanded)
        return None

    if shutil.which("piper") is None:
        log.warning("piper: `piper` binary not found on PATH")
        return None

    try:
        out = _tmp_dir() / f"{uuid.uuid4().hex}.wav"
    except OSError as e:
        log.warning("piper: could not create tmp dir: %s", e)
        return None

    # Piper's --length-scale is the duration multiplier. To speed up speech
    # we shrink durations, so length_scale = 1 / speech_rate.
    safe_rate = max(0.5, min(speech_rate or 1.0, 2.0))
    length_scale = 1.0 / safe_rate

    try:
        result = subprocess.run(
            [
                "piper",
                "--model", str(expanded),
                "--output_file", str(out),
                "--length-scale", f"{length_scale:.4f}",
            ],
            input=text,
            text=True,
            check=False,
            capture_output=True,
            timeout=PIPER_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            log.warning("piper: rc=%d stderr=%s", result.returncode, result.stderr[:200])
            return None
        if not out.exists() or out.stat().st_size == 0:
            log.warning("piper: returned 0 but produced no audio at %s", out)
            return None
        return out
    except subprocess.TimeoutExpired:
        log.warning("piper: timed out after %ds", PIPER_TIMEOUT_SECONDS)
        return None
    except Exception as e:
        log.warning("piper: unexpected error: %s", e)
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


_VALID_BACKENDS = ("deepgram", "piper", "say")


def _try_backend(backend: str, *, text: str, cfg, api_key: str) -> Path | None:
    """Attempt one synthesis backend. Returns None on any failure (missing
    prerequisites, network error, etc.) so the caller can move on to the
    next backend in the chain."""
    log = get_logger()
    if backend == "deepgram":
        if not api_key:
            log.info("deepgram: no DEEPGRAM_API_KEY set; skipping")
            return None
        return _synthesize_deepgram(
            text=text,
            voice=cfg.voice,
            api_key=api_key,
            speech_rate=cfg.speech_rate,
            max_chars=cfg.max_deepgram_chars,
        )
    if backend == "piper":
        return _synthesize_piper(
            text=text,
            voice_path=cfg.piper_voice,
            speech_rate=cfg.speech_rate,
        )
    if backend == "say":
        say_voice = cfg.say_voice_map.get(cfg.voice, "Samantha")
        return _synthesize_say(text=text, voice_name=say_voice)
    log.warning("unknown TTS backend %r; skipping", backend)
    return None


def _backend_chain(cfg) -> list[str]:
    """Build the ordered list of backends to try: primary, then fallback if
    distinct. Unknown backend names are logged and dropped."""
    log = get_logger()
    chain: list[str] = []
    for slot, name in (("primary_tts", cfg.primary_tts),
                        ("fallback_tts", cfg.fallback_tts)):
        if not name:
            continue
        if name not in _VALID_BACKENDS:
            log.warning("config.%s: %r is not a known backend %s; skipping",
                        slot, name, _VALID_BACKENDS)
            continue
        if name not in chain:
            chain.append(name)
    return chain


def synthesize(text: str) -> Path | None:
    """Synthesize `text` to an audio file using the configured TTS chain.

    Walks `[cfg.primary_tts, cfg.fallback_tts]` in order, returning the
    first backend that produces a playable file. Returns None if every
    backend failed. Never raises.
    """
    log = get_logger()
    from scripts.config import load as load_config
    cfg = load_config()
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    for backend in _backend_chain(cfg):
        path = _try_backend(backend, text=text, cfg=cfg, api_key=api_key)
        if path is not None:
            return path
        log.info("%s synthesis failed; trying next backend", backend)

    log.warning("all TTS backends failed; speech will be silent")
    return None


_AFPLAY_TIMEOUT_SECONDS = 60


def _stream_deepgram_to_ffplay(
    *,
    text: str,
    voice: str,
    api_key: str,
    speech_rate: float,
    max_chars: int,
    on_player_pid: Callable[[int], None],
) -> bool:
    """Pipe Deepgram TTS bytes directly into ffplay stdin. Blocks until playback ends.

    Returns True on success, False on any setup or transport failure (caller
    should fall back to file-based synth + afplay). Never raises.

    `on_player_pid(pid)` is invoked once with the spawned ffplay's pid so the
    caller can record it for `clear_and_kill`.
    """
    log = get_logger()

    if not api_key:
        return False
    if shutil.which("ffplay") is None:
        log.info("ffplay not on PATH; streaming disabled")
        return False

    if len(text) > max_chars:
        log.info("deepgram-stream input %d chars exceeds %d; truncating",
                 len(text), max_chars)
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

    # Spawn ffplay first so we can pipe into it. Flags chosen for low latency:
    #   -nodisp / -autoexit / -loglevel error — quiet, headless, exits on EOF
    #   -fflags nobuffer        — disable input format buffering
    #   -probesize 32           — minimum bytes to detect the stream format
    #   -analyzeduration 0      — don't analyze duration before playback
    # Without these, ffplay buffers ~3-5s of audio before producing sound.
    # bufsize=0 makes Python's stdin a raw stream so each write hits the pipe
    # immediately rather than sitting in a BufferedWriter; we also flush after
    # each write as a belt-and-suspenders defense against buffering anywhere.
    # start_new_session puts ffplay in its own process group so the caller can
    # SIGTERM the group cleanly.
    try:
        player = subprocess.Popen(
            [
                "ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                "-fflags", "nobuffer",
                "-probesize", "32",
                "-analyzeduration", "0",
                "-i", "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
    except FileNotFoundError:
        log.info("ffplay disappeared between which() and Popen; falling back")
        return False
    except Exception as e:
        log.warning("could not spawn ffplay: %s", e)
        return False

    try:
        on_player_pid(player.pid)
    except Exception as e:
        log.warning("on_player_pid callback raised: %s", e)

    try:
        with urllib.request.urlopen(req, timeout=DEEPGRAM_TIMEOUT_SECONDS) as resp:
            while True:
                chunk = resp.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                try:
                    player.stdin.write(chunk)
                    player.stdin.flush()
                except (BrokenPipeError, OSError) as e:
                    log.warning("ffplay stdin closed mid-stream: %s", e)
                    break
    except urllib.error.HTTPError as e:
        log.warning("deepgram-stream returned %d: %s", e.code, e.reason)
        _kill_quietly(player)
        return False
    except urllib.error.URLError as e:
        log.warning("deepgram-stream network error: %s", e.reason)
        _kill_quietly(player)
        return False
    except Exception as e:
        log.warning("deepgram-stream unexpected error: %s", e)
        _kill_quietly(player)
        return False

    try:
        if player.stdin is not None:
            player.stdin.close()
    except Exception:
        pass

    try:
        player.wait()
    except Exception as e:
        log.warning("ffplay wait failed: %s", e)
        return False

    return True


def _kill_quietly(player) -> None:
    try:
        player.kill()
    except Exception:
        pass
    try:
        player.wait(timeout=2)
    except Exception:
        pass


def _stream_chunks_through_ffplay(
    *,
    get_next_text: Callable[[], "str | None"],
    voice: str,
    api_key: str,
    speech_rate: float,
    max_chars: int,
    on_player_pid: Callable[[int], None],
) -> bool:
    """Pipe several Deepgram TTS chunks into a single ffplay process.

    Pulls chunks via `get_next_text`; each call returns the next text chunk
    or None when there are no more. All chunks share one ffplay stdin, so
    audio plays gaplessly across chunk boundaries (the next chunk's bytes
    arrive while the previous chunk's audio is still draining out of ffplay's
    own buffer).

    Returns True if at least one chunk was successfully streamed; False if
    ffplay couldn't be spawned or no chunks arrived. Never raises.
    """
    log = get_logger()

    if not api_key:
        return False
    if shutil.which("ffplay") is None:
        log.info("ffplay not on PATH; streaming disabled")
        return False

    # Peek at the first chunk before spawning ffplay — if there's nothing
    # to play, don't bother starting a player.
    first = get_next_text()
    if first is None:
        return False

    try:
        player = subprocess.Popen(
            [
                "ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                "-fflags", "nobuffer",
                "-probesize", "32",
                "-analyzeduration", "0",
                "-i", "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
    except FileNotFoundError:
        log.info("ffplay disappeared between which() and Popen; falling back")
        return False
    except Exception as e:
        log.warning("could not spawn ffplay: %s", e)
        return False

    try:
        on_player_pid(player.pid)
    except Exception as e:
        log.warning("on_player_pid callback raised: %s", e)

    text: "str | None" = first
    any_streamed = False
    stdin_alive = True
    while text is not None:
        if stdin_alive:
            ok_chunk = _stream_one_chunk_to_player(
                text=text,
                player=player,
                voice=voice,
                api_key=api_key,
                speech_rate=speech_rate,
                max_chars=max_chars,
            )
            if ok_chunk == "broken_pipe":
                # ffplay died; we can't push any more bytes. Drain the queue
                # by pulling remaining chunks (so the caller's queue empties)
                # but stop writing.
                stdin_alive = False
            elif ok_chunk == "ok":
                any_streamed = True
            # ok_chunk == "skip" (Deepgram failure): try the next chunk.
        text = get_next_text()

    try:
        if player.stdin is not None:
            player.stdin.close()
    except Exception:
        pass

    try:
        player.wait()
    except Exception as e:
        log.warning("ffplay wait failed: %s", e)

    return any_streamed


def _stream_one_chunk_to_player(
    *,
    text: str,
    player,
    voice: str,
    api_key: str,
    speech_rate: float,
    max_chars: int,
) -> str:
    """Stream one Deepgram synthesis into an already-running ffplay's stdin.

    Returns "ok" on success, "skip" on a recoverable Deepgram failure (caller
    should try the next chunk), "broken_pipe" if ffplay's stdin died (caller
    should stop writing).
    """
    log = get_logger()

    if len(text) > max_chars:
        log.info("deepgram-stream input %d chars exceeds %d; truncating",
                 len(text), max_chars)
        text = text[:max_chars]

    qs = urllib.parse.urlencode({
        "model": voice,
        "encoding": "mp3",
        "speed": f"{speech_rate:.2f}",
    })
    url = f"{DEEPGRAM_URL}?{qs}"
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
    )

    pipe_broken = False
    try:
        with urllib.request.urlopen(req, timeout=DEEPGRAM_TIMEOUT_SECONDS) as resp:
            while True:
                chunk = resp.read(_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                try:
                    player.stdin.write(chunk)
                    player.stdin.flush()
                except (BrokenPipeError, OSError) as e:
                    log.warning("ffplay stdin closed mid-stream: %s", e)
                    pipe_broken = True
                    break
    except urllib.error.HTTPError as e:
        log.warning("deepgram-stream returned %d: %s", e.code, e.reason)
        return "skip"
    except urllib.error.URLError as e:
        log.warning("deepgram-stream network error: %s", e.reason)
        return "skip"
    except Exception as e:
        log.warning("deepgram-stream unexpected error: %s", e)
        return "skip"

    return "broken_pipe" if pipe_broken else "ok"


def synthesize_and_play_session(
    get_next_text: Callable[[], "str | None"],
    *,
    on_player_pid: Callable[[int], None],
) -> bool:
    """Stream multiple text chunks through one ffplay process for gapless playback.

    Tries the streaming path first. If ffplay/streaming is unavailable, falls
    back to per-chunk file-based playback (slower, but at least audible).
    """
    log = get_logger()
    from scripts.config import load as load_config
    cfg = load_config()
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    # Streaming path: one ffplay for all chunks.
    if cfg.primary_tts == "deepgram" and api_key:
        # We need to peek-and-replay if streaming bails before consuming any
        # chunks. Use a small lookahead buffer.
        first_holder: list["str | None"] = []
        called = {"n": 0}

        def peek_get_next() -> "str | None":
            if first_holder:
                return first_holder.pop(0)
            return get_next_text()

        ok = _stream_chunks_through_ffplay(
            get_next_text=peek_get_next,
            voice=cfg.voice,
            api_key=api_key,
            speech_rate=cfg.speech_rate,
            max_chars=cfg.max_deepgram_chars,
            on_player_pid=on_player_pid,
        )
        if ok:
            return True
        log.info("streaming TTS unavailable; falling back per-chunk")

    # Fallback: pull chunks one by one and play each via the file-based chain.
    any_played = False
    while True:
        text = get_next_text()
        if text is None:
            return any_played
        if synthesize_and_play(text, on_player_pid=on_player_pid):
            any_played = True


def synthesize_and_play(
    text: str,
    *,
    on_player_pid: Callable[[int], None],
) -> bool:
    """Synthesize `text` and play it. Blocks until audio finishes.

    Tries (in order):
      1. Deepgram → ffplay streaming (low time-to-first-audio), only when
         primary_tts is "deepgram" and a key is configured.
      2. The file-based backend chain from `synthesize()` (deepgram, piper,
         or say depending on primary/fallback config) → afplay.

    Returns True if anything actually played, False otherwise. Never raises.
    `on_player_pid(pid)` is called whenever a playback subprocess is spawned,
    so the caller can record it for `clear_and_kill`.
    """
    log = get_logger()
    from scripts.config import load as load_config
    cfg = load_config()
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    # Path 1: streaming Deepgram → ffplay (low TTFB).
    if cfg.primary_tts == "deepgram" and api_key:
        ok = _stream_deepgram_to_ffplay(
            text=text,
            voice=cfg.voice,
            api_key=api_key,
            speech_rate=cfg.speech_rate,
            max_chars=cfg.max_deepgram_chars,
            on_player_pid=on_player_pid,
        )
        if ok:
            return True
        log.info("streaming TTS unavailable; falling back to file-based synth")

    # Path 2: file-based chain (deepgram → piper → say, per cfg).
    path = synthesize(text)
    if path is None:
        return False

    return _play_file_with_afplay(path, on_player_pid=on_player_pid)


def _play_file_with_afplay(
    path: Path,
    *,
    on_player_pid: Callable[[int], None],
) -> bool:
    """Block on `afplay path`. Returns True if afplay exited 0."""
    log = get_logger()
    try:
        proc = subprocess.Popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        log.warning("afplay missing; cannot play %s", path)
        return False
    except Exception as e:
        log.warning("afplay spawn failed: %s", e)
        return False

    try:
        on_player_pid(proc.pid)
    except Exception as e:
        log.warning("on_player_pid callback raised: %s", e)

    try:
        rc = proc.wait(timeout=_AFPLAY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.warning("afplay timed out on %s", path)
        _kill_quietly(proc)
        return False
    except Exception as e:
        log.warning("afplay wait error on %s: %s", path, e)
        return False
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    return rc == 0
