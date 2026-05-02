"""Hook entrypoint. Reads hook event JSON on stdin, dispatches by event."""
from __future__ import annotations
import json
import sys
from pathlib import Path

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
    msg_id, text = res

    stripped = extract.strip_for_voice(text, min_words=cfg.min_words)
    if not stripped:
        log.info("Stop: stripped text empty; skipping")
        return

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
    s.spoken_offsets[msg_id] = len(text)
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
    cfg = load_config()
    if cfg.mode != "B":
        return
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        return
    msg_id, _full_text = res

    s = state_mod.load(session_id)
    offset = s.spoken_offsets.get(msg_id, 0)

    from scripts.transcript import current_assistant_text_after
    new_text = current_assistant_text_after(transcript, msg_id, offset)
    if not new_text:
        return

    stripped = extract.strip_for_voice(new_text, min_words=cfg.min_words)
    if not stripped:
        return

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        return

    audio = tts.synthesize(voiced)
    if audio is None:
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
    return run(sys.stdin.read())


if __name__ == "__main__":
    sys.exit(main())
