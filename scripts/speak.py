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


_DISPATCH = {
    "Stop": _handle_stop,
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
