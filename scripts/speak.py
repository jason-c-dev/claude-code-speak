"""Hook entrypoint. Reads hook event JSON on stdin, dispatches by event."""
from __future__ import annotations
import sys
from pathlib import Path

# Bootstrap: when invoked directly by Claude Code as a hook script, the
# plugin root isn't on sys.path. Inject it so `from scripts import ...` works.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import json
from scripts import extract, playback, state as state_mod, tts
from scripts.config import load as load_config
from scripts.log import get_logger
from scripts.transcript import last_assistant_text


def _handle_stop(payload: dict) -> None:
    cfg = load_config()
    log = get_logger()
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        log.info("Stop: no assistant message in transcript; nothing to speak")
        return
    msg_id, full_text = res

    # In mode B, Pre/PostToolUse may already have spoken parts of this message.
    # Speak only the prose past the recorded offset so we don't repeat ourselves.
    # In modes A and C, no Pre/Post hooks fire, so the offset stays at 0 and we
    # speak the whole message.
    s = state_mod.load(session_id)
    offset = s.spoken_offsets.get(msg_id, 0)
    text_to_speak = full_text[offset:] if offset < len(full_text) else ""

    if not text_to_speak.strip():
        log.info("Stop: nothing new past offset %d; skipping", offset)
        return

    stripped = extract.strip_for_voice(text_to_speak, min_words=cfg.min_words)
    if not stripped:
        log.info("Stop: stripped text empty; text_to_speak=%r", text_to_speak[:80])
        return

    log.info("Stop: speaking %d chars: %r", len(stripped), stripped[:120])

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        log.info("Stop: voicify returned empty; skipping")
        return

    audio = tts.synthesize(voiced)
    if audio is None:
        log.warning("Stop: TTS produced no audio; skipping")
        return

    playback.enqueue(session_id, audio)

    # Record that we've spoken through the end of this message.
    s = state_mod.load(session_id)
    s.spoken_offsets[msg_id] = len(full_text)
    state_mod.save(s)


def _handle_user_prompt_submit(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    playback.clear_and_kill(session_id)


def _handle_session_start(payload: dict) -> None:
    state_mod.clean_stale(max_age_seconds=24 * 3600)


def _handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    # Drop this session's state file.
    state_mod.remove(session_id)
    # Sweep tmp audio. (Cheap; tmp is small.)
    from scripts.log import voice_home
    tmp = voice_home() / "tmp"
    if tmp.exists():
        for f in tmp.iterdir():
            try:
                f.unlink()
            except Exception:
                pass


def _handle_notification(payload: dict) -> None:
    cfg = load_config()
    if cfg.mode != "C":
        return
    msg = payload.get("message") or ""
    if not msg.strip():
        return
    voiced = extract.voicify(
        msg,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        return
    audio = tts.synthesize(voiced)
    if audio is None:
        return
    session_id = payload.get("session_id") or "default"
    playback.enqueue(session_id, audio)


def _handle_pre_or_post_tool(payload: dict) -> None:
    log = get_logger()
    cfg = load_config()
    event = payload.get("hook_event_name") or "?"
    if cfg.mode != "B":
        log.info("%s: mode is %s, skipping (only mode B speaks here)", event, cfg.mode)
        return
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        log.info("%s: no assistant message in transcript yet", event)
        return
    msg_id, _full_text = res

    s = state_mod.load(session_id)
    offset = s.spoken_offsets.get(msg_id, 0)

    from scripts.transcript import current_assistant_text_after
    new_text = current_assistant_text_after(transcript, msg_id, offset)
    if not new_text:
        log.info("%s: nothing new past offset %d", event, offset)
        return

    stripped = extract.strip_for_voice(new_text, min_words=cfg.min_words)
    if not stripped:
        log.info("%s: stripped text empty; new_text=%r", event, new_text[:80])
        return

    log.info("%s: speaking %d chars: %r", event, len(stripped), stripped[:120])

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        log.info("%s: voicify returned empty; skipping", event)
        return

    audio = tts.synthesize(voiced)
    if audio is None:
        log.warning("%s: TTS produced no audio", event)
        return

    playback.enqueue(session_id, audio)
    # Update spoken offset to end of full text.
    s = state_mod.load(session_id)
    res2 = last_assistant_text(transcript)
    if res2 is not None and res2[0] == msg_id:
        s.spoken_offsets[msg_id] = len(res2[1])
        state_mod.save(s)


_DISPATCH = {
    "Stop": _handle_stop,
    "PreToolUse": _handle_pre_or_post_tool,
    "PostToolUse": _handle_pre_or_post_tool,
    "Notification": _handle_notification,
    "UserPromptSubmit": _handle_user_prompt_submit,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
}


def run(stdin_text: str) -> int:
    log = get_logger()
    cfg = load_config()
    if not cfg.enabled:
        return 0

    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError as e:
        log.warning("speak: invalid hook payload: %s", e)
        return 0

    event = payload.get("hook_event_name") or ""
    handler = _DISPATCH.get(event)
    if handler is None:
        log.info("speak: no handler for event %r; skipping", event)
        return 0

    try:
        handler(payload)
    except Exception as e:
        log.warning("speak: handler %r raised: %s", event, e)
    return 0


def main() -> int:
    """Hook entrypoint.

    By default we hand the slow work (TTS, Haiku rewrite, audio enqueue) to a
    detached background worker so the hook returns to Claude Code in <100ms
    rather than blocking the session for several seconds. The worker is the
    same script reinvoked with --worker; it reads the original payload on stdin.

    A hook can opt out of detachment with --inline (used by tests).
    """
    payload_bytes = sys.stdin.buffer.read()

    if "--worker" in sys.argv or "--inline" in sys.argv:
        return run(payload_bytes.decode("utf-8", errors="replace"))

    # Detach to a background worker so the hook returns fast.
    import subprocess
    try:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(payload_bytes)
            proc.stdin.close()
        return 0
    except Exception as e:
        # If we can't spawn a worker, fall back to inline processing.
        get_logger().warning("speak: worker spawn failed (%s); processing inline", e)
        return run(payload_bytes.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    sys.exit(main())
