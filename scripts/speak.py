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
from scripts.config import load as load_config, plugin_root
from scripts.log import get_logger
from scripts.transcript import last_assistant_text


def _mode_b_narration_instructions() -> str:
    """Instructions Claude needs to narrate via the speak CLI in mode B."""
    cli = plugin_root() / "scripts" / "speak_cli.py"
    return (
        "Claude Voice mode B is active. Before issuing a tool call that may "
        "take more than a moment (web fetches, searches, file reads on "
        "unfamiliar code, MCP calls, etc.), narrate a short cue out loud by "
        "running this Bash command IMMEDIATELY before the tool call:\n\n"
        f"    python3 \"{cli}\" \"<short cue>\"\n\n"
        "The cue should be 2-6 conversational words like \"Looking that up\", "
        "\"Pulling it up\", \"Checking now\", \"On it\". Keep it varied and "
        "natural. Only narrate when the wait would be noticeable; don't "
        "narrate trivial reads or quick edits. The CLI returns immediately; "
        "audio plays in the background and won't slow you down. Final-response "
        "speech at end of turn is automatic — you don't need to call the CLI "
        "for that."
    )


def _handle_stop(payload: dict) -> None:
    cfg = load_config()
    log = get_logger()
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        log.info("Stop: no assistant message in transcript; nothing to speak")
        return
    _msg_id, full_text = res

    stripped = extract.strip_for_voice(full_text, min_words=cfg.min_words)
    if not stripped:
        log.info("Stop: stripped text empty; full_text=%r", full_text[:80])
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


def _handle_user_prompt_submit(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    playback.clear_and_kill(session_id)


def _handle_session_start(payload: dict) -> None:
    state_mod.clean_stale(max_age_seconds=24 * 3600)


def _handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    state_mod.remove(session_id)
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


_DISPATCH = {
    "Stop": _handle_stop,
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


def emit_session_start_context_if_applicable(payload: dict) -> bool:
    """If payload is SessionStart and config is mode B, write the narration-
    instruction JSON to stdout. Returns True if anything was written."""
    if payload.get("hook_event_name") != "SessionStart":
        return False
    try:
        cfg = load_config()
    except Exception as e:
        get_logger().warning("speak: SessionStart config load failed: %s", e)
        return False
    if not (cfg.enabled and cfg.mode == "B"):
        return False
    try:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _mode_b_narration_instructions(),
            }
        }))
        sys.stdout.flush()
        return True
    except Exception as e:
        get_logger().warning("speak: SessionStart additionalContext emit failed: %s", e)
        return False


def main() -> int:
    """Hook entrypoint.

    By default we hand the slow work (TTS, Haiku rewrite, audio enqueue) to a
    detached background worker so the hook returns to Claude Code in <100ms
    rather than blocking the session for several seconds. The worker is the
    same script reinvoked with --worker; it reads the original payload on stdin.

    Special case: on SessionStart in mode B, the parent process emits an
    `additionalContext` JSON to stdout (synchronously, since detached workers
    can't talk back to Claude Code) instructing the assistant how to narrate
    via the speak CLI. The worker still runs for state cleanup.

    A hook can opt out of detachment with --inline (used by tests).
    """
    payload_bytes = sys.stdin.buffer.read()
    payload_text = payload_bytes.decode("utf-8", errors="replace")

    if "--worker" in sys.argv or "--inline" in sys.argv:
        return run(payload_text)

    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        payload = {}

    emit_session_start_context_if_applicable(payload)

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
        get_logger().warning("speak: worker spawn failed (%s); processing inline", e)
        return run(payload_text)


if __name__ == "__main__":
    sys.exit(main())
